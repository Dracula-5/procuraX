import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DB, CurrentUser, requires
from app.core.principal import Principal
from app.domain.catalog import SpendCategory
from app.domain.policy.models import PolicyEvaluation
from app.domain.policy.rules import RULES
from app.domain.rbac import Permission
from app.domain.workflow.purchase_request import PRStatus
from app.modules.audit.schemas import AuditEntryOut
from app.modules.procurement import policy_service, service
from app.modules.procurement.schemas import (
    ApproveIn,
    CancelIn,
    InboxItemOut,
    PolicyOut,
    PolicyPublishIn,
    PRCreate,
    PRDetailOut,
    PRPage,
    PRStatsOut,
    PRUpdate,
    RejectIn,
    RuleOut,
)

router = APIRouter(prefix="/purchase-requests", tags=["purchase requests"])
approvals_router = APIRouter(prefix="/approvals", tags=["approvals"])
policy_router = APIRouter(prefix="/approval-policies", tags=["approval policy"])

Requester = Annotated[Principal, Depends(requires(Permission.PR_CREATE))]


@router.get("", response_model=PRPage)
async def list_purchase_requests(
    session: DB,
    principal: CurrentUser,
    mine: bool = False,
    status_: Annotated[list[PRStatus] | None, Query(alias="status")] = None,
    category: SpendCategory | None = None,
    department_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PRPage:
    """Requests visible to the caller (own, team, department, approver, or all — by role)."""
    items, total = await service.list_requests(
        session,
        principal,
        mine=mine,
        statuses=status_,
        category=category,
        department_id=department_id,
        q=q,
        limit=limit,
        offset=offset,
    )
    return PRPage(items=items, total=total)


@router.get("/stats", response_model=PRStatsOut)
async def purchase_request_stats(session: DB, principal: CurrentUser) -> PRStatsOut:
    return await service.stats(session, principal)


@router.post("", response_model=PRDetailOut, status_code=status.HTTP_201_CREATED)
async def create_purchase_request(data: PRCreate, session: DB, principal: Requester) -> PRDetailOut:
    pr = await service.create_request(session, principal, data)
    return await service.detail(session, principal, pr.id)


@router.get("/{pr_id}", response_model=PRDetailOut)
async def get_purchase_request(pr_id: uuid.UUID, session: DB, principal: CurrentUser) -> PRDetailOut:
    return await service.detail(session, principal, pr_id)


@router.patch("/{pr_id}", response_model=PRDetailOut)
async def update_purchase_request(
    pr_id: uuid.UUID, data: PRUpdate, session: DB, principal: Requester
) -> PRDetailOut:
    await service.update_request(session, principal, pr_id, data)
    return await service.detail(session, principal, pr_id)


@router.post("/{pr_id}/policy-preview", response_model=PolicyEvaluation)
async def preview_policy(pr_id: uuid.UUID, session: DB, principal: CurrentUser) -> PolicyEvaluation:
    """Dry-run the deterministic policy engine against the request as it is now."""
    return await service.preview_policy(session, principal, pr_id)


@router.patch("/{pr_id}/submit", response_model=PRDetailOut)
async def submit_purchase_request(pr_id: uuid.UUID, session: DB, principal: Requester) -> PRDetailOut:
    await service.submit(session, principal, pr_id)
    return await service.detail(session, principal, pr_id)


@router.post("/{pr_id}/approve", response_model=PRDetailOut)
async def approve_purchase_request(
    pr_id: uuid.UUID, session: DB, principal: CurrentUser, data: ApproveIn | None = None
) -> PRDetailOut:
    await service.approve(session, principal, pr_id, data.comment if data else None)
    return await service.detail(session, principal, pr_id)


@router.post("/{pr_id}/reject", response_model=PRDetailOut)
async def reject_purchase_request(
    pr_id: uuid.UUID, data: RejectIn, session: DB, principal: CurrentUser
) -> PRDetailOut:
    await service.reject(session, principal, pr_id, data.comment)
    return await service.detail(session, principal, pr_id)


@router.post("/{pr_id}/cancel", response_model=PRDetailOut)
async def cancel_purchase_request(
    pr_id: uuid.UUID, data: CancelIn, session: DB, principal: CurrentUser
) -> PRDetailOut:
    await service.cancel(session, principal, pr_id, data.reason)
    return await service.detail(session, principal, pr_id)


@router.post("/{pr_id}/reopen", response_model=PRDetailOut)
async def reopen_purchase_request(pr_id: uuid.UUID, session: DB, principal: Requester) -> PRDetailOut:
    await service.reopen(session, principal, pr_id)
    return await service.detail(session, principal, pr_id)


@router.get("/{pr_id}/timeline", response_model=list[AuditEntryOut])
async def purchase_request_timeline(
    pr_id: uuid.UUID, session: DB, principal: CurrentUser
) -> list[AuditEntryOut]:
    """Audit trail of this request, visible to anyone who can see the request."""
    return [AuditEntryOut.model_validate(e) for e in await service.timeline(session, principal, pr_id)]


@approvals_router.get("/inbox", response_model=list[InboxItemOut])
async def approval_inbox(session: DB, principal: CurrentUser) -> list[InboxItemOut]:
    """Approval steps the caller can act on now, most urgent first."""
    return await service.inbox(session, principal)


def _policy_out(row) -> PolicyOut:  # noqa: ANN001
    return PolicyOut(
        id=row.id,
        version=row.version,
        is_active=row.is_active,
        config=row.config,
        notes=row.notes,
        created_by_id=row.created_by_id,
        created_at=row.created_at,
    )


@policy_router.get("/active", response_model=PolicyOut)
async def active_policy(
    session: DB, principal: Annotated[Principal, Depends(requires(Permission.POLICY_READ))]
) -> PolicyOut:
    row, _ = await policy_service.get_active(session, principal.org_id)
    return _policy_out(row)


@policy_router.get("", response_model=list[PolicyOut])
async def policy_versions(
    session: DB, principal: Annotated[Principal, Depends(requires(Permission.POLICY_READ))]
) -> list[PolicyOut]:
    return [_policy_out(r) for r in await policy_service.list_versions(session, principal.org_id)]


@policy_router.post("", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
async def publish_policy(
    data: PolicyPublishIn,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.POLICY_MANAGE))],
) -> PolicyOut:
    """Publish a new policy version. Past decisions keep referencing the version they used."""
    row = await policy_service.publish(session, principal, data.config, data.notes)
    await session.commit()
    return _policy_out(row)


@policy_router.get("/rules", response_model=list[RuleOut])
async def rule_catalogue(_: CurrentUser) -> list[RuleOut]:
    return [RuleOut(id=r.id, title=r.title, description=r.description) for r in RULES.values()]
