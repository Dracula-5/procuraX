"""Purchase-request lifecycle: drafting, deterministic policy evaluation, approval routing,
human decisions, cancellation and reopening.

Rules of the road:
  * Every status change goes through the state machine (app.domain.workflow).
  * Every mutation locks the request row (SELECT … FOR UPDATE) so concurrent clicks and
    concurrent approvers serialise instead of racing.
  * Every mutation writes audit entries in the same transaction.
  * The policy engine decides routing; people decide approvals; nothing here uses ML.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import ColumnElement, and_, exists, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.db import utcnow
from app.core.errors import BusinessRuleViolation, ConflictError, NotFoundError, PermissionDeniedError
from app.core.principal import Principal
from app.core.sequences import next_document_number
from app.domain.catalog import ContractStatus, SpendCategory, VendorRiskLevel, VendorStatus
from app.domain.fiscal import fiscal_year_of
from app.domain.money import format_money, quantize_money
from app.domain.policy.engine import evaluate
from app.domain.policy.models import (
    ApproverRole,
    BudgetFacts,
    PolicyEvaluation,
    RequestFacts,
    StepOrder,
    VendorFacts,
)
from app.domain.rbac import APPROVER_ROLES, ROLE_DESCRIPTIONS, Permission, Role
from app.domain.workflow.purchase_request import TRANSITIONS, PRAction, PRStatus, transition
from app.domain.workflow.purchase_request import ApprovalStepStatus as Step
from app.modules.audit import service as audit
from app.modules.identity.models import Organization, User
from app.modules.organization import service as org_service
from app.modules.organization.models import CostCenter, Department
from app.modules.procurement import policy_service
from app.modules.procurement.models import Approval, PurchaseRequest, PurchaseRequestItem
from app.modules.procurement.schemas import (
    ApprovalStepOut,
    InboxItemOut,
    PRCreate,
    PRDetailOut,
    PRItemIn,
    PRItemOut,
    PRStatsOut,
    PRSummaryOut,
    PRUpdate,
)
from app.modules.vendors.models import Vendor

PR = PurchaseRequest
# Requests that count toward split-purchase detection.
SPLIT_WINDOW_STATUSES = (PRStatus.PENDING_APPROVAL.value, PRStatus.APPROVED.value)
OPEN_STATUSES = (PRStatus.DRAFT.value, PRStatus.PENDING_APPROVAL.value, PRStatus.POLICY_BLOCKED.value)


def _role_label(role: str) -> str:
    try:
        return ROLE_DESCRIPTIONS[Role(role)][0]
    except ValueError:
        return role


def _local_today(org: Organization) -> date:
    try:
        tz = ZoneInfo(org.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    return utcnow().astimezone(tz).date()


# ---------------------------------------------------------------------------------------
# Visibility (row-level authorisation inside the tenant)
# ---------------------------------------------------------------------------------------


def _pool_roles(principal: Principal) -> list[str]:
    if not principal.has(Permission.APPROVAL_ACT):
        return []
    return sorted(r.value for r in principal.roles if r in APPROVER_ROLES)


def visibility_clause(principal: Principal) -> ColumnElement[bool]:
    """Which requests the caller may see. RLS already restricts to the tenant; this narrows
    further by role: own requests, direct reports', departments headed, and any request
    where the caller is (or was) an approver or belongs to a pending step's approver pool."""
    if principal.has(Permission.PR_READ_ALL):
        return true()
    me = principal.user_id
    conditions: list[ColumnElement[bool]] = [PR.requester_id == me]
    if principal.has(Permission.PR_READ_TEAM):
        conditions.append(PR.requester_id.in_(select(User.id).where(User.manager_id == me)))
    if principal.has(Permission.PR_READ_DEPARTMENT):
        conditions.append(PR.department_id.in_(select(Department.id).where(Department.head_user_id == me)))
    step_condition = or_(Approval.assigned_user_id == me, Approval.decided_by_id == me)
    pool = _pool_roles(principal)
    if pool:
        step_condition = or_(
            step_condition, and_(Approval.assigned_user_id.is_(None), Approval.approver_role.in_(pool))
        )
    conditions.append(exists().where(Approval.purchase_request_id == PR.id, step_condition))
    return or_(*conditions)


async def _get(
    session: AsyncSession, principal: Principal, pr_id: uuid.UUID, *, for_update: bool = False
) -> PurchaseRequest:
    query = (
        select(PR)
        .where(PR.id == pr_id, PR.org_id == principal.org_id, visibility_clause(principal))
        .execution_options(populate_existing=True)
    )
    if for_update:
        query = query.with_for_update(of=PR)
    pr = await session.scalar(query)
    if pr is None:
        # Same response whether it does not exist, is in another tenant, or is not visible.
        raise NotFoundError("Purchase request not found")
    return pr


