import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DB, requires
from app.core.principal import Principal
from app.domain.catalog import SpendCategory, VendorRiskLevel, VendorStatus
from app.domain.rbac import Permission
from app.modules.vendors import service
from app.modules.vendors.schemas import VendorCreate, VendorOut, VendorPage, VendorStatusChange, VendorUpdate

router = APIRouter(prefix="/vendors", tags=["vendors"])

Reader = Annotated[Principal, Depends(requires(Permission.VENDOR_READ))]
Manager = Annotated[Principal, Depends(requires(Permission.VENDOR_MANAGE))]
Approver = Annotated[Principal, Depends(requires(Permission.VENDOR_APPROVE))]


@router.get("", response_model=VendorPage)
async def list_vendors(
    session: DB,
    principal: Reader,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category: SpendCategory | None = None,
    status_: Annotated[VendorStatus | None, Query(alias="status")] = None,
    risk_level: VendorRiskLevel | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> VendorPage:
    rows, total = await service.search_vendors(
        session,
        principal.org_id,
        q=q,
        category=category,
        status=status_,
        risk_level=risk_level,
        limit=limit,
        offset=offset,
    )
    return VendorPage(items=[VendorOut.model_validate(v) for v in rows], total=total)


@router.post("", response_model=VendorOut, status_code=status.HTTP_201_CREATED)
async def create_vendor(data: VendorCreate, session: DB, principal: Manager) -> VendorOut:
    return VendorOut.model_validate(await service.create_vendor(session, principal, data))


@router.get("/{vendor_id}", response_model=VendorOut)
async def get_vendor(vendor_id: uuid.UUID, session: DB, principal: Reader) -> VendorOut:
    return VendorOut.model_validate(await service.get_vendor(session, principal.org_id, vendor_id))


@router.patch("/{vendor_id}", response_model=VendorOut)
async def update_vendor(
    vendor_id: uuid.UUID, data: VendorUpdate, session: DB, principal: Manager
) -> VendorOut:
    return VendorOut.model_validate(await service.update_vendor(session, principal, vendor_id, data))


@router.post("/{vendor_id}/status", response_model=VendorOut)
async def change_vendor_status(
    vendor_id: uuid.UUID, data: VendorStatusChange, session: DB, principal: Approver
) -> VendorOut:
    """Approve, suspend or block a vendor. Suspended/blocked vendors are rejected by policy rule VEN-001."""
    return VendorOut.model_validate(await service.change_status(session, principal, vendor_id, data))
