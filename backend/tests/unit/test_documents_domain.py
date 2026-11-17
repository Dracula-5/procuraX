"""Invoice-file rules, storage backends and the clamd reply parser."""

import asyncio
import io
import os
from datetime import date

import pytest
from pypdf import PdfWriter

from app.core.config import get_settings
from app.core.storage import LocalStorage, ObjectExistsError, S3Storage, StorageError
from app.domain.documents import (
    MAX_DOCUMENT_BYTES,
    MAX_PDF_PAGES,
    DocumentRejected,
    ScanStatus,
    inspect_document,
    payment_block_reason,
    retain_until,
)
from app.modules.documents.scanner import ScanError, parse_clamd_reply

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def pdf(pages: int = 1, *, password: str | None = None) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    if password:
        writer.encrypt(password)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _reason(content: bytes, name: str) -> str:
    with pytest.raises(DocumentRejected) as exc:
        inspect_document(content, name)
    return exc.value.reason


def test_accepted_files_are_described_by_type_size_hash_and_pages() -> None:
    facts = inspect_document(pdf(3), "Invoice.PDF")
    assert facts.mime_type == "application/pdf" and facts.page_count == 3
    assert len(facts.sha256) == 64 and facts.size_bytes > 0
    assert inspect_document(PNG, "scan.png").page_count == 1
    assert inspect_document(b"II*\x00rest", "scan.tiff").mime_type == "image/tiff"


@pytest.mark.parametrize(
    ("content", "name", "reason"),
    [
        (b"", "a.pdf", "document_empty"),
        (b"%PDF-" + b"0" * MAX_DOCUMENT_BYTES, "a.pdf", "document_too_large"),
        (PNG, "a.exe", "document_type"),
        (PNG, "no-extension", "document_type"),
        (PNG, "renamed.pdf", "document_signature"),  # PNG bytes with a PDF name
        (b"%PDF-1.7 broken", "a.pdf", "document_unreadable"),
    ],
    ids=["empty", "too-large", "bad-extension", "no-extension", "signature-mismatch", "unreadable-pdf"],
)
def test_invalid_files_are_rejected_with_a_reason(content: bytes, name: str, reason: str) -> None:
    assert _reason(content, name) == reason


def test_page_and_encryption_limits() -> None:
    assert inspect_document(pdf(MAX_PDF_PAGES), "ok.pdf").page_count == MAX_PDF_PAGES
    assert _reason(pdf(MAX_PDF_PAGES + 1), "long.pdf") == "document_pages"
    assert _reason(pdf(1, password="secret"), "locked.pdf") == "document_encrypted"


def test_retention_date() -> None:
    assert retain_until(date(2026, 11, 13), 7) == date(2033, 11, 13)
    assert retain_until(date(2028, 2, 29), 7) == date(2035, 2, 28)


@pytest.mark.parametrize(
    ("statuses", "allow", "expected"),
    [
        ([], False, None),  # no attached files: unchanged behaviour
        (["clean"], False, None),
        (["clean", "infected"], True, "document_infected"),
        (["pending"], True, "document_scan_pending"),
        (["not_scanned"], False, "document_not_scanned"),
        (["not_scanned", "clean"], True, None),
    ],
)
def test_when_attached_files_block_payment(statuses: list[str], allow: bool, expected: str | None) -> None:
    assert payment_block_reason(statuses, allow_unscanned=allow) == expected


def test_clamd_replies() -> None:
    assert parse_clamd_reply("stream: OK\0") == (ScanStatus.CLEAN, "ClamAV: no threat found")
    assert parse_clamd_reply("stream: Win.Test.EICAR_HDB-1 FOUND\0") == (
        ScanStatus.INFECTED,
        "ClamAV: Win.Test.EICAR_HDB-1",
    )
    with pytest.raises(ScanError):
        parse_clamd_reply("INSTREAM size limit exceeded. ERROR\0")


async def test_local_storage_is_write_once_and_confined(tmp_path) -> None:  # noqa: ANN001
    store = LocalStorage(tmp_path)
    await store.put("org/invoices/2026/11/doc", b"first", "application/pdf")
    assert await store.get("org/invoices/2026/11/doc") == b"first"
    with pytest.raises(ObjectExistsError):
        await store.put("org/invoices/2026/11/doc", b"second", "application/pdf")
    assert await store.get("org/invoices/2026/11/doc") == b"first"
    for key in ("../escape", "/abs", "a/../../b", "", "a//b"):
        with pytest.raises(StorageError):
            await store.put(key, b"x", "application/pdf")
    assert (
        await store.presigned_url(
            "org/invoices/2026/11/doc", filename="a.pdf", content_type="application/pdf"
        )
        is None
    )


S3_ENDPOINT = os.environ.get("PROCURAX_TEST_S3_ENDPOINT")


@pytest.mark.skipif(
    not S3_ENDPOINT, reason="set PROCURAX_TEST_S3_ENDPOINT to an S3-compatible server (RustFS in CI)"
)
async def test_s3_storage_against_a_real_s3_compatible_server() -> None:
    import urllib.request
    import uuid

    settings = get_settings().model_copy(
        update={
            "storage_backend": "s3",
            "s3_endpoint_url": S3_ENDPOINT,
            "s3_bucket": "procurax-test",
            "s3_access_key_id": os.environ.get("PROCURAX_TEST_S3_ACCESS_KEY", "procurax"),
            "s3_secret_access_key": os.environ.get("PROCURAX_TEST_S3_SECRET_KEY", "procurax-dev-secret"),
        }
    )
    store = S3Storage(settings)
    store.ensure_bucket()
    key = f"org/invoices/{uuid.uuid4()}"
    await store.put(key, b"%PDF- stored", "application/pdf")
    assert await store.get(key) == b"%PDF- stored"
    with pytest.raises(ObjectExistsError):  # conditional write: never overwritten
        await store.put(key, b"tampered", "application/pdf")
    url = await store.presigned_url(key, filename="請求書.pdf", content_type="application/pdf")
    assert url is not None and "X-Amz-Signature" in url

    def fetch() -> tuple[bytes, str]:
        with urllib.request.urlopen(url) as response:  # noqa: S310 - local test server
            return response.read(), response.headers["Content-Disposition"]

    body, disposition = await asyncio.to_thread(fetch)
    assert body == b"%PDF- stored"
    assert "attachment" in disposition and "UTF-8''%E8%AB%8B%E6%B1%82%E6%9B%B8.pdf" in disposition