# ---------------------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------------------


async def _resolve_cost_center(
    session: AsyncSession, principal: Principal, cc_id: uuid.UUID | None
) -> CostCenter | None:
    if cc_id is None:
        return None
    cc = await org_service.get_cost_center(session, principal.org_id, cc_id)
    if not cc.is_active:
        raise BusinessRuleViolation("This cost centre is inactive")
    return cc


async def _check_vendor(session: AsyncSession, principal: Principal, vendor_id: uuid.UUID | None) -> None:
    if vendor_id is None:
        return
    vendor = await session.get(Vendor, vendor_id)
    if vendor is None or vendor.org_id != principal.org_id or vendor.deleted_at is not None:
        raise NotFoundError("Vendor not found")


def _apply_items(pr: PurchaseRequest, items: list[PRItemIn], currency: str) -> None:
    # Update rows in place by position, then append/remove at the tail. Replacing the
    # whole collection would INSERT line 1 before DELETEing the old line 1 and trip the
    # (purchase_request_id, line_no) unique constraint.
    existing = sorted(pr.items, key=lambda i: i.line_no)
    for line_no, data in enumerate(items, start=1):
        line_total = quantize_money(data.quantity * data.unit_price, currency)
        if line_no <= len(existing):
            item = existing[line_no - 1]
            item.description, item.quantity, item.unit_price = (
                data.description,
                data.quantity,
                data.unit_price,
            )
            item.uom, item.line_total = data.uom, line_total
        else:
            pr.items.append(
                PurchaseRequestItem(
                    org_id=pr.org_id,
                    line_no=line_no,
                    description=data.description,
                    quantity=data.quantity,
                    unit_price=data.unit_price,
                    uom=data.uom,
                    line_total=line_total,
                )
            )
    for item in existing[len(items) :]:
        pr.items.remove(item)
    pr.estimated_total = quantize_money(
        sum((quantize_money(d.quantity * d.unit_price, currency) for d in items), Decimal("0")), currency
    )


async def create_request(session: AsyncSession, principal: Principal, data: PRCreate) -> PurchaseRequest:
    org = await session.get(Organization, principal.org_id)
    assert org is not None
    cc = await _resolve_cost_center(session, principal, data.cost_center_id)
    await _check_vendor(session, principal, data.preferred_vendor_id)
    number = await next_document_number(session, org.id, "PR", _local_today(org).year)
    pr = PurchaseRequest(
        org_id=org.id,
        number=number,
        title=data.title.strip(),
        justification=data.justification,
        requester_id=principal.user_id,
        # The charged department (via cost centre) owns the budget and the head approval.
        department_id=cc.department_id if cc else principal.department_id,
        cost_center_id=cc.id if cc else None,
        category=data.category.value,
        currency=org.base_currency,
        required_by=data.required_by,
        preferred_vendor_id=data.preferred_vendor_id,
        is_emergency=data.is_emergency,
        status=PRStatus.DRAFT.value,
        submission_count=0,
        items=[],
        approvals=[],
    )
    _apply_items(pr, data.items, org.base_currency)
    session.add(pr)
    await session.flush()
    await audit.record(
        session,
        org_id=org.id,
        actor=principal,
        action="purchase_request.created",
        entity_type="purchase_request",
        entity_id=pr.id,
        summary=f"Draft {pr.number} created: {pr.title} ({format_money(pr.estimated_total, pr.currency)})",
    )
    await session.commit()
    return pr


async def update_request(
    session: AsyncSession, principal: Principal, pr_id: uuid.UUID, data: PRUpdate
) -> PurchaseRequest:
    pr = await _get(session, principal, pr_id, for_update=True)
    if pr.requester_id != principal.user_id:
        raise PermissionDeniedError("Only the requester can edit this request")
    transition(PRStatus(pr.status), PRAction.EDIT, PRStatus.DRAFT)

    fields = data.model_dump(exclude_unset=True)
    changed: list[str] = []
    if "cost_center_id" in fields:
        cc = await _resolve_cost_center(session, principal, data.cost_center_id)
        pr.cost_center_id = cc.id if cc else None
        pr.department_id = cc.department_id if cc else principal.department_id
        changed.append("cost_center_id")
    if "preferred_vendor_id" in fields:
        await _check_vendor(session, principal, data.preferred_vendor_id)
        pr.preferred_vendor_id = data.preferred_vendor_id
        changed.append("preferred_vendor_id")
    if "required_by" in fields:
        pr.required_by = data.required_by
        changed.append("required_by")
    for key in ("title", "justification", "is_emergency"):
        if fields.get(key) is not None:
            setattr(pr, key, fields[key].strip() if isinstance(fields[key], str) else fields[key])
            changed.append(key)
    if data.category is not None:
        pr.category = data.category.value
        changed.append("category")
    if data.items is not None:
        _apply_items(pr, data.items, pr.currency)
        changed.append("items")

    if changed:
        await audit.record(
            session,
            org_id=pr.org_id,
            actor=principal,
            action="purchase_request.updated",
            entity_type="purchase_request",
            entity_id=pr.id,
            summary=f"Draft updated: {', '.join(changed)}",
            meta={"fields": changed, "estimated_total": str(pr.estimated_total)},
        )
    await session.commit()
    return pr


