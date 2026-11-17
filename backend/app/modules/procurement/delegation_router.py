"""Delegation of named approval steps: list, eligible delegates, create and revoke.

A named approver (assigned by the org structure) may hand one undecided step to a colleague who
holds the step's approver role. The eligibility rules live in `_delegable_step` and
`_eligible_delegates`, which both the candidate list and the create endpoint use, so the screen can
never offer someone the API would refuse. Every refusal carries `details.reason`, a stable code the
UI translates.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.deps import DB, requires
from app.core.db import utcnow
from app.core.errors import BusinessRuleViolation, ConflictError, NotFoundError
from app.core.principal import Principal
from app.domain.rbac import Permission
from app.domain.workflow.purchase_request import PRStatus
from app.modules.audit import service as audit
from app.modules.identity.models import User, UserRole
from app.modules.procurement.delegations import ApprovalDelegation
from app.modules.procurement.models import Approval, PurchaseRequest

router = APIRouter(prefix="/approvals", tags=["approvals"])
Approver = Annotated[Principal, Depends(requires(Permission.APPROVAL_ACT))]

UNDECIDED = ("pending", "waiting")


class DelegationCreateIn(BaseModel):
    approval_id: uuid.UUID
    delegate_user_id: uuid.UUID


class DelegableStepOut(BaseModel):
    approval_id: uuid.UUID
    purchase_request_id: uuid.UUID
    number: str
    title: str
    requester_name: str | None
    approver_role: str
    status: str
    estimated_total: Decimal
    currency: str
    due_at: datetime | None


class DelegateCandidateOut(BaseModel):
    id: uuid.UUID
    full_name: str
    job_title: str | None


class DelegationOut(BaseModel):
    id: uuid.UUID
    approval_id: uuid.UUID
    purchase_request_id: uuid.UUID
    request_number: str
    request_title: str
    approver_role: str
    step_status: str
    delegator_id: uuid.UUID
    delegator_name: str
    delegate_id: uuid.UUID
    delegate_name: str
    delegated_at: datetime
    revoked_at: datetime | None
    # True for the delegator while the step is undecided and still with the delegate.
    can_revoke: bool


def _refuse(reason: str, message: str) -> NoReturn:
    raise BusinessRuleViolation(message, details={"reason": reason})


async def _delegable_step(
    session: AsyncSession, principal: Principal, approval_id: uuid.UUID, *, lock: bool
) -> tuple[Approval, PurchaseRequest]:
    """The step and its request, if the caller may delegate it; otherwise a coded refusal."""
    query = select(Approval).where(Approval.id == approval_id, Approval.org_id == principal.org_id)
    step = await session.scalar(query.with_for_update() if lock else query)
    if step is None:
        raise NotFoundError("Approval step not found")
    if step.assigned_user_id != principal.user_id:
        _refuse("delegation_not_named_approver", "Only the named approver can delegate this step")
    if step.status not in UNDECIDED:
        _refuse("delegation_step_decided", "Only an undecided approval step can be delegated")
    request = await session.scalar(
        select(PurchaseRequest).where(
            PurchaseRequest.id == step.purchase_request_id, PurchaseRequest.org_id == principal.org_id
        )
    )
    if request is None:
        raise NotFoundError("Approval step not found")
    return step, request


def _eligible_delegates(principal: Principal, step: Approval, request: PurchaseRequest) -> Select[User]:
    """Active users in the tenant holding the step's approver role, other than the caller and requester."""
    return (
        select(User)
        .join(UserRole, and_(UserRole.user_id == User.id, UserRole.org_id == User.org_id))
        .where(
            User.org_id == principal.org_id,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            UserRole.role_key == step.approver_role,
            User.id != principal.user_id,
            User.id != request.requester_id,
        )
    )


@router.get("/delegable", response_model=list[DelegableStepOut])
async def list_delegable_steps(session: DB, principal: Approver) -> list[DelegableStepOut]:
    """Undecided steps named to the caller that have never been delegated (one delegation per step)."""
    requester = aliased(User)
    rows = await session.execute(
        select(
            Approval,
            PurchaseRequest.number,
            PurchaseRequest.title,
            PurchaseRequest.estimated_total,
            PurchaseRequest.currency,
            requester.full_name,
        )
        .join(PurchaseRequest, PurchaseRequest.id == Approval.purchase_request_id)
        .join(requester, requester.id == PurchaseRequest.requester_id)
        .outerjoin(ApprovalDelegation, ApprovalDelegation.approval_id == Approval.id)
        .where(
            Approval.org_id == principal.org_id,
            Approval.assigned_user_id == principal.user_id,
            Approval.status.in_(UNDECIDED),
            PurchaseRequest.status == PRStatus.PENDING_APPROVAL.value,
            ApprovalDelegation.id.is_(None),
        )
        .order_by(Approval.due_at.asc().nulls_last(), PurchaseRequest.number)
    )
    return [
        DelegableStepOut(
            approval_id=step.id,
            purchase_request_id=step.purchase_request_id,
            number=number,
            title=title,
            requester_name=requester_name,
            approver_role=step.approver_role,
            status=step.status,
            estimated_total=total,
            currency=currency,
            due_at=step.due_at,
        )
        for step, number, title, total, currency, requester_name in rows
    ]


