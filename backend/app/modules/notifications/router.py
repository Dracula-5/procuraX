"""In-app notifications, personal preferences, tenant webhook channels and dead-letter review."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update

from app.api.deps import DB, CurrentUser, requires
from app.core import crypto
from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import BusinessRuleViolation, NotFoundError
from app.core.principal import Principal
from app.domain.notifications import EventType, webhook_host
from app.domain.rbac import Permission
from app.modules.audit import service as audit
from app.modules.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
)

router = APIRouter(tags=["notifications"])
OrgAdmin = Annotated[Principal, Depends(requires(Permission.ORG_MANAGE))]
EVENT_TYPES = [e.value for e in EventType]


class NotificationOut(BaseModel):
    id: uuid.UUID
    event_type: str
    payload: dict
    link: str | None
    read_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    unread_count: int


class PreferencesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_enabled: bool
    muted_events: list[str] = Field(default_factory=list, max_length=len(EVENT_TYPES))

    @field_validator("muted_events")
    @classmethod
    def _known_events(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(EVENT_TYPES))
        if unknown:
            raise ValueError(f"Unknown event types: {', '.join(unknown)}")
        return sorted(set(value))


class PreferencesOut(PreferencesIn):
    email_available: bool  # whether this deployment sends e-mail at all
    event_types: list[str]


class ChannelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Required when the channel is first configured; omit to keep the stored URL.
    webhook_url: str | None = Field(default=None, max_length=2000)
    events: list[str] = Field(default_factory=list, max_length=len(EVENT_TYPES))
    enabled: bool = True

    @field_validator("events")
    @classmethod
    def _known_events(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(EVENT_TYPES))
        if unknown:
            raise ValueError(f"Unknown event types: {', '.join(unknown)}")
        return sorted(set(value))


class ChannelOut(BaseModel):
    kind: str
    configured: bool
    webhook_host: str | None  # the URL itself is a secret and is never returned
    events: list[str]
    enabled: bool


class ChannelsOut(BaseModel):
    webhooks_available: bool
    allowed_hosts: list[str]
    channels: list[ChannelOut]


class DeliveryOut(BaseModel):
    id: uuid.UUID
    event_type: str
    channel: str
    status: str
    attempts: int
    last_error: str | None
    next_attempt_at: datetime
    created_at: datetime


# --- Personal --------------------------------------------------------------------------------------


@router.get("/notifications", response_model=NotificationPage)
async def list_notifications(
    session: DB,
    principal: CurrentUser,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> NotificationPage:
    mine = (Notification.org_id == principal.org_id, Notification.user_id == principal.user_id)
    query = select(Notification).where(*mine)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    rows = await session.scalars(query.order_by(Notification.created_at.desc()).limit(limit))
    unread = await session.scalar(select(func.count()).where(*mine, Notification.read_at.is_(None)))
    return NotificationPage(
        items=[
            NotificationOut(
                id=n.id,
                event_type=n.event_type,
                payload=n.payload,
                link=n.link,
                read_at=n.read_at,
                created_at=n.created_at,
            )
            for n in rows
        ],
        unread_count=unread or 0,
    )


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(notification_id: uuid.UUID, session: DB, principal: CurrentUser) -> None:
    result = await session.execute(
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.org_id == principal.org_id,
            Notification.user_id == principal.user_id,
        )
        .values(read_at=func.coalesce(Notification.read_at, utcnow()))
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise NotFoundError("Notification not found")
    await session.commit()


@router.post("/notifications/read-all")
async def mark_all_read(session: DB, principal: CurrentUser) -> dict[str, int]:
    result = await session.execute(
        update(Notification)
        .where(
            Notification.org_id == principal.org_id,
            Notification.user_id == principal.user_id,
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )
    await session.commit()
    return {"updated": result.rowcount}  # type: ignore[attr-defined]


@router.get("/notifications/preferences", response_model=PreferencesOut)
async def get_preferences(session: DB, principal: CurrentUser) -> PreferencesOut:
    pref = await session.get(NotificationPreference, principal.user_id)
    return PreferencesOut(
        email_enabled=pref.email_enabled if pref else True,
        muted_events=sorted(pref.muted_events) if pref else [],
        email_available=get_settings().email_enabled,
        event_types=EVENT_TYPES,
    )


@router.put("/notifications/preferences", response_model=PreferencesOut)
async def set_preferences(data: PreferencesIn, session: DB, principal: CurrentUser) -> PreferencesOut:
    pref = await session.get(NotificationPreference, principal.user_id)
    if pref is None:
        pref = NotificationPreference(org_id=principal.org_id, user_id=principal.user_id)
        session.add(pref)
    pref.email_enabled = data.email_enabled
    pref.muted_events = data.muted_events
    pref.updated_at = utcnow()
    await session.commit()
    return PreferencesOut(
        **data.model_dump(), email_available=get_settings().email_enabled, event_types=EVENT_TYPES
    )


# --- Tenant channels (Slack / Teams) ----------------------------------------------------------------


@router.get("/notification-channels", response_model=ChannelsOut)
async def list_channels(session: DB, principal: OrgAdmin) -> ChannelsOut:
    rows = {
        c.kind: c
        for c in await session.scalars(
            select(NotificationChannel).where(NotificationChannel.org_id == principal.org_id)
        )
    }
    settings = get_settings()
    return ChannelsOut(
        webhooks_available=settings.webhooks_enabled,
        allowed_hosts=settings.webhook_allowed_hosts,
        channels=[
            ChannelOut(
                kind=kind,
                configured=kind in rows,
                webhook_host=rows[kind].webhook_host if kind in rows else None,
                events=sorted(rows[kind].events) if kind in rows else [],
                enabled=rows[kind].enabled if kind in rows else False,
            )
            for kind in ("slack", "teams")
        ],
    )


@router.put("/notification-channels/{kind}", response_model=ChannelOut)
async def set_channel(
    kind: Literal["slack", "teams"], data: ChannelIn, session: DB, principal: OrgAdmin
) -> ChannelOut:
    channel = await session.scalar(
        select(NotificationChannel)
        .where(NotificationChannel.org_id == principal.org_id, NotificationChannel.kind == kind)
        .with_for_update()
    )
    host = None
    if data.webhook_url is not None:
        try:
            host = webhook_host(data.webhook_url, get_settings().webhook_allowed_hosts)
        except ValueError as exc:
            raise BusinessRuleViolation(str(exc), details={"reason": "webhook_url_not_allowed"}) from exc
    if channel is None:
        if host is None or data.webhook_url is None:
            raise BusinessRuleViolation(
                "A webhook URL is required to configure this channel",
                details={"reason": "webhook_url_required"},
            )
        channel = NotificationChannel(org_id=principal.org_id, kind=kind)
        session.add(channel)
    if host is not None and data.webhook_url is not None:
        channel.webhook_url_encrypted = crypto.encrypt(data.webhook_url.strip())
        channel.webhook_host = host
    channel.events = data.events
    channel.enabled = data.enabled
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="notification_channel.updated",
        entity_type="organization",
        entity_id=principal.org_id,
        summary=f"{kind.title()} notifications {'enabled' if data.enabled else 'disabled'} ({channel.webhook_host})",
        changes={
            "kind": kind,
            "host": channel.webhook_host,
            "events": data.events,
            "url_changed": host is not None,
        },
    )
    await session.commit()
    return ChannelOut(
        kind=kind,
        configured=True,
        webhook_host=channel.webhook_host,
        events=channel.events,
        enabled=channel.enabled,
    )


@router.delete("/notification-channels/{kind}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(kind: Literal["slack", "teams"], session: DB, principal: OrgAdmin) -> None:
    channel = await session.scalar(
        select(NotificationChannel).where(
            NotificationChannel.org_id == principal.org_id, NotificationChannel.kind == kind
        )
    )
    if channel is None:
        raise NotFoundError("This channel is not configured")
    await session.delete(channel)
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="notification_channel.removed",
        entity_type="organization",
        entity_id=principal.org_id,
        summary=f"{kind.title()} notifications removed",
        changes={"kind": kind, "host": channel.webhook_host},
    )
    await session.commit()


# --- Dead letters ----------------------------------------------------------------------------------


@router.get("/notification-deliveries", response_model=list[DeliveryOut])
async def list_deliveries(
    session: DB,
    principal: OrgAdmin,
    delivery_status: Annotated[Literal["pending", "sent", "dead"], Query(alias="status")] = "dead",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[DeliveryOut]:
    rows = await session.scalars(
        select(NotificationDelivery)
        .where(
            NotificationDelivery.org_id == principal.org_id, NotificationDelivery.status == delivery_status
        )
        .order_by(NotificationDelivery.created_at.desc())
        .limit(limit)
    )
    return [
        DeliveryOut(
            id=d.id,
            event_type=d.event_type,
            channel=d.channel,
            status=d.status,
            attempts=d.attempts,
            last_error=d.last_error,
            next_attempt_at=d.next_attempt_at,
            created_at=d.created_at,
        )
        for d in rows
    ]


@router.post("/notification-deliveries/{delivery_id}/retry", response_model=DeliveryOut)
async def retry_delivery(delivery_id: uuid.UUID, session: DB, principal: OrgAdmin) -> DeliveryOut:
    """Send a dead-lettered delivery again (e.g. after fixing the SMTP or webhook settings)."""
    delivery = await session.scalar(
        select(NotificationDelivery)
        .where(NotificationDelivery.id == delivery_id, NotificationDelivery.org_id == principal.org_id)
        .with_for_update()
    )
    if delivery is None:
        raise NotFoundError("Delivery not found")
    if delivery.status != "dead":
        raise BusinessRuleViolation(
            "Only dead-lettered deliveries can be retried", details={"reason": "delivery_not_dead"}
        )
    delivery.status = "pending"
    delivery.attempts = 0
    delivery.next_attempt_at = utcnow()
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="notification_delivery.retried",
        entity_type="organization",
        entity_id=principal.org_id,
        summary=f"Retried a dead-lettered {delivery.channel} notification",
        changes={"delivery_id": str(delivery.id), "last_error": delivery.last_error},
    )
    await session.commit()
    return DeliveryOut(
        id=delivery.id,
        event_type=delivery.event_type,
        channel=delivery.channel,
        status=delivery.status,
        attempts=delivery.attempts,
        last_error=delivery.last_error,
        next_attempt_at=delivery.next_attempt_at,
        created_at=delivery.created_at,
    )