# ---------------------------------------------------------------------------------------
# Policy evaluation
# ---------------------------------------------------------------------------------------


async def _build_facts(
    session: AsyncSession, pr: PurchaseRequest, org: Organization, window_days: int
) -> tuple[RequestFacts, int]:
    fiscal_year = fiscal_year_of(_local_today(org), org.fiscal_year_start_month)

    vendor_facts = None
    if pr.preferred_vendor_id:
        v = await session.get(Vendor, pr.preferred_vendor_id)
        if v is not None:
            vendor_facts = VendorFacts(
                id=str(v.id),
                name=v.name,
                status=VendorStatus(v.status),
                risk_level=VendorRiskLevel(v.risk_level),
                contract_status=ContractStatus(v.contract_status),
                categories=tuple(SpendCategory(c) for c in v.categories),
            )

    budget_facts = None
    if pr.cost_center_id:
        budget = await org_service.find_budget(session, org.id, pr.cost_center_id, fiscal_year)
        committed = await org_service.committed_amount(
            session, org.id, pr.cost_center_id, fiscal_year, exclude_request_id=pr.id
        )
        budget_facts = BudgetFacts(
            fiscal_year=fiscal_year, budget_amount=budget.amount if budget else None, committed=committed
        )

    since = utcnow() - timedelta(days=window_days)
    count, total, numbers = (
        await session.execute(
            select(
                func.count(), func.coalesce(func.sum(PR.estimated_total), 0), func.array_agg(PR.number)
            ).where(
                PR.org_id == org.id,
                PR.requester_id == pr.requester_id,
                PR.category == pr.category,
                PR.id != pr.id,
                PR.status.in_(SPLIT_WINDOW_STATUSES),
                PR.submitted_at >= since,
            )
        )
    ).one()

    facts = RequestFacts(
        amount=pr.estimated_total,
        currency=pr.currency,
        category=SpendCategory(pr.category),
        line_count=len(pr.items),
        has_cost_center=pr.cost_center_id is not None,
        is_emergency=pr.is_emergency,
        justification=pr.justification or "",
        vendor=vendor_facts,
        budget=budget_facts,
        recent_similar_total=Decimal(total or 0),
        recent_similar_count=int(count or 0),
        recent_similar_numbers=tuple(sorted(n for n in (numbers or []) if n)),
    )
    return facts, fiscal_year


async def preview_policy(session: AsyncSession, principal: Principal, pr_id: uuid.UUID) -> PolicyEvaluation:
    """Dry run: what would happen if this request were submitted now. No state change."""
    pr = await _get(session, principal, pr_id)
    org = await session.get(Organization, principal.org_id)
    assert org is not None
    policy_row, config = await policy_service.get_active(session, org.id)
    facts, _ = await _build_facts(session, pr, org, config.split_purchase_window_days)
    return evaluate(facts, config, policy_version=policy_row.version)


# ---------------------------------------------------------------------------------------
# Approval chain mechanics
# ---------------------------------------------------------------------------------------


async def _active_user(session: AsyncSession, user_id: uuid.UUID | None) -> User | None:
    if user_id is None:
        return None
    user = await session.get(User, user_id)
    return user if user is not None and user.is_active and user.deleted_at is None else None


async def _resolve_assignee(
    session: AsyncSession, pr: PurchaseRequest, requester: User, role: ApproverRole
) -> uuid.UUID | None:
    """Named approver from the org structure, or None → any holder of the role (pool)."""
    if role == ApproverRole.MANAGER:
        manager = await _active_user(session, requester.manager_id)
        if manager is not None and manager.id != requester.id:
            return manager.id
        role = ApproverRole.DEPARTMENT_HEAD  # no line manager → department head
    if role == ApproverRole.DEPARTMENT_HEAD:
        dept = await session.get(Department, pr.department_id) if pr.department_id else None
        head = await _active_user(session, dept.head_user_id if dept else None)
        if head is not None and head.id != requester.id:
            return head.id
        if head is not None and head.id == requester.id:
            # The requester heads the department: escalate to their own manager if any.
            manager = await _active_user(session, requester.manager_id)
            if manager is not None:
                return manager.id
        return None
    return None  # procurement / finance steps are handled by a team (role pool)


