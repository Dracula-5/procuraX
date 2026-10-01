"""Scheduled escalation of overdue approval steps to the approver's manager."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import system_session, utcnow
from app.domain.workflow.purchase_request import ApprovalStepStatus
from app.modules.audit import service as audit
from app.modules.identity.models import User
from app.modules.organization.models import Department
from app.modules.procurement.models import Approval, ApprovalPolicy, PurchaseRequest

logger = logging.getLogger("procurax.sla")


async def run_sla_escalations(session: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    """Reassign overdue approvals one management level upward, once per SLA window.

    The caller must use a system session with the narrowly-scoped RLS bypass. The scheduled
    process is idempotent: it extends due_at and records the escalation in the audit log.
    """
    current_time = now or utcnow()
    rows = await session.execute(
        select(Approval, PurchaseRequest)
        .join(PurchaseRequest, PurchaseRequest.id == Approval.purchase_request_id)
        .where(
            Approval.status == ApprovalStepStatus.PENDING.value,
            Approval.due_at.is_not(None),
            Approval.due_at <= current_time,
            PurchaseRequest.status == "pending_approval",
        )
        .order_by(Approval.due_at)
        .with_for_update(skip_locked=True)
    )
    escalated = 0
    no_target = 0
    sla_by_org: dict[Any, int] = {}
    for step, request in rows:
        source_id = step.assigned_user_id
        if source_id is None:
            requester = await session.get(User, request.requester_id)
            source_id = requester.manager_id if requester else None
        source = await session.get(User, source_id) if source_id else None
        candidate_id = source.manager_id if source else None
        if candidate_id is None and request.department_id:
            candidate_id = await session.scalar(
                select(Department.head_user_id).where(
                    Department.id == request.department_id,
                    Department.org_id == request.org_id,
                )
            )

        approved_by = set(
            await session.scalars(
                select(Approval.decided_by_id).where(
                    Approval.org_id == request.org_id,
                    Approval.purchase_request_id == request.id,
                    Approval.round == step.round,
                    Approval.status == ApprovalStepStatus.APPROVED.value,
                )
            )
        )
        target: User | None = None
        visited: set[Any] = set()
        while candidate_id and candidate_id not in visited:
            visited.add(candidate_id)
            candidate = await session.scalar(
                select(User).where(
                    User.id == candidate_id,
                    User.org_id == request.org_id,
                    User.is_active.is_(True),
                )
            )
            if candidate is None:
                break
            if candidate.id != request.requester_id and candidate.id not in approved_by:
                target = candidate
                break
            candidate_id = candidate.manager_id
        if target is None:
            no_target += 1
            continue

        old_assignee = step.assigned_user_id
        step.assigned_user_id = target.id
        step.escalation_count += 1
        step.last_escalated_at = current_time
        if step.org_id not in sla_by_org:
            config = await session.scalar(
                select(ApprovalPolicy.config).where(
                    ApprovalPolicy.org_id == step.org_id,
                    ApprovalPolicy.is_active.is_(True),
                )
            )
            sla_by_org[step.org_id] = int((config or {}).get("approval_sla_hours", 48))
        step.due_at = current_time + timedelta(hours=sla_by_org[step.org_id])
        step.reasons = [
            *(step.reasons or []),
            f"SLA escalation #{step.escalation_count} to {target.full_name}",
        ]
        await audit.record(
            session,
            org_id=step.org_id,
            actor=None,
            actor_type="system",
            action="approval.sla_escalated",
            entity_type="approval",
            entity_id=step.id,
            summary=f"Overdue approval for {request.number} escalated to {target.full_name}",
            changes={
                "assigned_user_id": {
                    "before": str(old_assignee) if old_assignee else None,
                    "after": str(target.id),
                },
                "due_at": step.due_at,
                "escalation_count": step.escalation_count,
            },
            meta={"purchase_request_id": str(request.id), "round": step.round, "sequence": step.sequence},
        )
        escalated += 1
    await session.commit()
    return {"escalated": escalated, "no_target": no_target}


# Arbitrary constant key for pg_try_advisory_xact_lock; identifies "the SLA sweep" cluster-wide.
SLA_SWEEP_LOCK_KEY = 7_221_301


async def run_sla_sweep_once() -> dict[str, int] | None:
    """One sweep in a system session; returns None when another process holds the sweep lock.

    The transaction-scoped advisory lock means only one API replica or scheduler run sweeps at a
    time; it is released when run_sla_escalations commits.
    """
    session = system_session()
    try:
        acquired = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": SLA_SWEEP_LOCK_KEY}
        )
        if not acquired:
            await session.rollback()
            return None
        return await run_sla_escalations(session)
    finally:
        await session.close()


async def sla_sweep_loop(interval_seconds: int) -> None:
    """Background loop started from the API lifespan when the interval setting is positive."""
    while True:
        try:
            result = await run_sla_sweep_once()
            if result is not None and (result["escalated"] or result["no_target"]):
                logger.info("sla_sweep", extra=result)
        except asyncio.CancelledError:
            raise
        except Exception:  # A failed sweep must not stop future sweeps.
            logger.exception("sla_sweep_failed")
        await asyncio.sleep(interval_seconds)
