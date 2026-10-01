"""Deterministic procurement policy engine.

Pure function of (facts, policy). No I/O, no clock, no randomness, no ML/LLM: approval
routing and blocking are compliance controls and must be reproducible and explainable.
AI components (vendor ranking, anomaly scores) may *add* signals upstream; they never
override a rule's outcome.
"""

from decimal import Decimal

from app.domain.catalog import ContractStatus, VendorRiskLevel, VendorStatus
from app.domain.money import format_money
from app.domain.policy.models import (
    STEP_ORDER,
    ApproverRole,
    BudgetCheck,
    PolicyConfig,
    PolicyEvaluation,
    RequestFacts,
    RequiredApproval,
    RuleHit,
    Severity,
)
from app.domain.policy.rules import RULES

ENGINE_VERSION = "1.0"


class _Collector:
    def __init__(self) -> None:
        self.hits: list[RuleHit] = []
        self.routes: dict[ApproverRole, RequiredApproval] = {}

    def hit(self, rule_id: str, severity: Severity, message: str, **evidence: object) -> None:
        self.hits.append(
            RuleHit(
                rule_id=rule_id,
                title=RULES[rule_id].title,
                severity=severity,
                message=message,
                evidence={k: _evidence_value(v) for k, v in evidence.items()},
            )
        )

    def route(self, role: ApproverRole, rule_id: str, reason: str, **evidence: object) -> None:
        self.hit(rule_id, "route", reason, approver_role=role.value, **evidence)
        step = self.routes.get(role)
        if step is None:
            self.routes[role] = RequiredApproval(
                role=role, sequence=int(STEP_ORDER[role]), rule_ids=[rule_id], reasons=[reason]
            )
        elif rule_id not in step.rule_ids:
            step.rule_ids.append(rule_id)
            step.reasons.append(reason)


def _evidence_value(value: object) -> str | int | bool | None | list[str]:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, list | tuple):
        return [str(v) for v in value]
    return str(value)