def _round_steps(pr: PurchaseRequest) -> list[Approval]:
    return sorted((s for s in pr.approvals if s.round == pr.submission_count), key=lambda s: s.sequence)


def _current_step(pr: PurchaseRequest) -> Approval | None:
    return next((s for s in _round_steps(pr) if s.status == Step.PENDING.value), None)


def _activate_next(pr: PurchaseRequest, sla_hours: int) -> Approval | None:
    """Make the next waiting step actionable. A step assigned to someone who already
    approved an earlier step in this round is skipped (no double sign-off by one person)."""
    now = utcnow()
    steps = _round_steps(pr)
    approved_by = {s.decided_by_id for s in steps if s.status == Step.APPROVED.value}
    for step in steps:
        if step.status != Step.WAITING.value:
            continue
        if step.assigned_user_id is not None and step.assigned_user_id in approved_by:
            step.status = Step.SKIPPED.value
            step.decided_at = now
            step.comment = "Skipped: this approver already approved an earlier step of this request"
            continue
        step.status = Step.PENDING.value
        step.activated_at = now
        step.due_at = now + timedelta(hours=sla_hours)
        return step
    return None


def _step_denial(principal: Principal, pr: PurchaseRequest, step: Approval) -> str | None:
    if principal.user_id == pr.requester_id:
        return "Requesters cannot approve their own requests (segregation of duties)"
    if step.assigned_user_id is not None:
        # Named approvers are authorised by the org structure (reporting line / head of department).
        if step.assigned_user_id != principal.user_id:
            return "This approval step is assigned to another approver"
        return None
    if step.approver_role not in _pool_roles(principal):
        return f"This approval step requires the '{_role_label(step.approver_role)}' role"
    return None


def _authorize_step(principal: Principal, pr: PurchaseRequest, step: Approval) -> None:
    denial = _step_denial(principal, pr, step)
    if denial:
        raise PermissionDeniedError(denial, code="approval_not_allowed")


def allowed_actions_for(principal: Principal, pr: PurchaseRequest) -> list[str]:
    status = PRStatus(pr.status)
    is_requester = pr.requester_id == principal.user_id
    actions: list[str] = []
    if is_requester and status == PRStatus.DRAFT:
        actions += ["edit", "submit"]
    step = _current_step(pr)
    if status == PRStatus.PENDING_APPROVAL and step is not None and _step_denial(principal, pr, step) is None:
        actions += ["approve", "reject"]
    if is_requester and status in (PRStatus.REJECTED, PRStatus.POLICY_BLOCKED):
        actions.append("reopen")
    if PRAction.CANCEL in TRANSITIONS[status] and (
        principal.has(Permission.PR_CANCEL_ANY) or (is_requester and status != PRStatus.APPROVED)
    ):
        actions.append("cancel")
    return actions


# ---------------------------------------------------------------------------------------
# Workflow actions
# ---------------------------------------------------------------------------------------


def _require_action(pr: PurchaseRequest, action: PRAction) -> PRStatus:
    status = PRStatus(pr.status)
    if action not in TRANSITIONS[status]:
        # Delegate to the state machine for a consistent error.
        transition(status, action, status)
    return status


