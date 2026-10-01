import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.domain.catalog import SpendCategory
from app.domain.policy.models import PolicyConfig
from app.domain.workflow.purchase_request import PRStatus


class PRItemIn(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit_price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    uom: str = Field(default="EA", min_length=1, max_length=20)


class PRCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    category: SpendCategory
    cost_center_id: uuid.UUID | None = None
    justification: str = Field(default="", max_length=5000)
    required_by: date | None = None
    preferred_vendor_id: uuid.UUID | None = None
    is_emergency: bool = False
    items: list[PRItemIn] = Field(default_factory=list, max_length=100)


class PRUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    category: SpendCategory | None = None
    cost_center_id: uuid.UUID | None = None
    justification: str | None = Field(default=None, max_length=5000)
    required_by: date | None = None
    preferred_vendor_id: uuid.UUID | None = None
    is_emergency: bool | None = None
    items: list[PRItemIn] | None = Field(default=None, max_length=100)


class PRItemOut(BaseModel):
    id: uuid.UUID
    line_no: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    uom: str
    line_total: Decimal


class ApprovalStepOut(BaseModel):
    id: uuid.UUID
    round: int
    sequence: int
    approver_role: str
    assigned_user_id: uuid.UUID | None
    assigned_user_name: str | None
    rule_ids: list[str]
    reasons: list[str]
    status: str
    decided_by_id: uuid.UUID | None
    decided_by_name: str | None
    decided_at: datetime | None
    comment: str | None
    activated_at: datetime | None
    due_at: datetime | None
    is_overdue: bool
    escalation_count: int = 0
    last_escalated_at: datetime | None = None


class PRSummaryOut(BaseModel):
    id: uuid.UUID
    number: str
    title: str
    status: PRStatus
    category: SpendCategory
    currency: str
    estimated_total: Decimal
    requester_id: uuid.UUID
    requester_name: str | None
    department_id: uuid.UUID | None
    department_name: str | None
    cost_center_id: uuid.UUID | None
    cost_center_code: str | None
    preferred_vendor_id: uuid.UUID | None
    vendor_name: str | None
    is_emergency: bool
    required_by: date | None
    submitted_at: datetime | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PRDetailOut(PRSummaryOut):
    justification: str
    fiscal_year: int | None
    policy_version: int | None
    policy_evaluation: dict[str, Any] | None
    submission_count: int
    cancel_reason: str | None
    items: list[PRItemOut]
    approvals: list[ApprovalStepOut]
    allowed_actions: list[str]


class PRPage(BaseModel):
    items: list[PRSummaryOut]
    total: int


class ApproveIn(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class RejectIn(BaseModel):
    comment: str = Field(min_length=3, max_length=2000)


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class InboxItemOut(BaseModel):
    approval_id: uuid.UUID
    purchase_request_id: uuid.UUID
    number: str
    title: str
    requester_name: str | None
    department_name: str | None
    category: str
    estimated_total: Decimal
    currency: str
    is_emergency: bool
    approver_role: str
    assigned_to_me: bool
    rule_ids: list[str]
    reasons: list[str]
    activated_at: datetime | None
    due_at: datetime | None
    is_overdue: bool


class PRStatsOut(BaseModel):
    """Counts over the requests visible to the caller. Computed live from the database."""

    by_status: dict[str, int]
    my_open: int
    awaiting_my_approval: int
    overdue_approvals_for_me: int
    approved_value_current_fy: Decimal
    currency: str
    fiscal_year: int


class PolicyOut(BaseModel):
    id: uuid.UUID
    version: int
    is_active: bool
    config: PolicyConfig
    notes: str | None
    created_by_id: uuid.UUID | None
    created_at: datetime


class PolicyPublishIn(BaseModel):
    config: PolicyConfig
    notes: str | None = Field(default=None, max_length=2000)


class RuleOut(BaseModel):
    id: str
    title: str
    description: str
