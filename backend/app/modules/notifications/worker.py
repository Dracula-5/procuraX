"""Outbox worker: fan events out to recipients, then deliver them with retries.

PostgreSQL is the queue (ADR-013). Both stages claim rows with `FOR UPDATE SKIP LOCKED`, so any number
of worker processes or API replicas can run at once without double-processing; a crashed worker's
transaction rolls back and its rows become claimable again.

Stage 1 (fan-out): a pending outbox event becomes in-app notifications and external deliveries, keyed
by a deterministic delivery key with a unique constraint, so replays insert nothing twice.
Stage 2 (deliver): pending deliveries are sent (SMTP, Slack, Teams). Failures back off exponentially
and are dead-lettered after `notification_max_attempts`. Delivery is at-least-once: if a worker dies
after the receiver accepted a message but before commit, it is sent again. The delivery key travels
as the e-mail Message-ID and the webhook Idempotency-Key header so receivers can deduplicate.
"""

import asyncio
import logging
import smtplib
import uuid
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import Settings, get_settings
from app.core.db import system_session, utcnow
from app.domain.notifications import (
    Channel,
    Preferences,
    Recipient,
    TenantChannel,
    backoff,
    is_dead,
    plan_deliveries,
    render,
    webhook_host,
)
from app.modules.identity.models import Organization, User, UserRole
from app.modules.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
    OutboxEvent,
)

logger = logging.getLogger("procurax.notifications")


class EmailTransport(Protocol):
    async def send(self, *, to: str, subject: str, body: str, message_id: str) -> None: ...


class WebhookTransport(Protocol):
    async def post(self, *, url: str, payload: dict, idempotency_key: str) -> None: ...