async def submit(session: AsyncSession, principal: Principal, pr_id: uuid.UUID) -> PurchaseRequest:
    pr = await _get(session, principal, pr_id, for_update=True)
    if pr.requester_id != principal.user_id:
        raise PermissionDeniedError("Only the requester can submit this request")
    status = _require_action(pr, PRAction.SUBMIT)

    org = await session.get(Organization, principal.org_id)
    assert org is not None
    policy_row, config = await policy_service.get_active(session, org.id)
    facts, fiscal_year = await _build_facts(session, pr, org, config.split_purchase_window_days)
    evaluation = evaluate(facts, config, policy_version=policy_row.version)

    target = {
        "blocked": PRStatus.POLICY_BLOCKED,
        "auto_approved": PRStatus.APPROVED,
        "requires_approval": PRStatus.PENDING_APPROVAL,
    }[evaluation.outcome]
    now = utcnow()
    pr.status = transition(status, PRAction.SUBMIT, target).value
    pr.submission_count += 1
    pr.fiscal_year = fiscal_year
    pr.policy_version = policy_row.version
    pr.policy_evaluation = evaluation.model_dump(mode="json")
    pr.submitted_at = now
    pr.decided_at = None

    await audit.record(
        session,
        org_id=pr.org_id,
        actor=principal,
        action="purchase_request.submitted",
        entity_type="purchase_request",
        entity_id=pr.id,
        summary=f"Submitted {pr.number} for {format_money(pr.estimated_total, pr.currency)}",
        changes={"status": {"before": status.value, "after": target.value}},
        meta={
            "round": pr.submission_count,
            "policy_version": policy_row.version,
            "outcome": evaluation.outcome,
            "rule_ids": [h.rule_id for h in evaluation.hits],
        },
    )

    if evaluation.outcome == "blocked":
        reasons = "; ".join(f"{h.rule_id}: {h.message}" for h in evaluation.blocking_hits)
        await audit.record(
            session,
            org_id=pr.org_id,
            actor=None,
            actor_type="policy_engine",
            action="purchase_request.policy_blocked",
            entity_type="purchase_request",
            entity_id=pr.id,
            summary=f"Blocked by policy v{policy_row.version}: {reasons}",
            meta={"rule_ids": [h.rule_id for h in evaluation.blocking_hits]},
        )
    elif evaluation.outcome == "auto_approved":
        pr.decided_at = now
        await audit.record(
            session,
            org_id=pr.org_id,
            actor=None,
            actor_type="policy_engine",
            action="purchase_request.auto_approved",
            entity_type="purchase_request",
            entity_id=pr.id,
            summary=f"Auto-approved by policy v{policy_row.version} (AUTO-001)",
            meta={"rule_ids": ["AUTO-001"]},
        )
    else:
        requester = await session.get(User, pr.requester_id)
        assert requester is not None
        for spec in evaluation.required_approvals:
            pr.approvals.append(
                Approval(
                    org_id=pr.org_id,
                    round=pr.submission_count,
                    sequence=spec.sequence,
                    approver_role=spec.role.value,
                    assigned_user_id=await _resolve_assignee(session, pr, requester, spec.role),
                    rule_ids=spec.rule_ids,
                    reasons=spec.reasons,
                    status=Step.WAITING.value,
                )
            )
        _activate_next(pr, config.approval_sla_hours)
        chain = " → ".join(_role_label(s.role.value) for s in evaluation.required_approvals)
        await audit.record(
            session,
            org_id=pr.org_id,
            actor=None,
            actor_type="policy_engine",
            action="approval.routed",
            entity_type="purchase_request",
            entity_id=pr.id,
            summary=f"Routed by policy v{policy_row.version}: {chain}",
            meta={
                "steps": [
                    {"role": s.role.value, "rule_ids": s.rule_ids} for s in evaluation.required_approvals
                ]
            },
        )
    await session.commit()
    return pr


async def _final_budget_recheck(
    session: AsyncSession, pr: PurchaseRequest, steps: list[Approval]
) -> tuple[str, str] | None:
    """Re-validate budget at the moment of final approval, under a lock on the budget row.

    Two requests that each fit the remaining budget at submission can jointly exceed it.
    Locking the budget row serialises final approvals per cost centre, so the second one
    sees the first one's commitment and is routed to finance instead of silently overspending.
    """
    if pr.cost_center_id is None or pr.fiscal_year is None:
        return None
    if any(
        s.approver_role == ApproverRole.FINANCE_MANAGER.value and s.status == Step.APPROVED.value
        for s in steps
    ):
        return None  # finance already accepted the budget position
    budget = await org_service.find_budget(
        session, pr.org_id, pr.cost_center_id, pr.fiscal_year, for_update=True
    )
    if budget is None:
        return "BUD-001", f"No FY{pr.fiscal_year} budget exists for this cost centre at final approval."
    committed = await org_service.committed_amount(
        session, pr.org_id, pr.cost_center_id, pr.fiscal_year, exclude_request_id=pr.id
    )
    if committed + pr.estimated_total > budget.amount:
        fmt = lambda v: format_money(v, pr.currency)  # noqa: E731
        return (
            "BUD-002",
            f"Budget changed since submission: committed {fmt(committed)} + this request "
            f"{fmt(pr.estimated_total)} exceeds the FY{pr.fiscal_year} budget {fmt(budget.amount)}.",
        )
    return None


