"""Auditable delegation records for named approval steps."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantMixin, TimestampMixin, UUIDPkMixin


class ApprovalDelegation(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "approval_delegations"
    __table_args__ = (UniqueConstraint("org_id", "approval_id"),)

    approval_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approvals.id"), index=True)
    delegator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    delegate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    delegated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
