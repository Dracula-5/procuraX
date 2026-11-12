"""Transactional outbox, deliveries, in-app notifications and notification settings.

`notification_outbox` rows are written in the same transaction as the business change they describe,
so an event exists if and only if the change committed. The worker fans each event out into
`notifications` (in-app) and `notification_deliveries` (e-mail, Slack, Teams), then delivers those
with retries. Every table is tenant-scoped with forced RLS.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantMixin, TimestampMixin, UUIDPkMixin, utcnow
from app.domain.notifications import Channel


def _in(column: str, values: list[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


OUTBOX_STATUSES = ["pending", "done", "dead"]
DELIVERY_STATUSES = ["pending", "sent", "dead"]
EXTERNAL_CHANNELS = [Channel.EMAIL.value, Channel.SLACK.value, Channel.TEAMS.value]
WEBHOOK_KINDS = [Channel.SLACK.value, Channel.TEAMS.value]


class OutboxEvent(UUIDPkMixin, TenantMixin, Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        CheckConstraint(_in("status", OUTBOX_STATUSES), name="valid_status"),
        # The worker's claim query: pending rows that are due, oldest first.
        Index("ix_notification_outbox_due", "status", "next_attempt_at"),
    )

    event_type: Mapped[str] = mapped_column(String(60))
    aggregate_type: Mapped[str] = mapped_column(String(40))
    aggregate_id: Mapped[uuid.UUID] = mapped_column()
    # Who caused the event (never notified about their own action); NULL for system events.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # Facts for rendering and recipients ({"recipient_user_ids": [...], "recipient_roles": [...], ...}).
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDelivery(UUIDPkMixin, TenantMixin, Base):
    """One external message (e-mail to a user, or a post to a tenant's Slack/Teams webhook)."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        CheckConstraint(_in("status", DELIVERY_STATUSES), name="valid_status"),
        CheckConstraint(_in("channel", EXTERNAL_CHANNELS), name="valid_channel"),
        # Idempotency: one delivery per (event, channel, recipient), whatever retries or replays happen.
        UniqueConstraint("delivery_key"),
        Index("ix_notification_deliveries_due", "status", "next_attempt_at"),
    )

    outbox_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notification_outbox.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    channel: Mapped[str] = mapped_column(String(16))
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    address: Mapped[str | None] = mapped_column(String(320))
    locale: Mapped[str] = mapped_column(String(5), default="en")
    delivery_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(UUIDPkMixin, TenantMixin, Base):
    """In-app notification. The SPA renders the text from event_type + payload in the user's language."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("delivery_key"),
        Index("ix_notifications_user_created", "org_id", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSONB)
    link: Mapped[str | None] = mapped_column(String(300))
    delivery_key: Mapped[str] = mapped_column(String(200))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationPreference(TenantMixin, Base):
    """A user's choices. No row means the defaults: in-app for everything, e-mail on."""

    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    muted_events: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NotificationChannel(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    """A tenant's Slack or Teams incoming webhook. The URL is a secret: stored encrypted, never returned."""

    __tablename__ = "notification_channels"
    __table_args__ = (
        CheckConstraint(_in("kind", WEBHOOK_KINDS), name="valid_kind"),
        UniqueConstraint("org_id", "kind"),
    )

    kind: Mapped[str] = mapped_column(String(16))
    webhook_url_encrypted: Mapped[str] = mapped_column(Text)
    webhook_host: Mapped[str] = mapped_column(String(255))  # shown to admins instead of the URL
    events: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