async def approve(
    session: AsyncSession, principal: Principal, pr_id: uuid.UUID, comment: str | None
) -> PurchaseRequest:
    pr = await _get(session, principal, pr_id, for_update=True)
    status = _require_action(pr, PRAction.APPROVE_STEP)
    step = _current_step(pr)
    if step is None:
        raise ConflictError("No approval step is awaiting a decision")
    _authorize_step(principal, pr, step)

    now = utcnow()
    step.status = Step.APPROVED.value
    step.decided_by_id = principal.user_id
    step.decided_at = now
    step.comment = comment
    await audit.record(
        session,
        org_id=pr.org_id,
        actor=principal,
        action="approval.approved",
        entity_type="purchase_request",
        entity_id=pr.id,
        summary=f"{_role_label(step.approver_role)} step approved by {principal.full_name}"
        + (f": {comment}" if comment else ""),
        meta={"approval_id": str(step.id), "sequence": step.sequence, "rule_ids": step.rule_ids},
    )

    _, config = await policy_service.get_active(session, pr.org_id)
    if _activate_next(pr, config.approval_sla_hours) is None:
        recheck = await _final_budget_recheck(session, pr, _round_steps(pr))
        if recheck is not None:
            rule_id, reason = recheck
            steps = _round_steps(pr)
            pr.approvals.append(
                Approval(
                    org_id=pr.org_id,
                    round=pr.submission_count,
                    sequence=max(max(s.sequence for s in steps) + 1, int(StepOrder.FINANCE_MANAGER)),
                    approver_role=ApproverRole.FINANCE_MANAGER.value,
                    assigned_user_id=None,
                    rule_ids=[rule_id],
                    reasons=[reason],
                    status=Step.PENDING.value,
                    activated_at=now,
                    due_at=now + timedelta(hours=config.approval_sla_hours),
                )
            )
            await audit.record(
                session,
                org_id=pr.org_id,
                actor=None,
                actor_type="policy_engine",
                action="approval.step_added",
                entity_type="purchase_request",
                entity_id=pr.id,
                summary=f"{rule_id}: {reason} Routed to Finance Manager.",
                meta={"rule_ids": [rule_id]},
            )
        else:
            pr.status = transition(status, PRAction.APPROVE_STEP, PRStatus.APPROVED).value
            pr.decided_at = now
            await audit.record(
                session,
                org_id=pr.org_id,
                actor=principal,
                action="purchase_request.approved",
                entity_type="purchase_request",
                entity_id=pr.id,
                summary=f"{pr.number} fully approved",
                changes={"status": {"before": status.value, "after": PRStatus.APPROVED.value}},
            )
    await session.commit()
    return pr


async def reject(
    session: AsyncSession, principal: Principal, pr_id: uuid.UUID, comment: str
) -> PurchaseRequest:
    pr = await _get(session, principal, pr_id, for_update=True)
    status = _require_action(pr, PRAction.REJECT)
    step = _current_step(pr)
    if step is None:
        raise ConflictError("No approval step is awaiting a decision")
    _authorize_step(principal, pr, step)

    now = utcnow()
    step.status = Step.REJECTED.value
    step.decided_by_id = principal.user_id
    step.decided_at = now
    step.comment = comment
    for other in _round_steps(pr):
        if other.status == Step.WAITING.value:
            other.status = Step.CANCELLED.value
    pr.status = transition(status, PRAction.REJECT, PRStatus.REJECTED).value
    pr.decided_at = now
    await audit.record(
        session,
        org_id=pr.org_id,
        actor=principal,
        action="approval.rejected",
        entity_type="purchase_request",
        entity_id=pr.id,
        summary=f"Rejected at {_role_label(step.approver_role)} step by {principal.full_name}: {comment}",
        changes={"status": {"before": status.value, "after": PRStatus.REJECTED.value}},
        meta={"approval_id": str(step.id), "sequence": step.sequence},
    )
    await session.commit()
    return pr


async def cancel(
    session: AsyncSession, principal: Principal, pr_id: uuid.UUID, reason: str
) -> PurchaseRequest:
    pr = await _get(session, principal, pr_id, for_update=True)
    status = _require_action(pr, PRAction.CANCEL)
    is_requester = pr.requester_id == principal.user_id
    if status == PRStatus.APPROVED and not principal.has(Permission.PR_CANCEL_ANY):
        raise PermissionDeniedError("Only procurement can cancel an approved request")
    if not is_requester and not principal.has(Permission.PR_CANCEL_ANY):
        raise PermissionDeniedError("Only the requester or procurement can cancel this request")

    now = utcnow()
    for step in _round_steps(pr):
        if step.status in (Step.WAITING.value, Step.PENDING.value):
            step.status = Step.CANCELLED.value
    pr.status = transition(status, PRAction.CANCEL, PRStatus.CANCELLED).value
    pr.cancelled_at = now
    pr.cancel_reason = reason
    await audit.record(
        session,
        org_id=pr.org_id,
        actor=principal,
        action="purchase_request.cancelled",
        entity_type="purchase_request",
        entity_id=pr.id,
        summary=f"Cancelled by {principal.full_name}: {reason}",
        changes={"status": {"before": status.value, "after": PRStatus.CANCELLED.value}},
    )
    await session.commit()
    return pr


