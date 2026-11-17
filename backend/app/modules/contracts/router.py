"""Supplier contracts: draft → active → expired / terminated, a value cap, and the signed file.

Status changes lock the contract row (FOR UPDATE) and go through `app.domain.contracts.transition`.
Expiry is not a user action: the worker's sweep expires active contracts after their end date.
"""

import base64
import binascii
import hashlib
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, requires
from app.core.config import get_settings
from app.core.errors import BusinessRuleViolation, ConflictError, DomainError, NotFoundError
from app.core.principal import Principal
from app.core.sequences import next_document_number
from app.core.storage import get_storage
from app.domain.catalog import SpendCategory
from app.domain.contracts import TRANSITIONS, ContractAction, ContractState, transition, validate_terms
from app.domain.documents import MAX_DOCUMENT_BYTES, DocumentRejected, ScanStatus, inspect_document
from app.domain.rbac import Permission
from app.modules.audit import service as audit
from app.modules.contracts.models import Contract
from app.modules.contracts.service import committed_under, local_today
from app.modules.documents.scanner import ScanError, clamd_scanner
from app.modules.identity.models import Organization
from app.modules.procurement.schemas import Currency
from app.modules.vendors.models import Vendor

router = APIRouter(prefix="/contracts", tags=["contracts"])
Reader = Annotated[Principal, Depends(requires(Permission.VENDOR_READ))]
Manager = Annotated[Principal, Depends(requires(Permission.VENDOR_MANAGE))]


class ScannerUnavailableError(DomainError):
    status_code = 503
    code = "scanner_unavailable"


class ContractIn(BaseModel):
    vendor_id: uuid.UUID
    title: str = Field(min_length=3, max_length=200)
    categories: list[SpendCategory] = Field(default_factory=list, max_length=20)
    starts_on: date
    ends_on: date
    value_cap: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    currency: Currency


class ContractUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    categories: list[SpendCategory] | None = Field(default=None, max_length=20)
    starts_on: date | None = None
    ends_on: date | None = None
    value_cap: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    currency: Currency | None = None


class TerminateIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class ContractDocumentIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=4, max_length=(MAX_DOCUMENT_BYTES * 4) // 3 + 8)


class ContractDocumentOut(BaseModel):
    filename: str
    mime_type: str
    sha256: str
    scan_status: str


class ContractOut(BaseModel):
    id: uuid.UUID
    number: str
    vendor_id: uuid.UUID
    vendor_name: str
    title: str
    categories: list[str]
    starts_on: date
    ends_on: date
    value_cap: Decimal | None
    currency: str
    status: ContractState
    terminated_reason: str | None
    # Base-currency value of requests in approval or approved under this contract.
    committed: Decimal
    base_currency: str
    document: ContractDocumentOut | None
    expiry_reminded_at: datetime | None
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]


async def _out(session: DB, contract: Contract, vendor_name: str, base_currency: str) -> ContractOut:
    document = None
    if contract.document_object_key:
        document = ContractDocumentOut(
            filename=contract.document_filename or "contract",
            mime_type=contract.document_mime_type or "application/octet-stream",
            sha256=contract.document_sha256 or "",
            scan_status=contract.document_scan_status or ScanStatus.NOT_SCANNED.value,
        )
    state = ContractState(contract.status)
    return ContractOut(
        id=contract.id,
        number=contract.number,
        vendor_id=contract.vendor_id,
        vendor_name=vendor_name,
        title=contract.title,
        categories=list(contract.categories or []),
        starts_on=contract.starts_on,
        ends_on=contract.ends_on,
        value_cap=contract.value_cap,
        currency=contract.currency,
        status=state,
        terminated_reason=contract.terminated_reason,
        committed=await committed_under(session, contract.id),
        base_currency=base_currency,
        document=document,
        expiry_reminded_at=contract.expiry_reminded_at,
        created_at=contract.created_at,
        updated_at=contract.updated_at,
        # Expiry belongs to the sweep, not to a person.
        allowed_actions=sorted(
            [a.value for a in TRANSITIONS[state] if a != ContractAction.EXPIRE]
            + (["edit"] if state == ContractState.DRAFT else [])
        ),
    )