def evaluate(facts: RequestFacts, policy: PolicyConfig, *, policy_version: int) -> PolicyEvaluation:
    c = _Collector()
    amount = facts.amount

    def fmt(value: Decimal) -> str:
        return format_money(value, policy.currency)

    # --- 1. Validation (blocking) --------------------------------------------------------
    if facts.line_count == 0 or amount <= 0:
        c.hit("VAL-001", "block", "Add at least one line item with a positive amount.", amount=amount)
    if not facts.has_cost_center:
        c.hit("VAL-002", "block", "Select the cost centre this purchase is charged to.")
    if facts.currency != policy.currency:
        c.hit(
            "VAL-004",
            "block",
            f"Request currency {facts.currency} differs from policy currency {policy.currency}.",
            request_currency=facts.currency,
            policy_currency=policy.currency,
        )
    if facts.is_emergency and len(facts.justification.strip()) < policy.emergency_min_justification_chars:
        c.hit(
            "VAL-003",
            "block",
            f"Emergency purchases need a justification of at least "
            f"{policy.emergency_min_justification_chars} characters.",
            justification_chars=len(facts.justification.strip()),
            required_chars=policy.emergency_min_justification_chars,
        )

    # --- 2. Vendor controls -----------------------------------------------------------------
    vendor = facts.vendor
    contract_category = facts.category in policy.contract_required_categories
    if vendor is not None:
        if vendor.status in (VendorStatus.BLOCKED, VendorStatus.SUSPENDED):
            c.hit(
                "VEN-001",
                "block",
                f"Vendor '{vendor.name}' is {vendor.status.value}; choose another vendor.",
                vendor_id=vendor.id,
                vendor_status=vendor.status.value,
            )
        elif vendor.status == VendorStatus.PENDING_REVIEW:
            c.route(
                ApproverRole.PROCUREMENT_OFFICER,
                "VEN-002",
                f"Vendor '{vendor.name}' has not completed onboarding review.",
                vendor_id=vendor.id,
            )
        if vendor.risk_level == VendorRiskLevel.HIGH:
            c.route(
                ApproverRole.PROCUREMENT_OFFICER,
                "VEN-003",
                f"Vendor '{vendor.name}' is rated high risk.",
                vendor_id=vendor.id,
            )
        if contract_category and vendor.contract_status != ContractStatus.ACTIVE:
            c.route(
                ApproverRole.PROCUREMENT_OFFICER,
                "VEN-004",
                f"'{facts.category.value}' is contract-controlled and vendor '{vendor.name}' has no active "
                f"contract ({vendor.contract_status.value}).",
                vendor_id=vendor.id,
                contract_status=vendor.contract_status.value,
            )
        if vendor.categories and facts.category not in vendor.categories:
            c.hit(
                "VEN-005",
                "flag",
                f"Vendor '{vendor.name}' is not registered for '{facts.category.value}'.",
                vendor_categories=[cat.value for cat in vendor.categories],
            )
    elif contract_category:
        c.route(
            ApproverRole.PROCUREMENT_OFFICER,
            "VEN-006",
            f"'{facts.category.value}' is contract-controlled and no vendor is selected.",
        )

    # --- 3. Value-based approval tiers -----------------------------------------------------
    if facts.is_emergency:
        c.route(
            ApproverRole.DEPARTMENT_HEAD,
            "APR-005",
            "Emergency purchase: expedited approval by the department head.",
        )
        c.hit("EMG-001", "flag", "Emergency purchase will be reviewed retrospectively by procurement.")
    else:
        if amount > policy.auto_approval_limit:
            c.route(
                ApproverRole.MANAGER,
                "APR-001",
                f"Amount {fmt(amount)} exceeds the auto-approval limit {fmt(policy.auto_approval_limit)}.",
                amount=amount,
                threshold=policy.auto_approval_limit,
            )
        if amount > policy.manager_approval_limit:
            c.route(
                ApproverRole.DEPARTMENT_HEAD,
                "APR-002",
                f"Amount {fmt(amount)} exceeds the manager limit {fmt(policy.manager_approval_limit)}.",
                amount=amount,
                threshold=policy.manager_approval_limit,
            )
    if amount > policy.procurement_review_threshold:
        c.route(
            ApproverRole.PROCUREMENT_OFFICER,
            "APR-003",
            f"Amount {fmt(amount)} exceeds the procurement review threshold "
            f"{fmt(policy.procurement_review_threshold)}.",
            amount=amount,
            threshold=policy.procurement_review_threshold,
        )
    if facts.category in policy.procurement_review_categories:
        c.route(
            ApproverRole.PROCUREMENT_OFFICER,
            "APR-004",
            f"Category '{facts.category.value}' always requires procurement review.",
            category=facts.category.value,
        )

    # --- 4. Split-purchase detection (deterministic) --------------------------------------
    if not facts.is_emergency and facts.recent_similar_count > 0 and amount > 0:
        combined = amount + facts.recent_similar_total
        tiers = [
            (policy.auto_approval_limit, ApproverRole.MANAGER),
            (policy.manager_approval_limit, ApproverRole.DEPARTMENT_HEAD),
            (policy.procurement_review_threshold, ApproverRole.PROCUREMENT_OFFICER),
        ]
        crossed = [(t, role) for t, role in tiers if amount <= t < combined]
        if crossed:
            threshold, role = crossed[-1]  # the highest tier the combined amount crosses
            c.route(
                role,
                "SPL-001",
                f"{facts.recent_similar_count} recent '{facts.category.value}' request(s) plus this one total "
                f"{fmt(combined)}, above the {fmt(threshold)} threshold each stays under.",
                combined_amount=combined,
                threshold=threshold,
                related_requests=list(facts.recent_similar_numbers),
                window_days=policy.split_purchase_window_days,
            )

    # --- 5. Budget ------------------------------------------------------------------------
    budget_check = BudgetCheck(status="not_applicable")
    b = facts.budget
    if b is not None and amount > 0:
        if b.budget_amount is None:
            budget_check = BudgetCheck(status="no_budget", fiscal_year=b.fiscal_year, requested=amount)
            c.route(
                ApproverRole.FINANCE_MANAGER,
                "BUD-001",
                f"No budget is defined for this cost centre in FY{b.fiscal_year}.",
                fiscal_year=b.fiscal_year,
            )
        else:
            remaining_after = b.budget_amount - b.committed - amount
            utilization = (
                ((b.committed + amount) / b.budget_amount).quantize(Decimal("0.0001"))
                if b.budget_amount > 0
                else Decimal("999")
            )
            status = "within_budget"
            if remaining_after < 0:
                status = "exceeded"
                c.route(
                    ApproverRole.FINANCE_MANAGER,
                    "BUD-002",
                    f"Request exceeds the FY{b.fiscal_year} budget by {fmt(-remaining_after)}.",
                    budget=b.budget_amount,
                    committed=b.committed,
                    requested=amount,
                )
            elif utilization >= policy.budget_warning_utilization:
                status = "near_limit"
                c.hit(
                    "BUD-003",
                    "flag",
                    f"Budget utilisation after this request: {utilization:.1%}.",
                    utilization_after=utilization,
                )
            budget_check = BudgetCheck(
                status=status,
                fiscal_year=b.fiscal_year,
                budget_amount=b.budget_amount,
                committed=b.committed,
                requested=amount,
                remaining_after=remaining_after,
                utilization_after=utilization,
            )

    # --- 6. Outcome -----------------------------------------------------------------------
    blocked = any(h.severity == "block" for h in c.hits)
    if blocked:
        outcome, steps = "blocked", []
    elif c.routes:
        outcome = "requires_approval"
        steps = sorted(c.routes.values(), key=lambda s: s.sequence)
    else:
        outcome, steps = "auto_approved", []
        c.hit(
            "AUTO-001",
            "info",
            f"Amount {fmt(amount)} is within the auto-approval limit and no risk rules fired.",
            amount=amount,
            threshold=policy.auto_approval_limit,
        )

    return PolicyEvaluation(
        engine_version=ENGINE_VERSION,
        policy_version=policy_version,
        outcome=outcome,
        required_approvals=steps,
        hits=c.hits,
        budget=budget_check,
    )