async def reopen(session: AsyncSession, principal: Principal, pr_id: uuid.UUID) -> PurchaseRequest:
    pr = await _get(session, principal, pr_id, for_update=True)
    if pr.requester_id != principal.user_id:
        raise PermissionDeniedError("Only the requester can reopen this request")
    status = _require_action(pr, PRAction.REOPEN)
    pr.status = transition(status, PRAction.REOPEN, PRStatus.DRAFT).value
    pr.decided_at = None
    await audit.record(
        session,
        org_id=pr.org_id,
        actor=principal,
        action="purchase_request.reopened",
        entity_type="purchase_request",
        entity_id=pr.id,
        summary=f"Reopened as draft (was {status.value}); previous approval history is kept",
        changes={"status": {"before": status.value, "after": PRStatus.DRAFT.value}},
    )
    await session.commit()
    return pr


# ---------------------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------------------

_Requester = aliased(User, name="requester")

_SUMMARY_COLUMNS = (
    PR.id,
    PR.number,
    PR.title,
    PR.status,
    PR.category,
    PR.currency,
    PR.estimated_total,
    PR.requester_id,
    _Requester.full_name.label("requester_name"),
    PR.department_id,
    Department.name.label("department_name"),
    PR.cost_center_id,
    CostCenter.code.label("cost_center_code"),
    PR.preferred_vendor_id,
    Vendor.name.label("vendor_name"),
    PR.is_emergency,
    PR.required_by,
    PR.submitted_at,
    PR.decided_at,
    PR.created_at,
    PR.updated_at,
)


def _summary_query():  # noqa: ANN202
    return (
        select(*_SUMMARY_COLUMNS)
        .join(_Requester, _Requester.id == PR.requester_id)
        .outerjoin(Department, Department.id == PR.department_id)
        .outerjoin(CostCenter, CostCenter.id == PR.cost_center_id)
        .outerjoin(Vendor, Vendor.id == PR.preferred_vendor_id)
    )


async def list_requests(
    session: AsyncSession,
    principal: Principal,
    *,
    mine: bool = False,
    statuses: list[PRStatus] | None = None,
    category: SpendCategory | None = None,
    department_id: uuid.UUID | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[PRSummaryOut], int]:
    conditions: list[ColumnElement[bool]] = [PR.org_id == principal.org_id, visibility_clause(principal)]
    if mine:
        conditions.append(PR.requester_id == principal.user_id)
    if statuses:
        conditions.append(PR.status.in_([s.value for s in statuses]))
    if category:
        conditions.append(PR.category == category.value)
    if department_id:
        conditions.append(PR.department_id == department_id)
    if q:
        pattern = f"%{q.strip()}%"
        conditions.append(or_(PR.title.ilike(pattern), PR.number.ilike(pattern)))
    total = await session.scalar(select(func.count()).select_from(PR).where(*conditions))
    rows = await session.execute(
        _summary_query().where(*conditions).order_by(PR.created_at.desc(), PR.id).limit(limit).offset(offset)
    )
    return [PRSummaryOut.model_validate(dict(r._mapping)) for r in rows], int(total or 0)


