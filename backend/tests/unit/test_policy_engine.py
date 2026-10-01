from dataclasses import replace
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.catalog import ContractStatus, SpendCategory, VendorRiskLevel, VendorStatus
from app.domain.policy.engine import evaluate
from app.domain.policy.models import BudgetFacts, PolicyConfig, RequestFacts, VendorFacts
from app.domain.policy.rules import RULES

POLICY = PolicyConfig()  # JPY example policy: auto ≤30k, manager ≤500k, procurement >1M


def facts(amount: str = "100000", **overrides: object) -> RequestFacts:
    base = RequestFacts(
        amount=Decimal(amount),
        currency="JPY",
        category=SpendCategory.OFFICE_SUPPLIES,
        line_count=1,
        has_cost_center=True,
        budget=BudgetFacts(fiscal_year=2026, budget_amount=Decimal("10000000"), committed=Decimal("0")),
    )
    return replace(base, **overrides)


def vendor(**overrides: object) -> VendorFacts:
    base = VendorFacts(
        id="v1",
        name="Acme",
        status=VendorStatus.APPROVED,
        risk_level=VendorRiskLevel.LOW,
        contract_status=ContractStatus.ACTIVE,
        categories=(SpendCategory.OFFICE_SUPPLIES, SpendCategory.SOFTWARE),
    )
    return replace(base, **overrides)


def run(f: RequestFacts, policy: PolicyConfig = POLICY):  # noqa: ANN201
    return evaluate(f, policy, policy_version=1)


def roles(result) -> list[str]:  # noqa: ANN001
    return [s.role.value for s in result.required_approvals]


def rule_ids(result) -> set[str]:  # noqa: ANN001
    return {h.rule_id for h in result.hits}


# --- Value tiers ---------------------------------------------------------------------------


def test_low_value_clean_request_is_auto_approved() -> None:
    result = run(facts("20000"))
    assert result.outcome == "auto_approved"
    assert result.required_approvals == []
    assert "AUTO-001" in rule_ids(result)


@pytest.mark.parametrize(
    ("amount", "expected_roles"),
    [
        ("30000", []),  # exactly at auto limit → auto
        ("30001", ["manager"]),
        ("500000", ["manager"]),  # exactly at manager limit → manager only
        ("500001", ["manager", "department_head"]),
        ("1000000", ["manager", "department_head"]),
        ("1000001", ["manager", "department_head", "procurement_officer"]),
    ],
)
def test_threshold_boundaries_are_strictly_greater_than(amount: str, expected_roles: list[str]) -> None:
    assert roles(run(facts(amount))) == expected_roles


def test_steps_are_in_canonical_order_and_deduplicated() -> None:
    # Procurement is triggered three ways; it must appear once, citing all three rules.
    f = facts(
        "1500000",
        category=SpendCategory.SOFTWARE,
        vendor=vendor(contract_status=ContractStatus.EXPIRED),
    )
    result = run(f)
    assert roles(result) == ["manager", "department_head", "procurement_officer"]
    procurement = result.required_approvals[-1]
    assert set(procurement.rule_ids) == {"VEN-004", "APR-003", "APR-004"}
    assert len(procurement.reasons) == 3
    assert [s.sequence for s in result.required_approvals] == sorted(
        s.sequence for s in result.required_approvals
    )


# --- Vendor controls -----------------------------------------------------------------------


@pytest.mark.parametrize("status", [VendorStatus.BLOCKED, VendorStatus.SUSPENDED])
def test_blocked_or_suspended_vendor_blocks_request(status: VendorStatus) -> None:
    result = run(facts(vendor=vendor(status=status)))
    assert result.outcome == "blocked"
    assert result.required_approvals == []
    assert [h.rule_id for h in result.blocking_hits] == ["VEN-001"]


def test_ai_style_preference_cannot_override_policy_block() -> None:
    """Even a request for a tiny amount (auto-approvable) is blocked for a blocked vendor."""
    result = run(facts("1000", vendor=vendor(status=VendorStatus.BLOCKED)))
    assert result.outcome == "blocked"


def test_vendor_pending_review_requires_procurement() -> None:
    result = run(facts("20000", vendor=vendor(status=VendorStatus.PENDING_REVIEW)))
    assert result.outcome == "requires_approval"
    assert roles(result) == ["procurement_officer"]
    assert "VEN-002" in rule_ids(result)


def test_high_risk_vendor_requires_procurement() -> None:
    result = run(facts("20000", vendor=vendor(risk_level=VendorRiskLevel.HIGH)))
    assert roles(result) == ["procurement_officer"]
    assert "VEN-003" in rule_ids(result)


def test_contract_category_without_active_contract_is_off_contract() -> None:
    f = facts("20000", category=SpendCategory.LOGISTICS, vendor=vendor(contract_status=ContractStatus.NONE))
    result = run(f)
    assert "VEN-004" in rule_ids(result)
    assert roles(result) == ["procurement_officer"]


def test_contract_category_without_vendor_requires_sourcing() -> None:
    result = run(facts("20000", category=SpendCategory.LOGISTICS))
    assert "VEN-006" in rule_ids(result)
    assert roles(result) == ["procurement_officer"]


