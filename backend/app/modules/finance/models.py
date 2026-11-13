"""Exchange rates, general-ledger account mapping and journal exports. All tenant-scoped with forced RLS."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantMixin, UUIDPkMixin, utcnow


class ExchangeRate(UUIDPkMixin, TenantMixin, Base):
    """1 unit of `currency` = `rate` units of the tenant's base currency, from `valid_on`."""

    __tablename__ = "exchange_rates"
    __table_args__ = (
        CheckConstraint("rate > 0", name="positive_rate"),
        UniqueConstraint("org_id", "currency", "valid_on", "source"),
        Index("ix_exchange_rates_lookup", "org_id", "currency", "valid_on"),
    )

    currency: Mapped[str] = mapped_column(String(3))
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    valid_on: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(40))  # "manual" or a provider name such as "ecb"
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GLAccountMapping(UUIDPkMixin, TenantMixin, Base):
    """Ledger accounts for journal export; the most specific row (category + cost centre) wins."""

    __tablename__ = "gl_account_mappings"
    # The default expense account lives in organizations.ledger_settings; a row here narrows it to a
    # category, a cost centre or both. NULLS NOT DISTINCT keeps (category, no cost centre) unique.
    __table_args__ = (
        CheckConstraint("category IS NOT NULL OR cost_center_id IS NOT NULL", name="has_scope"),
        UniqueConstraint("org_id", "category", "cost_center_id", postgresql_nulls_not_distinct=True),
    )

    category: Mapped[str | None] = mapped_column(String(40))
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cost_centers.id"))
    expense_account: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class JournalExport(UUIDPkMixin, TenantMixin, Base):
    """One export batch: which entries went out, in which format, and the file's hash."""

    __tablename__ = "journal_exports"

    reference: Mapped[str] = mapped_column(String(40))
    format: Mapped[str] = mapped_column(String(8))
    invoice_count: Mapped[int] = mapped_column(Integer)
    payment_count: Mapped[int] = mapped_column(Integer)
    entry_count: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    # The exported file itself (bounded by MAX_ENTRIES per batch), so it can be downloaded again.
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