async def detail(session: AsyncSession, principal: Principal, pr_id: uuid.UUID) -> PRDetailOut:
    pr = await _get(session, principal, pr_id)
    summary_row = (await session.execute(_summary_query().where(PR.id == pr.id))).one()
    user_ids = {s.assigned_user_id for s in pr.approvals} | {s.decided_by_id for s in pr.approvals}
    user_ids.discard(None)
    names: dict[uuid.UUID, str] = {}
    if user_ids:
        names = dict(
            (await session.execute(select(User.id, User.full_name).where(User.id.in_(user_ids)))).all()
        )
    now = utcnow()
    return PRDetailOut(
        **dict(summary_row._mapping),
        justification=pr.justification,
        fiscal_year=pr.fiscal_year,
        policy_version=pr.policy_version,
        policy_evaluation=pr.policy_evaluation,
        submission_count=pr.submission_count,
        cancel_reason=pr.cancel_reason,
        items=[
            PRItemOut(
                id=i.id,
                line_no=i.line_no,
                description=i.description,
                quantity=i.quantity,
                unit_price=i.unit_price,
                uom=i.uom,
                line_total=i.line_total,
            )
            for i in sorted(pr.items, key=lambda i: i.line_no)
        ],
        approvals=[
            ApprovalStepOut(
                id=s.id,
                round=s.round,
                sequence=s.sequence,
                approver_role=s.approver_role,
                assigned_user_id=s.assigned_user_id,
                assigned_user_name=names.get(s.assigned_user_id) if s.assigned_user_id else None,
                rule_ids=list(s.rule_ids),
                reasons=list(s.reasons or []),
                status=s.status,
                decided_by_id=s.decided_by_id,
                decided_by_name=names.get(s.decided_by_id) if s.decided_by_id else None,
                decided_at=s.decided_at,
                comment=s.comment,
                activated_at=s.activated_at,
                due_at=s.due_at,
                is_overdue=bool(s.status == Step.PENDING.value and s.due_at and s.due_at < now),
                escalation_count=s.escalation_count,
                last_escalated_at=s.last_escalated_at,
            )
            for s in sorted(pr.approvals, key=lambda s: (s.round, s.sequence))
        ],
        allowed_actions=allowed_actions_for(principal, pr),
    )


async def timeline(session: AsyncSession, principal: Principal, pr_id: uuid.UUID) -> list:
    pr = await _get(session, principal, pr_id)
    return await audit.for_entity(session, principal.org_id, pr.id)


async def inbox(session: AsyncSession, principal: Principal) -> list[InboxItemOut]:
    me = principal.user_id
    target: ColumnElement[bool] = Approval.assigned_user_id == me
    pool = _pool_roles(principal)
    if pool:
        target = or_(target, and_(Approval.assigned_user_id.is_(None), Approval.approver_role.in_(pool)))
    requester = aliased(User)
    rows = await session.execute(
        select(
            Approval,
            PR.number,
            PR.title,
            PR.category,
            PR.estimated_total,
            PR.currency,
            PR.is_emergency,
            requester.full_name,
            Department.name,
        )
        .join(PR, PR.id == Approval.purchase_request_id)
        .join(requester, requester.id == PR.requester_id)
        .outerjoin(Department, Department.id == PR.department_id)
        .where(
            Approval.org_id == principal.org_id,
            Approval.status == Step.PENDING.value,
            PR.status == PRStatus.PENDING_APPROVAL.value,
            PR.requester_id != me,
            target,
        )
        .order_by(Approval.due_at.asc().nulls_last(), Approval.activated_at)
    )
    now = utcnow()
    return [
        InboxItemOut(
            approval_id=step.id,
            purchase_request_id=step.purchase_request_id,
            number=number,
            title=title,
            requester_name=requester_name,
            department_name=department_name,
            category=category,
            estimated_total=total,
            currency=currency,
            is_emergency=is_emergency,
            approver_role=step.approver_role,
            assigned_to_me=step.assigned_user_id == me,
            rule_ids=list(step.rule_ids),
            reasons=list(step.reasons or []),
            activated_at=step.activated_at,
            due_at=step.due_at,
            is_overdue=bool(step.due_at and step.due_at < now),
        )
        for step, number, title, category, total, currency, is_emergency, requester_name, department_name in rows
    ]


async def stats(session: AsyncSession, principal: Principal) -> PRStatsOut:
    org = await session.get(Organization, principal.org_id)
    assert org is not None
    visible = [PR.org_id == org.id, visibility_clause(principal)]
    by_status = dict(
        (await session.execute(select(PR.status, func.count()).where(*visible).group_by(PR.status))).all()
    )
    my_open = await session.scalar(
        select(func.count())
        .select_from(PR)
        .where(PR.org_id == org.id, PR.requester_id == principal.user_id, PR.status.in_(OPEN_STATUSES))
    )
    fiscal_year = fiscal_year_of(_local_today(org), org.fiscal_year_start_month)
    approved_value = await session.scalar(
        select(func.coalesce(func.sum(PR.estimated_total), 0)).where(
            *visible, PR.status == PRStatus.APPROVED.value, PR.fiscal_year == fiscal_year
        )
    )
    pending = await inbox(session, principal)
    return PRStatsOut(
        by_status={s.value: int(by_status.get(s.value, 0)) for s in PRStatus},
        my_open=int(my_open or 0),
        awaiting_my_approval=len(pending),
        overdue_approvals_for_me=sum(1 for p in pending if p.is_overdue),
        approved_value_current_fy=Decimal(approved_value or 0),
        currency=org.base_currency,
        fiscal_year=fiscal_year,
    )
