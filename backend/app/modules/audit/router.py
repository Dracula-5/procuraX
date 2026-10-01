import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DB, requires
from app.core.principal import Principal
from app.domain.rbac import Permission
from app.modules.audit import service
from app.modules.audit.schemas import AuditEntryOut, AuditPage

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=AuditPage)
async def search_audit_logs(
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.AUDIT_READ))],
    entity_type: Annotated[str | None, Query(max_length=50)] = None,
    entity_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=80, description="Prefix match, e.g. 'approval.'")] = None,
    actor_user_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage:
    rows, total = await service.search(
        session,
        principal.org_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_user_id=actor_user_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return AuditPage(items=[AuditEntryOut.model_validate(r) for r in rows], total=total)
