"""Invoice files: upload, list, link to an invoice (once), and download with a permission check.

Upload writes the bytes to object storage first and the metadata row second, so a row never points at a
missing object (a failed commit can leave an orphaned object, never a dangling row).
"""

import base64
import binascii
import hashlib
import uuid
from datetime import date, datetime
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, requires, requires_any
from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import BusinessRuleViolation, ConflictError, DomainError, NotFoundError
from app.core.principal import Principal
from app.core.storage import get_storage
from app.domain.documents import (
    MAX_DOCUMENT_BYTES,
    DocumentRejected,
    ScanStatus,
    inspect_document,
    retain_until,
)
from app.domain.rbac import Permission
from app.modules.audit import service as audit
from app.modules.documents.models import InvoiceDocument
from app.modules.procurement.records import Invoice

router = APIRouter(tags=["invoice documents"])
Uploader = Annotated[Principal, Depends(requires(Permission.VENDOR_MANAGE))]
Reader = Annotated[Principal, Depends(requires_any(Permission.PR_READ_ALL, Permission.PAYMENT_APPROVE))]


class StorageUnavailableError(DomainError):
    status_code = 503
    code = "storage_unavailable"


class DocumentUploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    # base64 of at most 18 MiB (4/3 expansion plus padding)
    content_base64: str = Field(min_length=4, max_length=(MAX_DOCUMENT_BYTES * 4) // 3 + 8)


class DocumentLinkIn(BaseModel):
    document_id: uuid.UUID


class DocumentOut(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID | None
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    page_count: int
    scan_status: str
    scan_detail: str | None
    retain_until: date
    uploaded_by_id: uuid.UUID
    created_at: datetime
    linked_at: datetime | None


def _out(doc: InvoiceDocument) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        invoice_id=doc.invoice_id,
        original_filename=doc.original_filename,
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        sha256=doc.sha256,
        page_count=doc.page_count,
        scan_status=doc.scan_status,
        scan_detail=doc.scan_detail,
        retain_until=doc.retain_until,
        uploaded_by_id=doc.uploaded_by_id,
        created_at=doc.created_at,
        linked_at=doc.linked_at,
    )


def _refuse(reason: str, message: str) -> BusinessRuleViolation:
    return BusinessRuleViolation(message, details={"reason": reason})


@router.post("/invoice-documents", status_code=status.HTTP_201_CREATED, response_model=DocumentOut)
async def upload_document(data: DocumentUploadIn, session: DB, principal: Uploader) -> DocumentOut:
    settings = get_settings()
    if settings.env == "prod" and settings.storage_backend == "local":
        # A platform container's disk is not durable evidence storage.
        raise StorageUnavailableError(
            "Invoice file storage is not configured (set PROCURAX_STORAGE_BACKEND=s3)",
            details={"reason": "storage_not_configured"},
        )
    try:
        content = base64.b64decode(data.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise _refuse("document_encoding", "content_base64 must be valid base64") from exc
    try:
        facts = inspect_document(content, data.filename)
    except DocumentRejected as exc:
        raise _refuse(exc.reason, str(exc)) from exc

    now = utcnow()
    doc_id = uuid.uuid4()
    key = f"{principal.org_id}/invoices/{now:%Y/%m}/{doc_id}"
    await get_storage(settings).put(key, content, facts.mime_type)
    doc = InvoiceDocument(
        id=doc_id,
        org_id=principal.org_id,
        object_key=key,
        original_filename=data.filename.replace("\\", "/").rsplit("/", 1)[-1][:255],
        mime_type=facts.mime_type,
        size_bytes=facts.size_bytes,
        sha256=facts.sha256,
        page_count=facts.page_count,
        uploaded_by_id=principal.user_id,
        retain_until=retain_until(now.date(), settings.document_retention_years),
        scan_status=(ScanStatus.PENDING if settings.clamav_enabled else ScanStatus.NOT_SCANNED).value,
        scan_detail=None
        if settings.clamav_enabled
        else "Scanning is disabled (PROCURAX_CLAMAV_ENABLED=false)",
        next_scan_at=now,
        created_at=now,
    )
    session.add(doc)
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="invoice_document.uploaded",
        entity_type="invoice_document",
        entity_id=doc.id,
        summary=f"Invoice file {doc.original_filename} stored ({facts.size_bytes} bytes)",
        changes={"sha256": facts.sha256, "mime_type": facts.mime_type, "pages": facts.page_count},
    )
    await session.commit()
    return _out(doc)


@router.get("/invoice-documents", response_model=list[DocumentOut])
async def list_documents(
    session: DB, principal: Reader, invoice_id: Annotated[uuid.UUID | None, Query()] = None
) -> list[DocumentOut]:
    query = select(InvoiceDocument).where(InvoiceDocument.org_id == principal.org_id)
    if invoice_id is not None:
        query = query.where(InvoiceDocument.invoice_id == invoice_id)
    rows = await session.scalars(query.order_by(InvoiceDocument.created_at.desc()).limit(200))
    return [_out(d) for d in rows]


@router.post("/invoices/{invoice_id}/documents", response_model=DocumentOut)
async def link_document(
    invoice_id: uuid.UUID, data: DocumentLinkIn, session: DB, principal: Uploader
) -> DocumentOut:
    invoice = await session.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.org_id == principal.org_id)
    )
    if invoice is None:
        raise NotFoundError("Invoice not found")
    doc = await session.scalar(
        select(InvoiceDocument)
        .where(InvoiceDocument.id == data.document_id, InvoiceDocument.org_id == principal.org_id)
        .with_for_update()
    )
    if doc is None:
        raise NotFoundError("Invoice document not found")
    if doc.invoice_id is not None:
        raise ConflictError(
            "This file is already attached to an invoice", details={"reason": "document_already_linked"}
        )
    if doc.scan_status == ScanStatus.INFECTED:
        raise _refuse("document_infected", "An infected file cannot be attached to an invoice")
    doc.invoice_id = invoice.id
    doc.linked_at = utcnow()
    doc.linked_by_id = principal.user_id
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="invoice_document.linked",
        entity_type="invoice",
        entity_id=invoice.id,
        summary=f"File {doc.original_filename} attached to invoice {invoice.invoice_number}",
        changes={"document_id": str(doc.id), "sha256": doc.sha256},
    )
    await session.commit()
    return _out(doc)