def test_vendor_not_registered_for_category_is_a_flag_only() -> None:
    result = run(facts("20000", category=SpendCategory.TRAVEL, vendor=vendor()))
    hit = next(h for h in result.hits if h.rule_id == "VEN-005")
    assert hit.severity == "flag"
    assert result.outcome == "auto_approved"


# --- Budget --------------------------------------------------------------------------------


def test_budget_exceeded_routes_to_finance() -> None:
    f = facts("400000", budget=BudgetFacts(2026, Decimal("1000000"), committed=Decimal("700000")))
    result = run(f)
    assert roles(result) == ["manager", "finance_manager"]
    assert result.budget.status == "exceeded"
    assert result.budget.remaining_after == Decimal("-100000")


def test_missing_budget_routes_to_finance() -> None:
    result = run(facts("50000", budget=BudgetFacts(2026, None)))
    assert result.budget.status == "no_budget"
    assert "BUD-001" in rule_ids(result)
    assert roles(result) == ["manager", "finance_manager"]


def test_budget_near_limit_flags_without_finance_step() -> None:
    f = facts("100000", budget=BudgetFacts(2026, Decimal("1000000"), committed=Decimal("820000")))
    result = run(f)
    assert result.budget.status == "near_limit"
    assert "BUD-003" in rule_ids(result)
    assert "finance_manager" not in roles(result)


# --- Split purchases ---------------------------------------------------------------------


def test_split_below_auto_limit_escalates_to_manager() -> None:
    f = facts(
        "20000",
        recent_similar_total=Decimal("20000"),
        recent_similar_count=1,
        recent_similar_numbers=("PR-1",),
    )
    result = run(f)
    assert roles(result) == ["manager"]
    hit = next(h for h in result.hits if h.rule_id == "SPL-001")
    assert hit.evidence["related_requests"] == ["PR-1"]


def test_split_crossing_manager_limit_escalates_to_department_head() -> None:
    f = facts("300000", recent_similar_total=Decimal("400000"), recent_similar_count=2)
    result = run(f)
    assert roles(result) == ["manager", "department_head"]
    assert "SPL-001" in result.required_approvals[1].rule_ids


def test_no_split_flag_when_combined_total_stays_under_thresholds() -> None:
    result = run(facts("5000", recent_similar_total=Decimal("5000"), recent_similar_count=1))
    assert "SPL-001" not in rule_ids(result)
    assert result.outcome == "auto_approved"


# --- Validation & emergency ---------------------------------------------------------------


def test_empty_request_and_missing_cost_centre_are_blocked() -> None:
    result = run(facts("0", line_count=0, has_cost_center=False, budget=None))
    assert {h.rule_id for h in result.blocking_hits} == {"VAL-001", "VAL-002"}


def test_currency_mismatch_is_blocked() -> None:
    result = run(facts(currency="USD"))
    assert "VAL-004" in {h.rule_id for h in result.blocking_hits}


def test_emergency_needs_justification() -> None:
    result = run(facts(is_emergency=True, justification="urgent"))
    assert result.outcome == "blocked"
    assert "VAL-003" in rule_ids(result)


def test_emergency_goes_to_department_head_only_and_is_flagged() -> None:
    result = run(
        facts("200000", is_emergency=True, justification="Production line stopped; replacement pump " * 2)
    )
    assert roles(result) == ["department_head"]
    assert {"APR-005", "EMG-001"} <= rule_ids(result)


def test_emergency_high_value_still_needs_procurement() -> None:
    result = run(facts("2000000", is_emergency=True, justification="x" * 60))
    assert roles(result) == ["department_head", "procurement_officer"]


# --- Engine properties ------------------------------------------------------------------


def test_evaluation_is_deterministic_and_json_serialisable() -> None:
    f = facts("750000", category=SpendCategory.SOFTWARE, vendor=vendor())
    first, second = run(f), run(f)
    assert first == second
    dumped = first.model_dump(mode="json")
    assert dumped["budget"]["requested"] == "750000"
    assert all(isinstance(h["rule_id"], str) for h in dumped["hits"])


def test_every_cited_rule_exists_in_catalogue() -> None:
    f = facts(
        "1500000",
        category=SpendCategory.SOFTWARE,
        vendor=vendor(risk_level=VendorRiskLevel.HIGH, contract_status=ContractStatus.NONE),
        budget=BudgetFacts(2026, Decimal("100")),
        recent_similar_total=Decimal("10"),
        recent_similar_count=1,
    )
    assert rule_ids(run(f)) <= set(RULES)


def test_policy_thresholds_must_be_monotonic() -> None:
    with pytest.raises(ValidationError):
        PolicyConfig(auto_approval_limit=Decimal("600000"), manager_approval_limit=Decimal("500000"))


def test_custom_policy_changes_routing() -> None:
    strict = PolicyConfig(auto_approval_limit=Decimal("0"))
    assert roles(run(facts("1000"), strict)) == ["manager"]


def test_messages_use_policy_currency() -> None:
    usd = PolicyConfig(
        currency="USD",
        auto_approval_limit=Decimal("250"),
        manager_approval_limit=Decimal("5000"),
        procurement_review_threshold=Decimal("10000"),
    )
    result = run(facts("300", currency="USD"), usd)
    assert "$300.00" in result.hits[0].message
