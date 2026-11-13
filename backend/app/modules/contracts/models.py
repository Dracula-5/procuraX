"""Supplier contracts (framework agreements). Tenant-scoped with forced RLS (migration 0017)."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantMixin, TimestampMixin, UUIDPkMixin
from app.domain.contracts import ContractState


class Contract(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "contracts"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(s.value) for s in ContractState)})", name="valid_status"
        ),
        CheckConstraint("ends_on >= starts_on", name="dates_ordered"),
        CheckConstraint("value_cap IS NULL OR value_cap > 0", name="positive_cap"),
        # The policy lookup: this vendor's contracts by status.
        Index("ix_contracts_org_vendor_status", "org_id", "vendor_id", "status"),
    )

    number: Mapped[str] = mapped_column(String(20))
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    # Spend categories covered; empty = every category bought from the vendor.
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    value_cap: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16), default=ContractState.DRAFT.value)
    terminated_reason: Mapped[str | None] = mapped_column(Text)
    # Signed contract file in object storage (same rules as invoice files; write-once key).
    document_object_key: Mapped[str | None] = mapped_column(String(300))
    document_sha256: Mapped[str | None] = mapped_column(String(64))
    document_filename: Mapped[str | None] = mapped_column(String(255))
    document_mime_type: Mapped[str | None] = mapped_column(String(60))
    # clean (scanned at upload) or not_scanned (ClamAV disabled); infected files are refused.
    document_scan_status: Mapped[str | None] = mapped_column(String(16))
    expiry_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