async def _org(session: DB, principal: Principal) -> Organization:
    org = await session.get(Organization, principal.org_id)
    assert org is not None
    return org


async def _vendor(session: DB, principal: Principal, vendor_id: uuid.UUID) -> Vendor:
    vendor = await session.scalar(
        select(Vendor).where(
            Vendor.id == vendor_id, Vendor.org_id == principal.org_id, Vendor.deleted_at.is_(None)
        )
    )
    if vendor is None:
        raise NotFoundError("Vendor not found")
    return vendor


async def _get(session: DB, principal: Principal, contract_id: uuid.UUID, *, lock: bool = False) -> Contract:
    query = select(Contract).where(Contract.id == contract_id, Contract.org_id == principal.org_id)
    contract = await session.scalar(query.with_for_update() if lock else query)
    if contract is None:
        raise NotFoundError("Contract not found")
    return contract


@router.get("", response_model=list[ContractOut])
async def list_contracts(
    session: DB,
    principal: Reader,
    vendor_id: uuid.UUID | None = None,
    status_filter: Annotated[ContractState | None, Query(alias="status")] = None,
) -> list[ContractOut]:
    org = await _org(session, principal)
    query = (
        select(Contract, Vendor.name)
        .join(Vendor, Vendor.id == Contract.vendor_id)
        .where(Contract.org_id == principal.org_id)
        .order_by(Contract.ends_on.desc(), Contract.number)
    )
    if vendor_id:
        query = query.where(Contract.vendor_id == vendor_id)
    if status_filter:
        query = query.where(Contract.status == status_filter.value)
    rows = (await session.execute(query.limit(500))).all()
    return [await _out(session, c, name, org.base_currency) for c, name in rows]


@router.get("/{contract_id}", response_model=ContractOut)
async def get_contract(contract_id: uuid.UUID, session: DB, principal: Reader) -> ContractOut:
    contract = await _get(session, principal, contract_id)
    vendor = await _vendor(session, principal, contract.vendor_id)
    return await _out(session, contract, vendor.name, (await _org(session, principal)).base_currency)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ContractOut)
async def create_contract(data: ContractIn, session: DB, principal: Manager) -> ContractOut:
    validate_terms(data.starts_on, data.ends_on, data.value_cap)
    org = await _org(session, principal)
    vendor = await _vendor(session, principal, data.vendor_id)
    contract = Contract(
        org_id=org.id,
        number=await next_document_number(session, org.id, "CT", local_today(org.timezone).year),
        vendor_id=vendor.id,
        title=data.title.strip(),
        categories=sorted({c.value for c in data.categories}),
        starts_on=data.starts_on,
        ends_on=data.ends_on,
        value_cap=data.value_cap,
        currency=data.currency,
        status=ContractState.DRAFT.value,
        created_by_id=principal.user_id,
    )
    session.add(contract)
    await session.flush()
    await audit.record(
        session,
        org_id=org.id,
        actor=principal,
        action="contract.created",
        entity_type="contract",
        entity_id=contract.id,
        summary=f"Contract {contract.number} drafted with {vendor.name}: {contract.title}",
        changes={
            "starts_on": data.starts_on,
            "ends_on": data.ends_on,
            "value_cap": str(data.value_cap) if data.value_cap is not None else None,
            "currency": data.currency,
            "categories": contract.categories,
        },
    )
    await session.commit()
    return await _out(session, contract, vendor.name, org.base_currency)


