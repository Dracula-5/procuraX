"""Stored invoice files: upload, link once, download, virus scan, payment gate, immutability, RLS."""

import asyncio
import base64
import os
import socket
import struct
from datetime import timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.core.db import system_session, utcnow
from app.core.storage import get_storage
from app.modules.documents.scanner import clamd_scanner, scan_batch, scan_once
from tests.api.test_p2p_controls import _approved_request, _invoice, _open_order, _receive
from tests.helpers import API, build_org, create_vendor
from tests.unit.test_documents_domain import pdf

# The standard anti-virus test string (harmless); embedded in an otherwise valid PDF.
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def b64(content: bytes) -> str:
    return base64.b64encode(content).decode()


async def _matched_invoice(client: AsyncClient, org, number: str = "INV-DOC-1") -> dict:  # noqa: ANN001
    vendor = await create_vendor(client, org)
    request = await _approved_request(
        client, org, vendor["id"], [{"description": "Paper", "quantity": "1", "unit_price": "20000"}]
    )
    order = await _open_order(client, org, request["id"])
    await _receive(client, org, order, order["items"][0], "1")
    return await _invoice(
        client,
        org,
        order,
        number,
        items=[{"purchase_order_item_id": order["items"][0]["id"], "quantity": "1", "unit_price": "20000"}],
    )


async def _upload(client: AsyncClient, org, content: bytes, name: str = "請求書.pdf"):  # noqa: ANN001, ANN202
    return await client.post(
        f"{API}/invoice-documents",
        headers=org["procurement"].headers,
        json={"filename": name, "content_base64": b64(content)},
    )


async def _link(client: AsyncClient, org, invoice_id: str, document_id: str):  # noqa: ANN001, ANN202
    return await client.post(
        f"{API}/invoices/{invoice_id}/documents",
        headers=org["procurement"].headers,
        json={"document_id": document_id},
    )


@pytest.fixture
async def fake_clamd():  # noqa: ANN201
    """A clamd stand-in speaking the real INSTREAM protocol; flags the EICAR test string."""
    seen: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert await reader.readexactly(10) == b"zINSTREAM\0"
        data = b""
        while True:
            (size,) = struct.unpack("!I", await reader.readexactly(4))
            if size == 0:
                break
            data += await reader.readexactly(size)
        seen.append(data)
        reply = b"stream: Eicar-Test-Signature FOUND\0" if EICAR in data else b"stream: OK\0"
        writer.write(reply)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        yield port, seen


def _scan_settings(port: int):  # noqa: ANN202
    return get_settings().model_copy(
        update={"clamav_enabled": True, "clamav_port": port, "notification_batch_size": 1000}
    )


async def _scan(port: int) -> dict[str, int]:
    settings = _scan_settings(port)
    return await scan_once(settings, get_storage(settings), clamd_scanner(settings))


async def test_upload_link_once_and_download(client: AsyncClient) -> None:
    org = await build_org(client)
    invoice = await _matched_invoice(client, org)
    content = pdf(2)
    uploaded = await _upload(client, org, content)
    assert uploaded.status_code == 201, uploaded.text
    doc = uploaded.json()
    assert doc["page_count"] == 2 and doc["size_bytes"] == len(content) and doc["invoice_id"] is None
    assert doc["scan_status"] == "not_scanned"  # ClamAV is off by default, and says so
    assert doc["retain_until"] > doc["created_at"][:10]

    linked = await _link(client, org, invoice["id"], doc["id"])
    assert linked.status_code == 200 and linked.json()["invoice_id"] == invoice["id"]
    again = await _link(client, org, (await _matched_invoice(client, org, "INV-DOC-2"))["id"], doc["id"])
    assert (
        again.status_code == 409 and again.json()["error"]["details"]["reason"] == "document_already_linked"
    )

    listed = await client.get(
        f"{API}/invoice-documents", headers=org["finance"].headers, params={"invoice_id": invoice["id"]}
    )
    assert [d["id"] for d in listed.json()] == [doc["id"]]
    download = await client.get(
        f"{API}/invoice-documents/{doc['id']}/download", headers=org["finance"].headers
    )
    assert download.status_code == 200 and download.content == content
    assert download.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''%E8%AB%8B%E6%B1%82%E6%9B%B8.pdf" in download.headers["content-disposition"]
    assert download.headers["x-content-type-options"] == "nosniff"

    # Permission and tenant checks.
    assert (
        await client.get(f"{API}/invoice-documents/{doc['id']}/download", headers=org["employee"].headers)
    ).status_code == 403
    other = await build_org(client, name="Other docs K.K.")
    assert (
        await client.get(f"{API}/invoice-documents/{doc['id']}/download", headers=other["finance"].headers)
    ).status_code == 404
    assert (await _link(client, other, invoice["id"], doc["id"])).status_code == 404


