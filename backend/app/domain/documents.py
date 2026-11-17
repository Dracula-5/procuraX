"""Invoice document rules: what may be stored, how it is identified, and when an invoice may be paid.

Pure functions, no I/O. The same type, magic-byte, size and page limits apply to OCR previews and to
stored invoice files. A stored file is identified by its SHA-256, so any later change to the bytes is
detectable, and the retention date is fixed when it is stored.
"""

import hashlib
import io
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

MAX_DOCUMENT_BYTES = 18 * 1024 * 1024
MAX_PDF_PAGES = 10

_SIGNATURES: dict[str, tuple[str, tuple[bytes, ...]]] = {
    ".pdf": ("application/pdf", (b"%PDF-",)),
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".tif": ("image/tiff", (b"II*\x00", b"MM\x00*")),
    ".tiff": ("image/tiff", (b"II*\x00", b"MM\x00*")),
}


class ScanStatus(StrEnum):
    PENDING = "pending"  # waiting for the virus scanner
    CLEAN = "clean"
    INFECTED = "infected"
    NOT_SCANNED = "not_scanned"  # scanning is switched off for this deployment


class DocumentRejected(ValueError):
    """The file cannot be stored. `reason` is a stable code for the UI."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class DocumentFacts:
    mime_type: str
    size_bytes: int
    sha256: str
    page_count: int


def _pdf_pages(content: bytes) -> int:
    from pypdf import PdfReader  # parsed in memory; no files touched
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise DocumentRejected(
                "document_encrypted", "Encrypted PDFs cannot be stored; send an unprotected copy"
            )
        return len(reader.pages)
    except DocumentRejected:
        raise
    except (PdfReadError, ValueError, KeyError, OSError) as exc:
        raise DocumentRejected("document_unreadable", "The PDF could not be read") from exc


def inspect_document(content: bytes, filename: str) -> DocumentFacts:
    """Validate a file against the invoice-document limits and describe it, or raise DocumentRejected."""
    if not content:
        raise DocumentRejected("document_empty", "The file is empty")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise DocumentRejected("document_too_large", "Invoice documents must be no larger than 18 MiB")
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in _SIGNATURES:
        raise DocumentRejected("document_type", "Supported invoice files are PDF, PNG, JPEG and TIFF")
    mime_type, signatures = _SIGNATURES[suffix]
    if not content.startswith(signatures):
        raise DocumentRejected(
            "document_signature", f"File content does not match the {suffix[1:].upper()} extension"
        )
    pages = _pdf_pages(content) if mime_type == "application/pdf" else 1
    if pages < 1 or pages > MAX_PDF_PAGES:
        raise DocumentRejected("document_pages", f"Invoice PDFs must have 1 to {MAX_PDF_PAGES} pages")
    return DocumentFacts(mime_type, len(content), hashlib.sha256(content).hexdigest(), pages)


def retain_until(stored_on: date, years: int) -> date:
    """End of the retention period (e.g. 7 years for invoices under Japanese corporate tax law)."""
    try:
        return stored_on.replace(year=stored_on.year + years)
    except ValueError:  # 29 February
        return stored_on.replace(year=stored_on.year + years, day=28)


def payment_block_reason(statuses: list[str], *, allow_unscanned: bool) -> str | None:
    """Why an invoice with these attached files may not be paid yet, or None.

    Infected files always block. A file still waiting for the scanner blocks. A file that was not scanned
    because scanning is off blocks unless the deployment explicitly allows unscanned documents.
    """
    if ScanStatus.INFECTED in statuses:
        return "document_infected"
    if ScanStatus.PENDING in statuses:
        return "document_scan_pending"
    if ScanStatus.NOT_SCANNED in statuses and not allow_unscanned:
        return "document_not_scanned"
    return None