@router.get("/{approval_id}/delegate-candidates", response_model=list[DelegateCandidateOut])
async def list_delegate_candidates(
    approval_id: uuid.UUID, session: DB, principal: Approver
) -> list[DelegateCandidateOut]:
    step, request = await _delegable_step(session, principal, approval_id, lock=False)
    users = await session.scalars(_eligible_delegates(principal, step, request).order_by(User.full_name))
    return [DelegateCandidateOut(id=u.id, full_name=u.full_name, job_title=u.job_title) for u in users]


@router.post("/delegations", status_code=status.HTTP_201_CREATED)
async def delegate_approval(data: DelegationCreateIn, session: DB, principal: Approver) -> dict:
    step, request = await _delegable_step(session, principal, data.approval_id, lock=True)
    if data.delegate_user_id == principal.user_id:
        _refuse("delegation_self", "Choose another user to delegate this approval")
    if data.delegate_user_id == request.requester_id:
        _refuse("delegation_requester", "The requester cannot be assigned as an approver")
    delegate = await session.scalar(
        _eligible_delegates(principal, step, request).where(User.id == data.delegate_user_id)
    )
    if delegate is None:
        _refuse(
            "delegation_delegate_ineligible",
            "Delegate must be an active user with the required approver role",
        )
    if await session.scalar(select(ApprovalDelegation.id).where(ApprovalDelegation.approval_id == step.id)):
        raise ConflictError(
            "This approval step already has a delegation record", details={"reason": "delegation_exists"}
        )
    now = utcnow()
    prior_assignee = step.assigned_user_id
    step.assigned_user_id = delegate.id
    row = ApprovalDelegation(
        org_id=principal.org_id,
        approval_id=step.id,
        delegator_id=principal.user_id,
        delegate_id=delegate.id,
        delegated_at=now,
    )
    session.add(row)
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="approval.delegated",
        entity_type="purchase_request",
        entity_id=request.id,
        summary=f"Approval delegated to {delegate.full_name}",
        meta={
            "approval_id": str(step.id),
            "from_user_id": str(prior_assignee),
            "to_user_id": str(delegate.id),
        },
    )
    await session.commit()
    return {
        "id": row.id,
        "approval_id": row.approval_id,
        "delegator_id": row.delegator_id,
        "delegate_id": row.delegate_id,
        "delegated_at": row.delegated_at,
    }


@router.get("/delegations", response_model=list[DelegationOut])
async def list_delegations(session: DB, principal: Approver) -> list[DelegationOut]:
    """Delegations the caller gave or received, newest first."""
    delegator = aliased(User)
    delegate = aliased(User)
    rows = await session.execute(
        select(
            ApprovalDelegation,
            Approval,
            PurchaseRequest.number,
            PurchaseRequest.title,
            delegator.full_name,
            delegate.full_name,
        )
        .join(Approval, Approval.id == ApprovalDelegation.approval_id)
        .join(PurchaseRequest, PurchaseRequest.id == Approval.purchase_request_id)
        .join(delegator, delegator.id == ApprovalDelegation.delegator_id)
        .join(delegate, delegate.id == ApprovalDelegation.delegate_id)
        .where(
            ApprovalDelegation.org_id == principal.org_id,
            or_(
                ApprovalDelegation.delegator_id == principal.user_id,
                ApprovalDelegation.delegate_id == principal.user_id,
            ),
        )
        .order_by(ApprovalDelegation.created_at.desc())
    )
    return [
        DelegationOut(
            id=row.id,
            approval_id=row.approval_id,
            purchase_request_id=step.purchase_request_id,
            request_number=number,
            request_title=title,
            approver_role=step.approver_role,
            step_status=step.status,
            delegator_id=row.delegator_id,
            delegator_name=delegator_name,
            delegate_id=row.delegate_id,
            delegate_name=delegate_name,
            delegated_at=row.delegated_at,
            revoked_at=row.revoked_at,
            can_revoke=(
                row.delegator_id == principal.user_id
                and row.revoked_at is None
                and step.status in UNDECIDED
                and step.assigned_user_id == row.delegate_id
            ),
        )
        for row, step, number, title, delegator_name, delegate_name in rows
    ]


@router.post("/delegations/{delegation_id}/revoke")
async def revoke_delegation(delegation_id: uuid.UUID, session: DB, principal: Approver) -> dict:
    row = await session.scalar(
        select(ApprovalDelegation)
        .where(ApprovalDelegation.id == delegation_id, ApprovalDelegation.org_id == principal.org_id)
        .with_for_update()
    )
    if row is None or row.delegator_id != principal.user_id:
        raise NotFoundError("Approval delegation not found")
    if row.revoked_at is not None:
        raise ConflictError(
            "This delegation has already been revoked", details={"reason": "delegation_already_revoked"}
        )
    step = await session.scalar(
        select(Approval)
        .where(Approval.id == row.approval_id, Approval.org_id == principal.org_id)
        .with_for_update()
    )
    if step is None or step.status not in UNDECIDED or step.assigned_user_id != row.delegate_id:
        _refuse(
            "delegation_revoke_after_decision",
            "A delegation can only be revoked before the approval is decided",
        )
    now = utcnow()
    step.assigned_user_id = row.delegator_id
    row.revoked_at = now
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="approval.delegation_revoked",
        entity_type="purchase_request",
        entity_id=step.purchase_request_id,
        summary="Approval delegation revoked",
        meta={"approval_id": str(step.id), "delegation_id": str(row.id)},
    )
    await session.commit()
    return {"id": row.id, "approval_id": row.approval_id, "revoked_at": row.revoked_at}
