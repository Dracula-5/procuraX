import uuid
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TenantMixin, TimestampMixin, UUIDPkMixin, utcnow
from app.domain.catalog import SpendCategory
from app.domain.workflow.purchase_request import ApprovalStepStatus, PRStatus


def _in(column: str, values: Iterable[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class PurchaseRequest(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "purchase_requests"
    __table_args__ = (
        UniqueConstraint("org_id", "number"),
        CheckConstraint(_in("status", PRStatus), name="status"),
        CheckConstraint(_in("category", SpendCategory), name="category"),
        CheckConstraint("estimated_total >= 0", name="total_non_negative"),
        Index("ix_purchase_requests_org_status", "org_id", "status"),
        Index("ix_purchase_requests_org_requester", "org_id", "requester_id", "created_at"),
        Index("ix_purchase_requests_org_department", "org_id", "department_id", "status"),
        Index("ix_purchase_requests_org_created", "org_id", "created_at"),
    )

    number: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200))
    justification: Mapped[str] = mapped_column(Text, default="")
    requester_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"))
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cost_centers.id"))
    category: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(3))
    estimated_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    required_by: Mapped[date | None] = mapped_column(Date)
    preferred_vendor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendors.id"))
    is_emergency: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default=PRStatus.DRAFT.value)
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    # Snapshot of the deterministic policy evaluation made at submission (explainability).
    policy_version: Mapped[int | None] = mapped_column(Integer)
    policy_evaluation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    submission_count: Mapped[int] = mapped_column(Integer, default=0)

    items: Mapped[list["PurchaseRequestItem"]] = relationship(
        back_populates="purchase_request",
        lazy="selectin",
        order_by="PurchaseRequestItem.line_no",
        cascade="all, delete-orphan",
    )
    approvals: Mapped[list["Approval"]] = relationship(
        back_populates="purchase_request",
        lazy="selectin",
        order_by="[Approval.round, Approval.sequence]",
        cascade="all, delete-orphan",
    )


class PurchaseRequestItem(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "purchase_request_items"
    __table_args__ = (
        UniqueConstraint("purchase_request_id", "line_no"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
    )

    purchase_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_requests.id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    uom: Mapped[str] = mapped_column(String(20), default="EA")
    line_total: Mapped[Decimal] = mapped_column(Numeric(18, 2))

    purchase_request: Mapped[PurchaseRequest] = relationship(back_populates="items")


class Approval(UUIDPkMixin, TimestampMixin, TenantMixin, Base):
    """One step of a request's approval chain. Steps run sequentially by `sequence`.

    `round` = the submission the step belongs to. A rejected-then-reopened request gets a
    fresh chain on resubmission; earlier rounds are kept as history.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("purchase_request_id", "round", "sequence"),
        CheckConstraint(_in("status", ApprovalStepStatus), name="status"),
        # Inbox queries: "pending steps for my role / assigned to me".
        Index("ix_approvals_inbox_role", "org_id", "status", "approver_role"),
        Index("ix_approvals_inbox_user", "org_id", "status", "assigned_user_id"),
        Index("ix_approvals_org_pending_due", "org_id", "status", "due_at"),
    )

    purchase_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_requests.id", ondelete="CASCADE"), index=True
    )
    round: Mapped[int] = mapped_column(Integer, default=1)
    sequence: Mapped[int] = mapped_column(Integer)
    approver_role: Mapped[str] = mapped_column(String(40))
    # NULL → any holder of approver_role (a pool) may act, except the requester.
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    rule_ids: Mapped[list[str]] = mapped_column(ARRAY(String(20)))
    reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default=ApprovalStepStatus.WAITING.value)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalation_count: Mapped[int] = mapped_column(Integer, default=0)
    last_escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    purchase_request: Mapped[PurchaseRequest] = relationship(back_populates="approvals")


class ApprovalPolicy(UUIDPkMixin, TenantMixin, Base):
    """Versioned approval policy. Publishing creates a new version; old ones are kept so
    past decisions stay explainable against the exact rules that produced them."""

    __tablename__ = "approval_policies"
    __table_args__ = (
        UniqueConstraint("org_id", "version"),
        Index(
            "uq_approval_policies_one_active",
            "org_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