async def test_invalid_uploads_are_refused_with_a_reason(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await build_org(client)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    renamed = await _upload(client, org, png, "invoice.pdf")
    assert renamed.status_code == 422 and renamed.json()["error"]["details"]["reason"] == "document_signature"
    bad = await client.post(
        f"{API}/invoice-documents",
        headers=org["procurement"].headers,
        json={"filename": "a.pdf", "content_base64": "@@@@"},
    )
    assert bad.json()["error"]["details"]["reason"] == "document_encoding"
    assert (await _upload(client, org, pdf(11))).json()["error"]["details"]["reason"] == "document_pages"
    assert (
        await client.post(
            f"{API}/invoice-documents",
            headers=org["employee"].headers,
            json={"filename": "a.pdf", "content_base64": b64(pdf())},
        )
    ).status_code == 403
    # A production deployment on a container's local disk refuses rather than losing evidence.
    monkeypatch.setattr(get_settings(), "env", "prod")
    refused = await _upload(client, org, pdf())
    assert (
        refused.status_code == 503
        and refused.json()["error"]["details"]["reason"] == "storage_not_configured"
    )


async def test_unscanned_files_block_payment_unless_explicitly_allowed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await build_org(client)
    invoice = await _matched_invoice(client, org)
    doc = (await _upload(client, org, pdf())).json()
    await _link(client, org, invoice["id"], doc["id"])
    refused = await client.post(
        f"{API}/invoices/{invoice['id']}/payment-requests", headers=org["procurement"].headers
    )
    assert (
        refused.status_code == 422 and refused.json()["error"]["details"]["reason"] == "document_not_scanned"
    )
    monkeypatch.setattr(get_settings(), "allow_unscanned_documents", True)
    accepted = await client.post(
        f"{API}/invoices/{invoice['id']}/payment-requests", headers=org["procurement"].headers
    )
    assert accepted.status_code == 201, accepted.text


async def test_clamav_scan_marks_clean_and_infected_files(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, fake_clamd
) -> None:  # noqa: ANN001
    port, seen = fake_clamd
    monkeypatch.setattr(get_settings(), "clamav_enabled", True)
    org = await build_org(client)
    invoice = await _matched_invoice(client, org)
    clean = (await _upload(client, org, pdf(), "clean.pdf")).json()
    infected_bytes = pdf() + b"\n%" + EICAR + b"\n"
    infected = (await _upload(client, org, infected_bytes, "eicar.pdf")).json()
    assert clean["scan_status"] == infected["scan_status"] == "pending"

    await _link(client, org, invoice["id"], clean["id"])
    pending = await client.post(
        f"{API}/invoices/{invoice['id']}/payment-requests", headers=org["procurement"].headers
    )
    assert pending.json()["error"]["details"]["reason"] == "document_scan_pending"
    blocked = await client.get(
        f"{API}/invoice-documents/{clean['id']}/download", headers=org["finance"].headers
    )
    assert (
        blocked.status_code == 409 and blocked.json()["error"]["details"]["reason"] == "document_scan_pending"
    )

    counts = await _scan(port)
    assert counts["scanned"] >= 2 and counts["infected"] >= 1
    assert infected_bytes in seen
    by_id = {
        d["id"]: d
        for d in (await client.get(f"{API}/invoice-documents", headers=org["finance"].headers)).json()
    }
    assert by_id[clean["id"]]["scan_status"] == "clean"
    assert by_id[infected["id"]]["scan_status"] == "infected"
    assert "Eicar-Test-Signature" in by_id[infected["id"]]["scan_detail"]

    refused = await _link(client, org, invoice["id"], infected["id"])
    assert refused.json()["error"]["details"]["reason"] == "document_infected"
    assert (
        await client.get(f"{API}/invoice-documents/{infected['id']}/download", headers=org["finance"].headers)
    ).status_code == 409
    paid = await client.post(
        f"{API}/invoices/{invoice['id']}/payment-requests", headers=org["procurement"].headers
    )
    assert paid.status_code == 201, paid.text


async def test_a_file_linked_after_the_request_must_be_clean_before_approval(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, fake_clamd
) -> None:  # noqa: ANN001
    port, _ = fake_clamd
    org = await build_org(client)
    invoice = await _matched_invoice(client, org)
    payment = await client.post(
        f"{API}/invoices/{invoice['id']}/payment-requests", headers=org["procurement"].headers
    )
    assert payment.status_code == 201  # no files attached: unchanged behaviour
    monkeypatch.setattr(get_settings(), "clamav_enabled", True)
    doc = (await _upload(client, org, pdf() + b"\n%" + EICAR)).json()
    await _link(client, org, invoice["id"], doc["id"])  # linked while still pending
    await _scan(port)
    decided = await client.post(
        f"{API}/payments/{payment.json()['id']}/decision",
        headers=org["finance"].headers,
        json={"decision": "approve", "comment": "Reviewed and approved"},
    )
    assert decided.status_code == 422 and decided.json()["error"]["details"]["reason"] == "document_infected"


async def test_scanner_outage_and_tampering_never_produce_clean(
    client: AsyncClient, admin_engine, monkeypatch: pytest.MonkeyPatch, fake_clamd
) -> None:  # noqa: ANN001
    port, _ = fake_clamd
    monkeypatch.setattr(get_settings(), "clamav_enabled", True)
    org = await build_org(client)
    doc = (await _upload(client, org, pdf())).json()

    # clamd unreachable: stays pending, backs off, records why.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    await _scan(dead_port)
    async with admin_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT scan_status, scan_detail, next_scan_at FROM invoice_documents WHERE id = :id"),
                {"id": doc["id"]},
            )
        ).one()
    assert row.scan_status == "pending" and row.scan_detail and row.next_scan_at > utcnow()

    # The stored object is changed behind the application's back: the hash check catches it.
    async with admin_engine.connect() as conn:
        key = (
            await conn.execute(
                text("SELECT object_key FROM invoice_documents WHERE id = :id"), {"id": doc["id"]}
            )
        ).scalar_one()
    path = Path(get_settings().storage_local_dir) / key
    path.write_bytes(path.read_bytes() + b"tampered")
    settings = _scan_settings(port)
    session = system_session()
    try:
        await scan_batch(
            session,
            settings,
            get_storage(settings),
            clamd_scanner(settings),
            now=utcnow() + timedelta(days=1),
        )
    finally:
        await session.close()
    async with admin_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT scan_status, scan_detail FROM invoice_documents WHERE id = :id"),
                {"id": doc["id"]},
            )
        ).one()
    assert row.scan_status == "pending" and "Integrity check failed" in row.scan_detail


