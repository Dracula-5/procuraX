from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import InvalidStateTransitionError
from app.domain.fiscal import fiscal_year_of
from app.domain.money import format_money, quantize_money
from app.domain.rbac import (
    ASSIGNABLE_ROLES,
    PERMISSION_DESCRIPTIONS,
    ROLE_DESCRIPTIONS,
    ROLE_PERMISSIONS,
    Permission,
    Role,
    permissions_for,
)
from app.domain.workflow.purchase_request import (
    TERMINAL_STATES,
    TRANSITIONS,
    PRAction,
    PRStatus,
    allowed_actions,
    transition,
)

# --- Purchase-request state machine --------------------------------------------------------


def test_happy_path_transitions() -> None:
    s = transition(PRStatus.DRAFT, PRAction.SUBMIT, PRStatus.PENDING_APPROVAL)
    s = transition(s, PRAction.APPROVE_STEP, PRStatus.PENDING_APPROVAL)
    assert transition(s, PRAction.APPROVE_STEP, PRStatus.APPROVED) == PRStatus.APPROVED


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (PRStatus.DRAFT, PRAction.APPROVE_STEP),
        (PRStatus.APPROVED, PRAction.SUBMIT),
        (PRStatus.APPROVED, PRAction.EDIT),
        (PRStatus.CANCELLED, PRAction.REOPEN),
        (PRStatus.PENDING_APPROVAL, PRAction.EDIT),
        (PRStatus.REJECTED, PRAction.APPROVE_STEP),
    ],
)
def test_illegal_actions_raise(status: PRStatus, action: PRAction) -> None:
    with pytest.raises(InvalidStateTransitionError):
        transition(status, action, PRStatus.DRAFT)


def test_action_cannot_reach_undeclared_target() -> None:
    with pytest.raises(InvalidStateTransitionError):
        transition(PRStatus.DRAFT, PRAction.SUBMIT, PRStatus.REJECTED)


def test_cancelled_is_the_only_terminal_state_and_everything_can_reach_it() -> None:
    assert {PRStatus.CANCELLED} == TERMINAL_STATES
    for status in PRStatus:
        if status is not PRStatus.CANCELLED:
            assert PRAction.CANCEL in allowed_actions(status)


def test_every_status_reachable_from_draft() -> None:
    seen, frontier = {PRStatus.DRAFT}, [PRStatus.DRAFT]
    while frontier:
        for targets in TRANSITIONS[frontier.pop()].values():
            for t in targets - seen:
                seen.add(t)
                frontier.append(t)
    assert seen == set(PRStatus)


# --- RBAC ----------------------------------------------------------------------------------


def test_every_role_and_permission_is_described() -> None:
    assert set(ROLE_DESCRIPTIONS) == set(Role) == set(ROLE_PERMISSIONS)
    assert set(PERMISSION_DESCRIPTIONS) == set(Permission)


def test_org_admin_cannot_approve_spend() -> None:
    # Segregation of duties: whoever configures policy/users must not approve purchases.
    assert Permission.APPROVAL_ACT not in ROLE_PERMISSIONS[Role.ORG_ADMIN]
    assert Permission.BUDGET_MANAGE not in ROLE_PERMISSIONS[Role.ORG_ADMIN]


def test_read_only_analyst_has_no_write_permissions() -> None:
    writes = {"create", "manage", "act", "approve", "cancel_any", "admin"}
    for perm in ROLE_PERMISSIONS[Role.READ_ONLY_ANALYST]:
        assert perm.value.split(":")[1] not in writes, perm


def test_platform_admin_has_no_tenant_business_permissions_and_is_not_assignable() -> None:
    assert ROLE_PERMISSIONS[Role.PLATFORM_ADMIN] == {Permission.PLATFORM_ADMIN}
    assert Role.PLATFORM_ADMIN not in ASSIGNABLE_ROLES


def test_vendor_role_is_isolated_from_internal_data() -> None:
    assert ROLE_PERMISSIONS[Role.VENDOR] == {Permission.VENDOR_PORTAL}


def test_permissions_union_across_roles() -> None:
    perms = permissions_for(frozenset({Role.EMPLOYEE, Role.FINANCE_ANALYST}))
    assert Permission.PR_CREATE in perms and Permission.BUDGET_READ in perms
    assert Permission.APPROVAL_ACT not in perms


# --- Fiscal year & money ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "start", "expected"),
    [
        (date(2026, 3, 31), 4, 2025),
        (date(2026, 4, 1), 4, 2026),
        (date(2027, 1, 15), 4, 2026),
        (date(2026, 1, 1), 1, 2026),
        (date(2026, 9, 30), 10, 2025),
    ],
)
def test_fiscal_year(day: date, start: int, expected: int) -> None:
    assert fiscal_year_of(day, start) == expected


def test_fiscal_year_rejects_invalid_month() -> None:
    with pytest.raises(ValueError):
        fiscal_year_of(date(2026, 1, 1), 13)


def test_money_rounding_and_formatting() -> None:
    assert quantize_money(Decimal("1234.5"), "JPY") == Decimal("1235")
    assert quantize_money(Decimal("10.005"), "USD") == Decimal("10.01")
    assert format_money(Decimal("1500000"), "JPY") == "¥1,500,000"
    assert format_money(Decimal("12.5"), "USD") == "$12.50"
    assert format_money(Decimal("99"), "CHF") == "CHF 99.00"
