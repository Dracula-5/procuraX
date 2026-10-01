import uuid
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, SoftDeleteMixin, TenantMixin, TimestampMixin, UUIDPkMixin
from app.domain.catalog import ContractStatus, VendorRiskLevel, VendorStatus


def _in(column: str, values: Iterable[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class Vendor(UUIDPkMixin, TimestampMixin, SoftDeleteMixin, TenantMixin, Base):
    __tablename__ = "vendors"
    # Status vocabularies are CHECK constraints rather than PostgreSQL ENUM types:
    # adding a value is a one-line migration and never needs a table rewrite.
    __table_args__ = (
        CheckConstraint(_in("status", VendorStatus), name="status"),
        CheckConstraint(_in("risk_level", VendorRiskLevel), name="risk_level"),
        CheckConstraint(_in("contract_status", ContractStatus), name="contract_status"),
        CheckConstraint("rating IS NULL OR (rating >= 0 AND rating <= 5)", name="rating_range"),
        CheckConstraint(
            "invoice_registration_number IS NULL OR invoice_registration_number ~ '^T[0-9]{13}$'",
            name="invoice_registration_number_format",
        ),
        Index("ix_vendors_org_status", "org_id", "status"),
        # "Which approved vendors supply networking hardware?" → array containment (@>).
        Index("ix_vendors_categories", "categories", postgresql_using="gin"),
        # Case-insensitive unique name per tenant, ignoring soft-deleted vendors.
        Index(
            "uq_vendors_org_lower_name",
            "org_id",
            func.lower(text("name")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str | None] = mapped_column(String(200))
    # Japan: 13-digit corporate number (法人番号) and Qualified Invoice System
    # registration number (適格請求書発行事業者登録番号, "T" + 13 digits).
    corporate_number: Mapped[str | None] = mapped_column(String(13))
    invoice_registration_number: Mapped[str | None] = mapped_column(String(14))
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    status: Mapped[str] = mapped_column(String(20), default=VendorStatus.PENDING_REVIEW.value)
    risk_level: Mapped[str] = mapped_column(String(10), default=VendorRiskLevel.UNKNOWN.value)
    contract_status: Mapped[str] = mapped_column(String(10), default=ContractStatus.NONE.value)
    contract_expires_on: Mapped[date | None] = mapped_column(Date)
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(40))
    country: Mapped[str] = mapped_column(String(2), default="JP")
    currency: Mapped[str] = mapped_column(String(3), default="JPY")
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    website: Mapped[str | None] = mapped_column(String(300))
    # Domestic transfer details for Zengin export, validated and stored in bank character form.
    bank_account: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_changed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", use_alter=True, name="fk_vendors_status_changed_by_id_users")
    )
