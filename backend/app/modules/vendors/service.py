import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.core.principal import Principal
from app.domain.catalog import SpendCategory, VendorRiskLevel, VendorStatus
from app.modules.audit import service as audit
from app.modules.vendors.models import Vendor
from app.modules.vendors.schemas import VendorCreate, VendorStatusChange, VendorUpdate


def _jsonable(value: object) -> object:
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value if value is None or isinstance(value, bool | int | str) else str(value)


async def get_vendor(session: AsyncSession, org_id: uuid.UUID, vendor_id: uuid.UUID) -> Vendor:
    vendor = await session.get(Vendor, vendor_id)
    if vendor is None or vendor.org_id != org_id or vendor.deleted_at is not None:
        raise NotFoundError("Vendor not found")
    return vendor


async def search_vendors(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    q: str | None = None,
    category: SpendCategory | None = None,
    status: VendorStatus | None = None,
    risk_level: VendorRiskLevel | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Vendor], int]:
    conditions = [Vendor.org_id == org_id, Vendor.deleted_at.is_(None)]
    if q:
        pattern = f"%{q.strip()}%"
        conditions.append(or_(Vendor.name.ilike(pattern), Vendor.legal_name.ilike(pattern)))
    if category:
        conditions.append(Vendor.categories.contains([category.value]))  # @> uses the GIN index
    if status:
        conditions.append(Vendor.status == status.value)
    if risk_level:
        conditions.append(Vendor.risk_level == risk_level.value)
    total = await session.scalar(select(func.count()).select_from(Vendor).where(*conditions))
    rows = await session.scalars(
        select(Vendor).where(*conditions).order_by(Vendor.name).limit(limit).offset(offset)
    )
    return list(rows), int(total or 0)


async def _flush_or_conflict(session: AsyncSession, name: str) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(f"A vendor named '{name}' already exists", code="vendor_exists") from exc


async def create_vendor(session: AsyncSession, principal: Principal, data: VendorCreate) -> Vendor:
    payload = data.model_dump(mode="json")
    vendor = Vendor(
        org_id=principal.org_id,
        **{**payload, "name": data.name.strip()},
        # New vendors always start in onboarding review; approval is a separate,
        # permissioned action (segregation of duties between data entry and approval).
        status=VendorStatus.PENDING_REVIEW.value,
    )
    if data.contract_expires_on:
        vendor.contract_expires_on = data.contract_expires_on
    session.add(vendor)
    await _flush_or_conflict(session, data.name)
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="vendor.created",
        entity_type="vendor",
        entity_id=vendor.id,
        summary=f"Onboarded vendor '{vendor.name}' (pending review)",
        changes={"after": payload},
    )
    await session.commit()
    return vendor


async def update_vendor(
    session: AsyncSession, principal: Principal, vendor_id: uuid.UUID, data: VendorUpdate
) -> Vendor:
    vendor = await get_vendor(session, principal.org_id, vendor_id)
    fields = data.model_dump(exclude_unset=True)
    before, after = {}, {}
    for key, value in fields.items():
        if isinstance(value, list):
            value = [v.value if hasattr(v, "value") else v for v in value]
        elif hasattr(value, "value"):
            value = value.value
        if getattr(vendor, key) != value:
            before[key], after[key] = _jsonable(getattr(vendor, key)), _jsonable(value)
            setattr(vendor, key, value)
    if after:
        await _flush_or_conflict(session, vendor.name)
        await audit.record(
            session,
            org_id=principal.org_id,
            actor=principal,
            action="vendor.updated",
            entity_type="vendor",
            entity_id=vendor.id,
            summary=f"Updated vendor '{vendor.name}': {', '.join(after)}",
            changes={"before": before, "after": after},
        )
    await session.commit()
    return vendor


async def change_status(
    session: AsyncSession, principal: Principal, vendor_id: uuid.UUID, data: VendorStatusChange
) -> Vendor:
    vendor = await get_vendor(session, principal.org_id, vendor_id)
    previous = vendor.status
    if previous == data.status.value:
        return vendor
    vendor.status = data.status.value
    vendor.status_changed_at = utcnow()
    vendor.status_changed_by_id = principal.user_id
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action=f"vendor.status.{data.status.value}",
        entity_type="vendor",
        entity_id=vendor.id,
        summary=f"Vendor '{vendor.name}' status {previous} → {data.status.value}",
        changes={"before": {"status": previous}, "after": {"status": data.status.value}},
        meta={"reason": data.reason} if data.reason else None,
    )
    await session.commit()
    return vendor
