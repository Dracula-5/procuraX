"""Audit trail writer. Called inside the same transaction as the change it records, so an
action and its audit entry commit (or roll back) together."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal
from app.core.request_context import get_client_ip, get_request_id
from app.modules.audit.models import AuditLog


async def record(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | None,
    actor: Principal | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    summary: str,
    changes: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    actor_type: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        org_id=org_id,
        actor_user_id=actor.user_id if actor else None,
        actor_email=actor.email if actor else None,
        actor_type=actor_type or ("user" if actor else "system"),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        changes=changes,
        meta=meta,
        request_id=get_request_id(),
        ip_address=get_client_ip(),
    )
    session.add(entry)
    return entry


def _conditions(
    org_id: uuid.UUID,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    action: str | None,
    actor_user_id: uuid.UUID | None,
    since: datetime | None,
    until: datetime | None,
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [AuditLog.org_id == org_id]
    if entity_type:
        conditions.append(AuditLog.entity_type == entity_type)
    if entity_id:
        conditions.append(AuditLog.entity_id == entity_id)
    if action:
        conditions.append(AuditLog.action.startswith(action))
    if actor_user_id:
        conditions.append(AuditLog.actor_user_id == actor_user_id)
    if since:
        conditions.append(AuditLog.created_at >= since)
    if until:
        conditions.append(AuditLog.created_at < until)
    return conditions


async def search(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    action: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    conditions = _conditions(org_id, entity_type, entity_id, action, actor_user_id, since, until)
    total = await session.scalar(select(func.count()).select_from(AuditLog).where(*conditions))
    rows = await session.scalars(
        select(AuditLog).where(*conditions).order_by(AuditLog.seq.desc()).limit(limit).offset(offset)
    )
    return list(rows), int(total or 0)


async def for_entity(session: AsyncSession, org_id: uuid.UUID, entity_id: uuid.UUID) -> list[AuditLog]:
    rows = await session.scalars(
        select(AuditLog)
        .where(AuditLog.org_id == org_id, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.seq)
    )
    return list(rows)
