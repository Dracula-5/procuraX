"""Purchase orders, receipts and invoices: tenant-owned operational records."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TenantMixin, TimestampMixin, UUIDPkMixin, utcnow


class PurchaseOrder(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("org_id", "number"),
        UniqueConstraint("org_id", "purchase_request_id"),
        CheckConstraint("total >= 0", name="total_non_negative"),
        CheckConstraint(
            "status IN ('approved', 'sent', 'acknowledged', 'partially_received', 'received', 'closed', 'cancelled')",
            name="valid_status",
        ),
        Index("ix_purchase_orders_org_status", "org_id", "status"),
    )

    number: Mapped[str] = mapped_column(String(30))
    purchase_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_requests.id"))
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"))
    status: Mapped[str] = mapped_column(String(24), default="approved")
    currency: Mapped[str] = mapped_column(String(3))
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    expected_delivery: Mapped[date | None] = mapped_column(Date)
    terms: Mapped[str] = mapped_column(Text, default="")
    awarded_quote_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendor_quotes.id"))
    items: Mapped[list["PurchaseOrderItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class PurchaseOrderItem(UUIDPkMixin, TenantMixin, Base):
    __tablename__ = "purchase_order_items"
    __table_args__ = (
        UniqueConstraint("purchase_order_id", "line_no"),
        CheckConstraint("quantity > 0 AND unit_price >= 0", name="valid_quantity_price"),
    )

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int] = mapped_column()
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 2))


class VendorQuote(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "vendor_quotes"
    __table_args__ = (
        CheckConstraint("amount >= 0 AND quality_score BETWEEN 0 AND 5", name="valid_quote_score"),
        Index("ix_vendor_quotes_org_request", "org_id", "purchase_request_id"),
        Index(
            "uq_vendor_quotes_awarded_per_request",
            "org_id",
            "purchase_request_id",
            unique=True,
            postgresql_where=text("is_awarded"),
        ),
    )

    purchase_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_requests.id"), index=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    delivery_days: Mapped[int] = mapped_column()
    quality_score: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=Decimal("3"))
    contract_compliant: Mapped[bool] = mapped_column(Boolean, default=False)
    quote_valid_until: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Optional supplier unit prices per request line: [{"line_no": 1, "unit_price": "1200.00"}].
    # When present, the PO uses them exactly instead of apportioning the quote total.
    line_prices: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    is_awarded: Mapped[bool] = mapped_column(Boolean, default=False)
    awarded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    awarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Human-in-the-loop record: the baseline recommendation shown at award time, whether the
    # human followed it, and the override reason when they did not.
    award_context: Mapped[dict | None] = mapped_column(JSONB)


class GoodsReceipt(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "goods_receipts"
    __table_args__ = (
        CheckConstraint(
            "quantity > 0 AND damaged_quantity >= 0 AND damaged_quantity <= quantity", name="valid_quantities"
        ),
        Index("ix_goods_receipts_org_po", "org_id", "purchase_order_id"),
        Index("ix_goods_receipts_org_po_item", "org_id", "purchase_order_id", "purchase_order_item_id"),
    )

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id"))
    purchase_order_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_order_items.id"))
    received_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    received_on: Mapped[date] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    damaged_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal("0"))
    comments: Mapped[str] = mapped_column(Text, default="")
    evidence_reference: Mapped[str | None] = mapped_column(String(500))


class Invoice(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint(
            "quantity > 0 AND total >= 0 AND subtotal >= 0 AND tax >= 0", name="valid_invoice_amounts"
        ),
        CheckConstraint(
            "match_status IN ('matched', 'partially_matched', 'mismatch', 'duplicate', 'requires_review', "
            "'exception_approved', 'exception_rejected')",
            name="valid_match_status",
        ),
        CheckConstraint(
            "registration_number IS NULL OR registration_number ~ '^T[0-9]{13}$'",
            name="registration_number_format",
        ),
        Index("ix_invoices_org_status", "org_id", "match_status"),
        Index(
            "uq_invoices_vendor_number_ci",
            "org_id",
            "vendor_id",
            func.lower(text("invoice_number")),
            unique=True,
        ),
    )

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id"))
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"))
    invoice_number: Mapped[str] = mapped_column(String(100))
    invoice_date: Mapped[date] = mapped_column(Date)
    # Qualified Invoice System registration number printed on the invoice (T + 13 digits).
    registration_number: Mapped[str | None] = mapped_column(String(14))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    tax: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    match_status: Mapped[str] = mapped_column(String(24), default="requires_review")
    match_details: Mapped[dict] = mapped_column(JSONB, default=dict)
    line_match_details: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_comment: Mapped[str | None] = mapped_column(Text)


class Payment(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("org_id", "invoice_id"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint(
            "status IN ('pending_approval', 'approved', 'rejected', 'exported')", name="valid_status"
        ),
        Index("ix_payments_org_status", "org_id", "status"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(24), default="pending_approval")
    submitted_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    # Bank transfer file export (ProcuraX produces the file; a person uploads it to the bank).
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exported_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    export_reference: Mapped[str | None] = mapped_column(String(40), index=True)
    transfer_record: Mapped[str | None] = mapped_column(String(120))
