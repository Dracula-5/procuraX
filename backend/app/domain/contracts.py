"""Supplier contracts: lifecycle, which contract covers a purchase, cap use and expiry reminders.

Pure functions, no I/O. The policy engine's `contract_status` fact comes from an active contract that
covers the vendor, category and date (see `covering_contract`); vendors.contract_status remains the
fallback for vendors with no contract records yet (migration path from the old vendor field).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.core.errors import BusinessRuleViolation, InvalidStateTransitionError


class ContractState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    EXPIRED = "expired"
    TERMINATED = "terminated"


class ContractAction(StrEnum):
    ACTIVATE = "activate"
    EXPIRE = "expire"
    TERMINATE = "terminate"


TRANSITIONS: dict[ContractState, dict[ContractAction, ContractState]] = {
    ContractState.DRAFT: {
        ContractAction.ACTIVATE: ContractState.ACTIVE,
        ContractAction.TERMINATE: ContractState.TERMINATED,
    },
    ContractState.ACTIVE: {
        ContractAction.EXPIRE: ContractState.EXPIRED,
        ContractAction.TERMINATE: ContractState.TERMINATED,
    },
    ContractState.EXPIRED: {},
    ContractState.TERMINATED: {},
}

REMIND_DAYS_BEFORE = 30


def transition(current: ContractState, action: ContractAction) -> ContractState:
    target = TRANSITIONS[current].get(action)
    if target is None:
        raise InvalidStateTransitionError(
            f"Cannot {action.value} a contract that is {current.value}",
            details={"status": current.value, "action": action.value},
        )
    return target


def validate_terms(starts_on: date, ends_on: date, value_cap: Decimal | None) -> None:
    if ends_on < starts_on:
        raise BusinessRuleViolation(
            "A contract cannot end before it starts", details={"reason": "contract_dates"}
        )
    if value_cap is not None and value_cap <= 0:
        raise BusinessRuleViolation("A value cap must be positive", details={"reason": "contract_cap"})


@dataclass(frozen=True)
class ContractTerms:
    id: str
    number: str
    vendor_id: str
    state: ContractState
    starts_on: date
    ends_on: date
    categories: tuple[str, ...] = ()  # empty = every category
    value_cap: Decimal | None = None
    currency: str = "JPY"


def is_in_force(c: ContractTerms, on: date) -> bool:
    return c.state == ContractState.ACTIVE and c.starts_on <= on <= c.ends_on


def covers(c: ContractTerms, vendor_id: str, category: str, on: date) -> bool:
    return c.vendor_id == vendor_id and is_in_force(c, on) and (not c.categories or category in c.categories)


def covering_contract(
    contracts: list[ContractTerms], vendor_id: str, category: str, on: date
) -> ContractTerms | None:
    """The in-force contract for this vendor and category, latest ending first (deterministic)."""
    matches = [c for c in contracts if covers(c, vendor_id, category, on)]
    return max(matches, key=lambda c: (c.ends_on, c.number)) if matches else None


def vendor_contract_status(
    contracts: list[ContractTerms], vendor_id: str, category: str, on: date
) -> str | None:
    """active / expired / none from contract records, or None when the vendor has no records (use the legacy field)."""
    mine = [c for c in contracts if c.vendor_id == vendor_id and c.state != ContractState.DRAFT]
    if not mine:
        return None
    if covering_contract(mine, vendor_id, category, on) is not None:
        return "active"
    ended = [
        c for c in mine if c.state in {ContractState.EXPIRED, ContractState.TERMINATED} or c.ends_on < on
    ]
    return "expired" if ended else "none"


def due_for_expiry(c: ContractTerms, today: date) -> bool:
    return c.state == ContractState.ACTIVE and c.ends_on < today


def due_for_reminder(
    c: ContractTerms, today: date, reminded: bool, days_before: int = REMIND_DAYS_BEFORE
) -> bool:
    """One reminder when an active contract enters its last `days_before` days."""
    return c.state == ContractState.ACTIVE and not reminded and 0 <= (c.ends_on - today).days <= days_before