@router.patch("/{contract_id}", response_model=ContractOut)
async def update_contract(
    contract_id: uuid.UUID, data: ContractUpdate, session: DB, principal: Manager
) -> ContractOut:
    contract = await _get(session, principal, contract_id, lock=True)
    if contract.status != ContractState.DRAFT.value:
        raise ConflictError(
            "Only a draft contract can be edited; terminate it and draft a new one",
            details={"reason": "contract_not_draft", "status": contract.status},
        )
    fields = data.model_dump(exclude_unset=True)
    if "categories" in fields:
        fields["categories"] = sorted({c.value for c in data.categories or []})
    if "title" in fields and fields["title"] is not None:
        fields["title"] = fields["title"].strip()
    if fields.get("currency") is None:
        fields.pop("currency", None)
    for key in ("title", "starts_on", "ends_on"):
        if key in fields and fields[key] is None:
            fields.pop(key)
    validate_terms(
        fields.get("starts_on", contract.starts_on),
        fields.get("ends_on", contract.ends_on),
        fields.get("value_cap", contract.value_cap),
    )
    before = {k: getattr(contract, k) for k in fields}
    for key, value in fields.items():
        setattr(contract, key, value)
    if fields:
        await audit.record(
            session,
            org_id=contract.org_id,
            actor=principal,
            action="contract.updated",
            entity_type="contract",
            entity_id=contract.id,
            summary=f"Contract {contract.number} draft updated: {', '.join(sorted(fields))}",
            changes={k: {"before": str(before[k]), "after": str(v)} for k, v in fields.items()},
        )
    await session.commit()
    vendor = await _vendor(session, principal, contract.vendor_id)
    return await _out(session, contract, vendor.name, (await _org(session, principal)).base_currency)


@router.post("/{contract_id}/activate", response_model=ContractOut)
async def activate_contract(contract_id: uuid.UUID, session: DB, principal: Manager) -> ContractOut:
    org = await _org(session, principal)
    contract = await _get(session, principal, contract_id, lock=True)
    target = transition(ContractState(contract.status), ContractAction.ACTIVATE)
    if contract.ends_on < local_today(org.timezone):
        raise BusinessRuleViolation(
            "This contract's end date has passed; correct the dates first",
            details={"reason": "contract_already_ended"},
        )
    vendor = await _vendor(session, principal, contract.vendor_id)
    contract.status = target.value
    await audit.record(
        session,
        org_id=contract.org_id,
        actor=principal,
        action="contract.activated",
        entity_type="contract",
        entity_id=contract.id,
        summary=f"Contract {contract.number} with {vendor.name} activated "
        f"({contract.starts_on.isoformat()} – {contract.ends_on.isoformat()})",
        changes={"status": {"before": ContractState.DRAFT.value, "after": target.value}},
    )
    await session.commit()
    return await _out(session, contract, vendor.name, org.base_currency)


@router.post("/{contract_id}/terminate", response_model=ContractOut)
async def terminate_contract(
    contract_id: uuid.UUID, data: TerminateIn, session: DB, principal: Manager
) -> ContractOut:
    contract = await _get(session, principal, contract_id, lock=True)
    before = ContractState(contract.status)
    contract.status = transition(before, ContractAction.TERMINATE).value
    contract.terminated_reason = data.reason.strip()
    await audit.record(
        session,
        org_id=contract.org_id,
        actor=principal,
        action="contract.terminated",
        entity_type="contract",
        entity_id=contract.id,
        summary=f"Contract {contract.number} terminated: {contract.terminated_reason}",
        changes={"status": {"before": before.value, "after": contract.status}},
    )
    await session.commit()
    vendor = await _vendor(session, principal, contract.vendor_id)
    return await _out(session, contract, vendor.name, (await _org(session, principal)).base_currency)