async def test_stored_file_records_are_immutable_and_undeletable(
    client: AsyncClient, admin_engine, app_engine
) -> None:
    org = await build_org(client)
    invoice = await _matched_invoice(client, org)
    doc = (await _upload(client, org, pdf())).json()
    await _link(client, org, invoice["id"], doc["id"])
    other = await _matched_invoice(client, org, "INV-DOC-9")

    for statement, params in [
        ("UPDATE invoice_documents SET sha256 = repeat('0', 64) WHERE id = :id", {}),
        ("UPDATE invoice_documents SET object_key = 'x/y' WHERE id = :id", {}),
        ("UPDATE invoice_documents SET invoice_id = :other WHERE id = :id", {"other": other["id"]}),
        ("DELETE FROM invoice_documents WHERE id = :id", {}),
    ]:
        # Even the owner role cannot rewrite or delete a linked file record.
        with pytest.raises(DBAPIError, match="immutable|already linked|cannot be deleted"):
            async with admin_engine.begin() as conn:
                await conn.execute(text(statement), {"id": doc["id"], **params})
    # The runtime role has no DELETE privilege at all.
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": org.org_id})
            await conn.execute(text("DELETE FROM invoice_documents WHERE id = :id"), {"id": doc["id"]})
    # Scan results may still be recorded.
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE invoice_documents SET scan_status = 'clean' WHERE id = :id"), {"id": doc["id"]}
        )


async def test_invoice_documents_are_isolated_by_rls(client: AsyncClient, app_engine) -> None:
    a = await build_org(client, name="Docs A K.K.")
    b = await build_org(client, name="Docs B K.K.")
    await _upload(client, a, pdf())
    await _upload(client, b, pdf())
    async with app_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": a.org_id})
            orgs = {
                str(r[0]) for r in await conn.execute(text("SELECT DISTINCT org_id FROM invoice_documents"))
            }
            assert orgs == {a.org_id}
        async with conn.begin():
            assert (await conn.execute(text("SELECT count(*) FROM invoice_documents"))).scalar() == 0
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": a.org_id})
            await conn.execute(
                text(
                    "INSERT INTO invoice_documents (id, org_id, object_key, original_filename, mime_type, "
                    "size_bytes, sha256, page_count, uploaded_by_id, retain_until, scan_status, scan_attempts, "
                    "next_scan_at, created_at) "
                    "VALUES (gen_random_uuid(), :org, :key, 'x.pdf', 'application/pdf', 1, repeat('a', 64), 1, :user, "
                    "current_date, 'pending', 0, now(), now())"
                ),
                {"org": b.org_id, "key": "b/x", "user": b["procurement"].user_id},
            )


CLAMAV_PORT = os.environ.get("PROCURAX_TEST_CLAMAV_PORT")


@pytest.mark.skipif(
    not CLAMAV_PORT, reason="set PROCURAX_TEST_CLAMAV_PORT to a running clamd (docker compose --profile scan)"
)
async def test_real_clamav_scans_an_uploaded_invoice(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upload → worker scan with a real clamd → clean. (Real ClamAV only matches EICAR at the start of a
    file, which no valid PDF/image can do, so the infected path is covered by the protocol stand-in.)"""
    monkeypatch.setattr(get_settings(), "clamav_enabled", True)
    org = await build_org(client)
    doc = (await _upload(client, org, pdf(2))).json()
    settings = get_settings().model_copy(
        update={"clamav_port": int(CLAMAV_PORT or 0), "clamav_timeout_seconds": 120}
    )
    await scan_once(settings, get_storage(settings), clamd_scanner(settings))
    listed = {
        d["id"]: d
        for d in (await client.get(f"{API}/invoice-documents", headers=org["finance"].headers)).json()
    }
    assert listed[doc["id"]]["scan_status"] == "clean"
    assert listed[doc["id"]]["scan_detail"] == "ClamAV: no threat found"
