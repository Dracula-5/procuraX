"""Stored invoice files. The bytes live in object storage; this row records what was stored and where.

Once written, the file's identity (object key, SHA-256, size, type, page count, uploader) never changes,
and once a file is linked to an invoice the link is permanent. A database trigger enforces both
(migration 0015), so even a bug in the application cannot rewrite the evidence. Only the virus-scan
fields change after upload.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantMixin, UUIDPkMixin, utcnow
from app.domain.documents import ScanStatus

SCAN_STATUSES = [s.value for s in ScanStatus]


class InvoiceDocument(UUIDPkMixin, TenantMixin, Base):
    __tablename__ = "invoice_documents"
    __table_args__ = (
        CheckConstraint(
            f"scan_status IN ({', '.join(repr(s) for s in SCAN_STATUSES)})", name="valid_scan_status"
        ),
        CheckConstraint("size_bytes > 0", name="positive_size"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint("(invoice_id IS NULL) = (linked_at IS NULL)", name="link_consistent"),
        UniqueConstraint("object_key"),
        # The worker's scan queue.
        Index("ix_invoice_documents_scan_due", "scan_status", "next_scan_at"),
    )

    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id"), index=True)
    object_key: Mapped[str] = mapped_column(String(300))
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(60))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int] = mapped_column(Integer)
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    retain_until: Mapped[date] = mapped_column(Date)
    scan_status: Mapped[str] = mapped_column(String(16))
    scan_detail: Mapped[str | None] = mapped_column(Text)
    scan_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_scan_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    linked_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
