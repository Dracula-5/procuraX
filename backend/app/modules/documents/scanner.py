"""Virus scanning of stored invoice files with ClamAV (clamd), run by the worker.

Uses clamd's INSTREAM command over TCP (no extra dependency). Files are claimed with
`FOR UPDATE SKIP LOCKED`; each scan also re-hashes the stored object, so a file changed in storage after
upload is caught. A file is only ever marked clean by an actual scan; scanner outages leave it pending
(which blocks payment) and retry with backoff.
"""

import asyncio
import hashlib
import logging
import struct
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.config import Settings, get_settings
from app.core.db import system_session, utcnow
from app.core.storage import ObjectStorage, get_storage
from app.domain.documents import ScanStatus
from app.domain.notifications import backoff
from app.modules.documents.models import InvoiceDocument

logger = logging.getLogger("procurax.documents")
CHUNK = 64 * 1024

Scanner = Callable[[bytes], Awaitable[tuple[ScanStatus, str]]]


class ScanError(RuntimeError):
    pass


def parse_clamd_reply(reply: str) -> tuple[ScanStatus, str]:
    """`stream: OK` → clean; `stream: <signature> FOUND` → infected; anything else is an error."""
    text = reply.strip().rstrip("\0").strip()
    if text.endswith(" OK") or text == "OK":
        return ScanStatus.CLEAN, "ClamAV: no threat found"
    if text.endswith(" FOUND"):
        signature = text.removesuffix(" FOUND").split(":", 1)[-1].strip()
        return ScanStatus.INFECTED, f"ClamAV: {signature}"
    raise ScanError(f"Unexpected clamd reply: {text[:200]}")


def clamd_scanner(settings: Settings) -> Scanner:
    async def scan(data: bytes) -> tuple[ScanStatus, str]:
        timeout = settings.clamav_timeout_seconds
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.clamav_host, settings.clamav_port), timeout
        )
        try:
            writer.write(b"zINSTREAM\0")
            for start in range(0, len(data), CHUNK):
                chunk = data[start : start + CHUNK]
                writer.write(struct.pack("!I", len(chunk)) + chunk)
            writer.write(struct.pack("!I", 0))
            await writer.drain()
            reply = await asyncio.wait_for(reader.readuntil(b"\0"), timeout)
        finally:
            writer.close()
            await writer.wait_closed()
        return parse_clamd_reply(reply.decode("utf-8", "replace"))

    return scan


async def scan_batch(
    session: AsyncSession,
    settings: Settings,
    storage: ObjectStorage,
    scanner: Scanner | None,
    now: datetime | None = None,
) -> dict[str, int]:
    now = now or utcnow()
    documents = list(
        await session.scalars(
            select(InvoiceDocument)
            .where(
                InvoiceDocument.scan_status == ScanStatus.PENDING.value, InvoiceDocument.next_scan_at <= now
            )
            .order_by(InvoiceDocument.next_scan_at)
            .limit(settings.notification_batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    counts = {"scanned": 0, "infected": 0, "scan_errors": 0}
    for doc in documents:
        if scanner is None:
            # Scanning was switched off after upload: say so instead of pretending the file is clean.
            doc.scan_status = ScanStatus.NOT_SCANNED.value
            doc.scan_detail = "Scanning is disabled (PROCURAX_CLAMAV_ENABLED=false)"
            continue
        doc.scan_attempts += 1
        try:
            data = await storage.get(doc.object_key)
            if hashlib.sha256(data).hexdigest() != doc.sha256:
                raise ScanError("Integrity check failed: the stored object differs from the uploaded file")
            status, detail = await scanner(data)
            doc.scan_status = status.value
            doc.scan_detail = detail
            doc.scanned_at = now
            counts["scanned"] += 1
            metrics.DOCUMENT_SCANS.labels(status.value).inc()
            if status == ScanStatus.INFECTED:
                counts["infected"] += 1
                logger.warning(
                    "invoice_document_infected", extra={"document_id": str(doc.id), "detail": detail}
                )
        except Exception as exc:  # noqa: BLE001 - recorded, retried; the file stays pending (unpayable)
            doc.scan_detail = f"{type(exc).__name__}: {exc}"[:2000]
            doc.next_scan_at = now + backoff(min(doc.scan_attempts, 10), key=str(doc.id))
            counts["scan_errors"] += 1
            metrics.DOCUMENT_SCANS.labels("error").inc()
    await session.commit()
    return counts


async def scan_once(
    settings: Settings | None = None, storage: ObjectStorage | None = None, scanner: Scanner | None = None
) -> dict[str, int]:
    settings = settings or get_settings()
    storage = storage or get_storage(settings)
    if scanner is None and settings.clamav_enabled:
        scanner = clamd_scanner(settings)
    session = system_session()
    try:
        return await scan_batch(session, settings, storage, scanner)
    finally:
        await session.close()
