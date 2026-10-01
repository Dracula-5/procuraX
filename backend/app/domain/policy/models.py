"""Inputs and outputs of the procurement policy engine.

The engine is deterministic: the same facts + the same policy version always produce the
same evaluation. The evaluation is persisted on the request at submission time, so any
decision can be explained later ("why was this blocked / routed to finance?") exactly as
it was made, even after the policy changes.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from app.domain.catalog import ContractStatus, SpendCategory, VendorRiskLevel, VendorStatus

# Decimals serialise as strings in JSON snapshots: exact, and no float rounding.
Money = Annotated[Decimal, PlainSerializer(lambda v: str(v), return_type=str, when_used="json")]


class PolicyConfig(BaseModel):
    """An organisation's approval policy. Stored as versioned JSONB (approval_policies).

    Default values are an example policy for a Japanese company (amounts in JPY). They are
    configuration, not recommendations; each tenant publishes its own version.
    """

    model_config = ConfigDict(extra="forbid")

    currency: str = Field(default="JPY", min_length=3, max_length=3)
    auto_approval_limit: Money = Field(default=Decimal("30000"), ge=0)
    manager_approval_limit: Money = Field(default=Decimal("500000"), ge=0)
    procurement_review_threshold: Money = Field(default=Decimal("1000000"), ge=0)
    procurement_review_categories: list[SpendCategory] = Field(
        default_factory=lambda: [SpendCategory.SOFTWARE, SpendCategory.PROFESSIONAL_SERVICES]
    )
    contract_required_categories: list[SpendCategory] = Field(
        default_factory=lambda: [
            SpendCategory.SOFTWARE,
            SpendCategory.PROFESSIONAL_SERVICES,
            SpendCategory.LOGISTICS,
        ]
    )
    budget_warning_utilization: Decimal = Field(default=Decimal("0.90"), gt=0, le=1)
    split_purchase_window_days: int = Field(default=14, ge=0, le=90)
    emergency_min_justification_chars: int = Field(default=40, ge=0, le=2000)
    approval_sla_hours: int = Field(default=48, ge=1, le=720)

    @model_validator(mode="after")
    def _thresholds_are_monotonic(self) -> "PolicyConfig":
        if not (self.auto_approval_limit <= self.manager_approval_limit <= self.procurement_review_threshold):
            raise ValueError(
                "Thresholds must satisfy auto_approval_limit <= manager_approval_limit "
                "<= procurement_review_threshold"
            )
        return self


class ApproverRole(StrEnum):
    MANAGER = "manager"
    DEPARTMENT_HEAD = "department_head"
    PROCUREMENT_OFFICER = "procurement_officer"
    FINANCE_MANAGER = "finance_manager"


class StepOrder(IntEnum):
    """Canonical order of approval steps: line management → sourcing → finance."""

    MANAGER = 10
    DEPARTMENT_HEAD = 20
    PROCUREMENT_OFFICER = 30
    FINANCE_MANAGER = 40


STEP_ORDER = {
    ApproverRole.MANAGER: StepOrder.MANAGER,
    ApproverRole.DEPARTMENT_HEAD: StepOrder.DEPARTMENT_HEAD,
    ApproverRole.PROCUREMENT_OFFICER: StepOrder.PROCUREMENT_OFFICER,
    ApproverRole.FINANCE_MANAGER: StepOrder.FINANCE_MANAGER,
}


@dataclass(frozen=True)
class VendorFacts:
    id: str
    name: str
    status: VendorStatus
    risk_level: VendorRiskLevel
    contract_status: ContractStatus
    categories: tuple[SpendCategory, ...] = ()


@dataclass(frozen=True)
class BudgetFacts:
    fiscal_year: int
    budget_amount: Decimal | None  # None → no budget defined for the cost centre / year
    committed: Decimal = Decimal("0")  # approved, not-yet-closed commitments


@dataclass(frozen=True)
class RequestFacts:
    amount: Decimal
    currency: str
    category: SpendCategory
    line_count: int
    has_cost_center: bool
    is_emergency: bool = False
    justification: str = ""
    vendor: VendorFacts | None = None
    budget: BudgetFacts | None = None
    # Requester's other active requests in the same category inside the split window.
    recent_similar_total: Decimal = Decimal("0")
    recent_similar_count: int = 0
    recent_similar_numbers: tuple[str, ...] = field(default_factory=tuple)


Severity = Literal["block", "route", "flag", "info"]


class RuleHit(BaseModel):
    rule_id: str
    title: str
    severity: Severity
    message: str
    evidence: dict[str, str | int | bool | None | list[str]] = Field(default_factory=dict)


class RequiredApproval(BaseModel):
    role: ApproverRole
    sequence: int
    rule_ids: list[str]
    reasons: list[str]


class BudgetCheck(BaseModel):
    status: Literal["within_budget", "near_limit", "exceeded", "no_budget", "not_applicable"]
    fiscal_year: int | None = None
    budget_amount: Money | None = None
    committed: Money | None = None
    requested: Money | None = None
    remaining_after: Money | None = None
    utilization_after: Decimal | None = None


class PolicyEvaluation(BaseModel):
    engine_version: str
    policy_version: int
    outcome: Literal["auto_approved", "requires_approval", "blocked"]
    required_approvals: list[RequiredApproval]
    hits: list[RuleHit]
    budget: BudgetCheck

    @property
    def blocking_hits(self) -> list[RuleHit]:
        return [h for h in self.hits if h.severity == "block"]

    @property
    def flags(self) -> list[RuleHit]:
        return [h for h in self.hits if h.severity == "flag"]