@router.get("/invoice-documents/{document_id}/download", response_model=None)
async def download_document(
    document_id: uuid.UUID,
    session: DB,
    principal: Reader,
    mode: Annotated[Literal["redirect", "link"], Query()] = "redirect",
) -> Response:
    """Permission-checked download. With object storage the response points at a short-lived presigned
    URL (a 307 redirect, or JSON `{"url": ...}` with mode=link for browsers that cannot follow a
    cross-origin redirect); otherwise the bytes are streamed after their SHA-256 is re-checked."""
    doc = await session.scalar(
        select(InvoiceDocument).where(
            InvoiceDocument.id == document_id, InvoiceDocument.org_id == principal.org_id
        )
    )
    if doc is None:
        raise NotFoundError("Invoice document not found")
    if doc.scan_status == ScanStatus.INFECTED:
        raise ConflictError(
            "This file was flagged by the virus scanner", details={"reason": "document_infected"}
        )
    if doc.scan_status == ScanStatus.PENDING:
        raise ConflictError(
            "This file is waiting for its virus scan", details={"reason": "document_scan_pending"}
        )
    storage = get_storage()
    url = await storage.presigned_url(
        doc.object_key, filename=doc.original_filename, content_type=doc.mime_type
    )
    if url is not None and mode == "link":
        return JSONResponse(
            {"url": url, "expires_in": get_settings().s3_presign_seconds},
            headers={"Cache-Control": "no-store"},
        )
    if url is not None:
        return RedirectResponse(
            url, status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Cache-Control": "no-store"}
        )
    content = await storage.get(doc.object_key)
    if hashlib.sha256(content).hexdigest() != doc.sha256:
        raise ConflictError(
            "The stored file does not match its recorded hash", details={"reason": "document_integrity"}
        )
    return Response(
        content,
        media_type=doc.mime_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(doc.original_filename)}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