@router.post("/{contract_id}/document", response_model=ContractOut)
async def upload_contract_document(
    contract_id: uuid.UUID, data: ContractDocumentIn, session: DB, principal: Manager
) -> ContractOut:
    """Attach the signed contract (PDF/PNG/JPEG, same checks as invoice files).

    A draft's file can be replaced; once the contract is active its file is fixed (it can be attached
    if missing). With ClamAV on, the file is scanned before it is stored and refused if infected.
    """
    settings = get_settings()
    contract = await _get(session, principal, contract_id, lock=True)
    if contract.status != ContractState.DRAFT.value and (
        contract.document_object_key or contract.status != ContractState.ACTIVE.value
    ):
        raise ConflictError(
            "The signed file of an active or ended contract cannot be replaced",
            details={"reason": "contract_document_locked"},
        )
    if settings.env == "prod" and settings.storage_backend == "local":
        raise BusinessRuleViolation(
            "File storage is not configured (set PROCURAX_STORAGE_BACKEND=s3)",
            details={"reason": "storage_not_configured"},
        )
    try:
        content = base64.b64decode(data.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise BusinessRuleViolation(
            "content_base64 must be valid base64", details={"reason": "document_encoding"}
        ) from exc
    try:
        facts = inspect_document(content, data.filename)
    except DocumentRejected as exc:
        raise BusinessRuleViolation(str(exc), details={"reason": exc.reason}) from exc
    scan_status = ScanStatus.NOT_SCANNED
    if settings.clamav_enabled:
        try:
            scan_status, detail = await clamd_scanner(settings)(content)
        except (OSError, TimeoutError, ScanError) as exc:
            raise ScannerUnavailableError(
                "The virus scanner is unavailable; try again shortly",
                details={"reason": "scanner_unavailable"},
            ) from exc
        if scan_status == ScanStatus.INFECTED:
            await audit.record(
                session,
                org_id=contract.org_id,
                actor=principal,
                action="contract.document_refused",
                entity_type="contract",
                entity_id=contract.id,
                summary=f"Contract {contract.number}: uploaded file refused by the virus scanner ({detail})",
            )
            await session.commit()
            raise BusinessRuleViolation(
                "The virus scanner flagged this file", details={"reason": "document_infected"}
            )
    key = f"{principal.org_id}/contracts/{contract.id}/{uuid.uuid4()}"
    await get_storage(settings).put(key, content, facts.mime_type)
    contract.document_object_key = key
    contract.document_sha256 = facts.sha256
    contract.document_filename = data.filename.replace("\\", "/").rsplit("/", 1)[-1][:255]
    contract.document_mime_type = facts.mime_type
    contract.document_scan_status = scan_status.value
    await audit.record(
        session,
        org_id=contract.org_id,
        actor=principal,
        action="contract.document_attached",
        entity_type="contract",
        entity_id=contract.id,
        summary=f"Contract {contract.number}: signed file {contract.document_filename} attached",
        changes={"sha256": facts.sha256, "mime_type": facts.mime_type, "scan_status": scan_status.value},
    )
    await session.commit()
    vendor = await _vendor(session, principal, contract.vendor_id)
    return await _out(session, contract, vendor.name, (await _org(session, principal)).base_currency)


@router.get("/{contract_id}/document", response_model=None)
async def download_contract_document(
    contract_id: uuid.UUID,
    session: DB,
    principal: Reader,
    mode: Annotated[Literal["redirect", "link"], Query()] = "redirect",
) -> Response:
    contract = await _get(session, principal, contract_id)
    if not contract.document_object_key:
        raise NotFoundError("This contract has no signed file")
    filename = contract.document_filename or "contract"
    mime = contract.document_mime_type or "application/octet-stream"
    storage = get_storage()
    url = await storage.presigned_url(contract.document_object_key, filename=filename, content_type=mime)
    if url is not None and mode == "link":
        return JSONResponse(
            {"url": url, "expires_in": get_settings().s3_presign_seconds},
            headers={"Cache-Control": "no-store"},
        )
    if url is not None:
        return RedirectResponse(
            url, status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Cache-Control": "no-store"}
        )
    content = await storage.get(contract.document_object_key)
    if hashlib.sha256(content).hexdigest() != contract.document_sha256:
        raise ConflictError(
            "The stored file does not match its recorded hash", details={"reason": "document_integrity"}
        )
    return Response(
        content,
        media_type=mime,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
