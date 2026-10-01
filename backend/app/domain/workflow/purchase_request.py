"""Purchase-request lifecycle as an explicit state machine.

Every status change in the system goes through `transition()`, so an illegal move
(e.g. approving a cancelled request) is impossible regardless of which endpoint or
background job triggers it. P6 extends APPROVED → ORDERED → CLOSED when purchase orders land.

    DRAFT ──submit──▶ PENDING_APPROVAL ──approve(last step)──▶ APPROVED
      │   ╲                │  ╲
      │    ╲ submit        │   ╲ reject
      │     ▼              │    ▼
      │   POLICY_BLOCKED   │  REJECTED
      │     │ reopen       │    │ reopen
      │     ▼              │    ▼
      └──▶ DRAFT ◀─────────┴────┘          (any non-terminal) ──cancel──▶ CANCELLED
    DRAFT ──submit (auto-approve rule)──▶ APPROVED
"""

from enum import StrEnum

from app.core.errors import InvalidStateTransitionError


class PRStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    POLICY_BLOCKED = "policy_blocked"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class PRAction(StrEnum):
    EDIT = "edit"
    SUBMIT = "submit"
    APPROVE_STEP = "approve_step"
    REJECT = "reject"
    CANCEL = "cancel"
    REOPEN = "reopen"


S, A = PRStatus, PRAction

TRANSITIONS: dict[PRStatus, dict[PRAction, frozenset[PRStatus]]] = {
    S.DRAFT: {
        A.EDIT: frozenset({S.DRAFT}),
        A.SUBMIT: frozenset({S.PENDING_APPROVAL, S.APPROVED, S.POLICY_BLOCKED}),
        A.CANCEL: frozenset({S.CANCELLED}),
    },
    S.PENDING_APPROVAL: {
        A.APPROVE_STEP: frozenset({S.PENDING_APPROVAL, S.APPROVED}),
        A.REJECT: frozenset({S.REJECTED}),
        A.CANCEL: frozenset({S.CANCELLED}),
    },
    S.POLICY_BLOCKED: {
        A.REOPEN: frozenset({S.DRAFT}),
        A.CANCEL: frozenset({S.CANCELLED}),
    },
    S.REJECTED: {
        A.REOPEN: frozenset({S.DRAFT}),
        A.CANCEL: frozenset({S.CANCELLED}),
    },
    S.APPROVED: {
        A.CANCEL: frozenset({S.CANCELLED}),
    },
    S.CANCELLED: {},
}

TERMINAL_STATES = frozenset(s for s, actions in TRANSITIONS.items() if not actions)

_VERBS = {
    PRAction.EDIT: "edit",
    PRAction.SUBMIT: "submit",
    PRAction.APPROVE_STEP: "approve",
    PRAction.REJECT: "reject",
    PRAction.CANCEL: "cancel",
    PRAction.REOPEN: "reopen",
}


def allowed_actions(status: PRStatus) -> list[PRAction]:
    return list(TRANSITIONS[status])


def transition(current: PRStatus, action: PRAction, target: PRStatus) -> PRStatus:
    targets = TRANSITIONS[current].get(action)
    if targets is None:
        raise InvalidStateTransitionError(
            f"Cannot {_VERBS[action]} a request that is {current.value.replace('_', ' ')}",
            details={"status": current.value, "action": action.value},
        )
    if target not in targets:
        raise InvalidStateTransitionError(
            f"Action '{action.value}' cannot move a request from '{current.value}' to '{target.value}'",
            details={"status": current.value, "action": action.value, "target": target.value},
        )
    return target


class ApprovalStepStatus(StrEnum):
    WAITING = "waiting"  # later in the chain; not yet actionable
    PENDING = "pending"  # current step; actionable by the assigned approver / role pool
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"  # e.g. same person already approved an earlier step
    CANCELLED = "cancelled"  # request cancelled / rejected before this step was reached
