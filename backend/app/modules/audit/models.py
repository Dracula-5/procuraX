import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPkMixin, utcnow


class AuditLog(UUIDPkMixin, Base):
    """Append-only audit trail.

    Immutability is enforced by the database, not by convention: the runtime role is
    granted INSERT and SELECT only on this table (see migration 0001).
    Actor e-mail is snapshotted so entries remain meaningful if a user is later removed.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_org_entity", "org_id", "entity_type", "entity_id"),
        Index("ix_audit_logs_org_created", "org_id", "created_at"),
        Index("ix_audit_logs_org_action", "org_id", "action"),
    )

    # Strictly increasing insertion order (timestamps can tie within one transaction).
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_email: Mapped[str | None] = mapped_column(String(320))
    actor_type: Mapped[str] = mapped_column(String(20), default="user")  # user | system
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    summary: Mapped[str] = mapped_column(Text)
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
