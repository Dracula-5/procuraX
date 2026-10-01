"""Rule catalogue. Every decision the engine makes cites one of these IDs.

Severity semantics:
  block  – the request cannot proceed (status POLICY_BLOCKED); requester must change it
  route  – adds a required human approval step
  flag   – non-blocking signal shown to approvers and recorded for audit / analytics
  info   – explanatory (e.g. why a request was auto-approved)
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    description: str


RULES: dict[str, Rule] = {
    r.id: r
    for r in [
        Rule("VAL-001", "Request has no value", "A request needs at least one line with a positive total."),
        Rule("VAL-002", "Cost centre missing", "Every request must be charged to a cost centre."),
        Rule(
            "VAL-003",
            "Emergency justification insufficient",
            "Emergency purchases bypass line-manager approval, so they require a written justification.",
        ),
        Rule(
            "VAL-004",
            "Currency mismatch",
            "Request currency must match the policy currency (multi-currency approval is future scope).",
        ),
        Rule(
            "VEN-001",
            "Vendor blocked or suspended",
            "Purchases from blocked or suspended vendors are not allowed.",
        ),
        Rule(
            "VEN-002",
            "Vendor not yet approved",
            "Vendors pending onboarding review require procurement review.",
        ),
        Rule("VEN-003", "High-risk vendor", "High-risk vendors require procurement review."),
        Rule(
            "VEN-004",
            "No active contract for contract-controlled category",
            "This category must be bought under an active contract; off-contract purchases need procurement review.",
        ),
        Rule(
            "VEN-005",
            "Vendor not registered for category",
            "The vendor is not registered for this spend category.",
        ),
        Rule(
            "VEN-006",
            "Sourcing required",
            "Contract-controlled category with no vendor selected: procurement must source a contracted vendor.",
        ),
        Rule(
            "APR-001",
            "Manager approval",
            "Requests above the auto-approval limit need line-manager approval.",
        ),
        Rule(
            "APR-002",
            "Department head approval",
            "Requests above the manager limit need department-head approval.",
        ),
        Rule("APR-003", "Procurement review (value)", "High-value requests need procurement review."),
        Rule("APR-004", "Procurement review (category)", "This category always needs procurement review."),
        Rule(
            "APR-005",
            "Emergency approval",
            "Emergency purchases are approved by the department head directly and reviewed retrospectively.",
        ),
        Rule(
            "BUD-001",
            "No budget defined",
            "No budget exists for the cost centre and fiscal year; finance must approve.",
        ),
        Rule(
            "BUD-002",
            "Budget exceeded",
            "The request would exceed the remaining budget; finance must approve.",
        ),
        Rule(
            "BUD-003",
            "Budget nearly exhausted",
            "Budget utilisation after this request is above the warning level.",
        ),
        Rule(
            "SPL-001",
            "Possible split purchase",
            "Recent requests in the same category, combined with this one, cross an approval threshold "
            "that each stays under individually.",
        ),
        Rule("EMG-001", "Emergency purchase", "Flagged for retrospective procurement review."),
        Rule("AUTO-001", "Auto-approved", "Low-value request with no risk signals; approved by policy."),
    ]
}
