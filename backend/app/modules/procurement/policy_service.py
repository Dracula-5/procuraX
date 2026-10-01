"""Versioned approval-policy storage."""

import uuid
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.principal import Principal
from app.domain.policy.models import PolicyConfig
from app.modules.audit import service as audit
from app.modules.procurement.models import ApprovalPolicy

# Example thresholds (auto-approve, manager limit, procurement review) per currency.
# They are starting configuration for a new tenant, not recommendations.
_DEFAULT_THRESHOLDS: dict[str, tuple[str, str, str]] = {
    "JPY": ("30000", "500000", "1000000"),
    "USD": ("250", "5000", "10000"),
    "EUR": ("250", "5000", "10000"),
    "GBP": ("200", "4000", "8000"),
}


def default_policy_config(currency: str) -> PolicyConfig:
    auto, manager, procurement = _DEFAULT_THRESHOLDS.get(currency, _DEFAULT_THRESHOLDS["USD"])
    return PolicyConfig(
        currency=currency,
        auto_approval_limit=Decimal(auto),
        manager_approval_limit=Decimal(manager),
        procurement_review_threshold=Decimal(procurement),
    )


async def create_initial_policy(
    session: AsyncSession, org_id: uuid.UUID, currency: str, created_by: uuid.UUID | None
) -> ApprovalPolicy:
    policy = ApprovalPolicy(
        org_id=org_id,
        version=1,
        config=default_policy_config(currency).model_dump(mode="json"),
        is_active=True,
        notes="Initial example policy created at organisation registration",
        created_by_id=created_by,
    )
    session.add(policy)
    return policy


async def get_active(session: AsyncSession, org_id: uuid.UUID) -> tuple[ApprovalPolicy, PolicyConfig]:
    row = await session.scalar(
        select(ApprovalPolicy).where(ApprovalPolicy.org_id == org_id, ApprovalPolicy.is_active.is_(True))
    )
    if row is None:
        raise NotFoundError("No active approval policy for this organisation")
    return row, PolicyConfig.model_validate(row.config)


async def list_versions(session: AsyncSession, org_id: uuid.UUID) -> list[ApprovalPolicy]:
    rows = await session.scalars(
        select(ApprovalPolicy).where(ApprovalPolicy.org_id == org_id).order_by(ApprovalPolicy.version.desc())
    )
    return list(rows)


async def publish(
    session: AsyncSession, principal: Principal, config: PolicyConfig, notes: str | None
) -> ApprovalPolicy:
    org_id = principal.org_id
    current, current_config = await get_active(session, org_id)
    # Serialise concurrent publishes for the tenant.
    await session.execute(select(ApprovalPolicy.id).where(ApprovalPolicy.id == current.id).with_for_update())
    next_version = (
        await session.scalar(select(func.max(ApprovalPolicy.version)).where(ApprovalPolicy.org_id == org_id))
        or 0
    ) + 1
    await session.execute(
        update(ApprovalPolicy)
        .where(ApprovalPolicy.org_id == org_id, ApprovalPolicy.is_active.is_(True))
        .values(is_active=False)
    )
    await session.flush()
    policy = ApprovalPolicy(
        org_id=org_id,
        version=next_version,
        config=config.model_dump(mode="json"),
        is_active=True,
        notes=notes,
        created_by_id=principal.user_id,
    )
    session.add(policy)
    await session.flush()
    await audit.record(
        session,
        org_id=org_id,
        actor=principal,
        action="approval_policy.published",
        entity_type="approval_policy",
        entity_id=policy.id,
        summary=f"Published approval policy v{next_version}",
        changes={
            "before": current_config.model_dump(mode="json"),
            "after": config.model_dump(mode="json"),
            "previous_version": current.version,
        },
    )
    return policy