class SmtpEmail:
    """Plain SMTP (Mailpit locally; any provider's SMTP relay in production). Runs in a thread."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _send_sync(self, message: EmailMessage) -> None:
        s = self.settings
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password or "")
            smtp.send_message(message)

    async def send(self, *, to: str, subject: str, body: str, message_id: str) -> None:
        message = EmailMessage()
        message["From"] = self.settings.email_from
        message["To"] = to
        message["Subject"] = subject
        # Stable per delivery, so a resent message can be recognised as the same one.
        message["Message-ID"] = make_msgid(idstring=message_id.replace(":", "."), domain="procurax.local")
        message.set_content(body)
        await asyncio.to_thread(self._send_sync, message)


class HttpWebhook:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def post(self, *, url: str, payload: dict, idempotency_key: str) -> None:
        async with httpx.AsyncClient(
            timeout=self.settings.webhook_timeout_seconds, follow_redirects=False
        ) as client:
            response = await client.post(url, json=payload, headers={"Idempotency-Key": idempotency_key})
        if response.status_code >= 300:
            raise RuntimeError(f"Webhook answered HTTP {response.status_code}")


@dataclass
class Transports:
    email: EmailTransport | None
    webhook: WebhookTransport | None


def default_transports(settings: Settings) -> Transports:
    return Transports(
        email=SmtpEmail(settings) if settings.email_enabled else None,
        webhook=HttpWebhook(settings) if settings.webhooks_enabled else None,
    )


def _error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:2000]


# --- Stage 1: fan-out ----------------------------------------------------------------------------


async def _recipients(session: AsyncSession, event: OutboxEvent) -> list[Recipient]:
    user_ids = {uuid.UUID(u) for u in event.payload.get("recipient_user_ids", [])}
    roles = event.payload.get("recipient_roles", [])
    if roles:
        user_ids |= set(
            await session.scalars(
                select(UserRole.user_id).where(UserRole.org_id == event.org_id, UserRole.role_key.in_(roles))
            )
        )
    if not user_ids:
        return []
    rows = await session.execute(
        select(User, NotificationPreference)
        .outerjoin(NotificationPreference, NotificationPreference.user_id == User.id)
        .where(
            User.org_id == event.org_id,  # the worker bypasses RLS, so tenant scoping is explicit
            User.id.in_(user_ids),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    )
    return [
        Recipient(
            user_id=str(user.id),
            email=user.email,
            locale=user.locale,
            preferences=Preferences(email=pref.email_enabled, muted_events=frozenset(pref.muted_events))
            if pref
            else Preferences(),
        )
        for user, pref in rows
    ]


async def _fan_out(session: AsyncSession, event: OutboxEvent, settings: Settings) -> int:
    org = await session.get(Organization, event.org_id)
    channels = await session.scalars(
        select(NotificationChannel).where(
            NotificationChannel.org_id == event.org_id, NotificationChannel.enabled
        )
    )
    plans = plan_deliveries(
        event_id=str(event.id),
        event_type=event.event_type,
        recipients=await _recipients(session, event),
        tenant_channels=[TenantChannel(Channel(c.kind), frozenset(c.events), c.enabled) for c in channels],
        actor_user_id=str(event.actor_user_id) if event.actor_user_id else None,
        email_available=settings.email_enabled,
        webhooks_available=settings.webhooks_enabled,
        tenant_locale="ja" if org is not None and org.country == "JP" else "en",
    )
    for plan in plans:
        if plan.channel == Channel.IN_APP:
            stmt = insert(Notification).values(
                id=uuid.uuid4(),
                org_id=event.org_id,
                user_id=uuid.UUID(plan.user_id) if plan.user_id else None,
                event_type=event.event_type,
                payload=event.payload,
                link=event.payload.get("link"),
                delivery_key=plan.delivery_key,
                created_at=utcnow(),
            )
            model: type[Notification] | type[NotificationDelivery] = Notification
        else:
            stmt = insert(NotificationDelivery).values(
                id=uuid.uuid4(),
                org_id=event.org_id,
                outbox_id=event.id,
                event_type=event.event_type,
                channel=plan.channel.value,
                recipient_user_id=uuid.UUID(plan.user_id) if plan.user_id else None,
                address=plan.address,
                locale=plan.locale,
                delivery_key=plan.delivery_key,
                status="pending",
                attempts=0,
                next_attempt_at=utcnow(),
                created_at=utcnow(),
            )
            model = NotificationDelivery
        # Idempotent: a replayed event (e.g. after a crash before commit) inserts nothing new.
        await session.execute(stmt.on_conflict_do_nothing(index_elements=[model.__table__.c.delivery_key]))
    return len(plans)


async def fan_out_batch(
    session: AsyncSession, settings: Settings, now: datetime | None = None
) -> dict[str, int]:
    now = now or utcnow()
    events = list(
        await session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.status == "pending", OutboxEvent.next_attempt_at <= now)
            .order_by(OutboxEvent.created_at)
            .limit(settings.notification_batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    counts = {"events": 0, "planned": 0, "event_failures": 0}
    for event in events:
        try:
            async with session.begin_nested():  # one bad event must not undo the rest of the batch
                counts["planned"] += await _fan_out(session, event, settings)
            event.status = "done"
            event.processed_at = now
            counts["events"] += 1
        except Exception as exc:  # noqa: BLE001 - any failure is recorded and retried
            event.attempts += 1
            event.last_error = _error_text(exc)
            if is_dead(event.attempts, settings.notification_max_attempts):
                event.status = "dead"
            else:
                event.next_attempt_at = now + backoff(event.attempts, key=str(event.id))
            counts["event_failures"] += 1
            logger.warning(
                "outbox_fan_out_failed", extra={"outbox_id": str(event.id), "error": event.last_error}
            )
    await session.commit()
    return counts


# --- Stage 2: deliver ----------------------------------------------------------------------------


def _webhook_payload(channel: str, subject: str, body: str, link: str | None) -> dict:
    if channel == Channel.SLACK:
        text = f"*{subject}*\n{body}" + (f"\n<{link}|Open in ProcuraX>" if link else "")
        return {"text": text}
    # Teams: an Adaptive Card message, accepted by Teams Workflows incoming webhooks.
    card_body: list[dict] = [
        {"type": "TextBlock", "text": subject, "weight": "Bolder", "wrap": True},
        {"type": "TextBlock", "text": body, "wrap": True},
    ]
    actions = [{"type": "Action.OpenUrl", "title": "ProcuraX", "url": link}] if link else []
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": card_body,
                    "actions": actions,
                },
            }
        ],
    }


async def _send(
    session: AsyncSession, delivery: NotificationDelivery, settings: Settings, transports: Transports
) -> None:
    event = await session.get(OutboxEvent, delivery.outbox_id)
    if event is None:
        raise RuntimeError("Outbox event missing")
    subject, body = render(delivery.event_type, event.payload, delivery.locale)
    path = event.payload.get("link")
    link = f"{settings.public_app_url}{path}" if path else None
    if delivery.channel == Channel.EMAIL:
        if transports.email is None:
            raise RuntimeError("E-mail is disabled (PROCURAX_EMAIL_ENABLED=false)")
        if not delivery.address:
            raise RuntimeError("No e-mail address")
        text = body + (f"\n\n{link}" if link else "")
        await transports.email.send(
            to=delivery.address, subject=subject, body=text, message_id=delivery.delivery_key
        )
        return
    if transports.webhook is None:
        raise RuntimeError("Webhooks are disabled (PROCURAX_WEBHOOKS_ENABLED=false)")
    channel = await session.scalar(
        select(NotificationChannel).where(
            NotificationChannel.org_id == delivery.org_id, NotificationChannel.kind == delivery.channel
        )
    )
    if channel is None or not channel.enabled:
        raise RuntimeError(f"The tenant's {delivery.channel} webhook is not configured")
    url = crypto.decrypt(channel.webhook_url_encrypted)
    webhook_host(url, settings.webhook_allowed_hosts)  # re-check: the allow-list may have changed
    await transports.webhook.post(
        url=url,
        payload=_webhook_payload(delivery.channel, subject, body, link),
        idempotency_key=delivery.delivery_key,
    )


async def deliver_batch(
    session: AsyncSession, settings: Settings, transports: Transports, now: datetime | None = None
) -> dict[str, int]:
    now = now or utcnow()
    deliveries = list(
        await session.scalars(
            select(NotificationDelivery)
            .where(NotificationDelivery.status == "pending", NotificationDelivery.next_attempt_at <= now)
            .order_by(NotificationDelivery.next_attempt_at)
            .limit(settings.notification_batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    counts = {"sent": 0, "retried": 0, "dead": 0}
    for delivery in deliveries:
        delivery.attempts += 1
        try:
            await _send(session, delivery, settings, transports)
            delivery.status = "sent"
            delivery.sent_at = now
            delivery.last_error = None
            counts["sent"] += 1
        except Exception as exc:  # noqa: BLE001 - transport errors are recorded and retried
            delivery.last_error = _error_text(exc)
            if is_dead(delivery.attempts, settings.notification_max_attempts):
                delivery.status = "dead"
                counts["dead"] += 1
                logger.error(
                    "notification_dead_lettered",
                    extra={
                        "delivery_id": str(delivery.id),
                        "channel": delivery.channel,
                        "error": delivery.last_error,
                    },
                )
            else:
                delivery.next_attempt_at = now + backoff(delivery.attempts, key=delivery.delivery_key)
                counts["retried"] += 1
    await session.commit()
    return counts


# --- Entry points ----------------------------------------------------------------------------------


async def process_once(
    settings: Settings | None = None, transports: Transports | None = None
) -> dict[str, int]:
    """One fan-out batch and one delivery batch, each in its own system-session transaction."""
    settings = settings or get_settings()
    transports = transports or default_transports(settings)
    session = system_session()
    try:
        fanned = await fan_out_batch(session, settings)
    finally:
        await session.close()
    session = system_session()
    try:
        delivered = await deliver_batch(session, settings, transports)
    finally:
        await session.close()
    return {**fanned, **delivered}
