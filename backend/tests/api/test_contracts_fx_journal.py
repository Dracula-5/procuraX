"""Contracts (lifecycle, policy effect, sweep, signed file), multi-currency requests and journal export."""

import base64
import csv
import io
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.core.db import system_session, utcnow
from app.integrations.ecb import parse_daily
from app.modules.contracts.service import sweep
from tests.api.test_p2p_controls import _approved_request, _invoice, _open_order, _receive
from tests.api.test_payment_export import BANK, _approved_payment
from tests.helpers import API, Org, add_user, build_org, create_vendor
from tests.unit.test_documents_domain import pdf

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TODAY = date.today()


async def _contract(client: AsyncClient, org: Org, vendor_id: str, **body: object) -> dict:
    payload = {
        "vendor_id": vendor_id,
        "title": "Software framework agreement",
        "categories": ["software"],
        "starts_on": (TODAY - timedelta(days=10)).isoformat(),
        "ends_on": (TODAY + timedelta(days=120)).isoformat(),
        "value_cap": "500000",
        "currency": "JPY",
        **body,
    }
    r = await client.post(f"{API}/contracts", headers=org["procurement"].headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def _submitted(client: AsyncClient, org: Org, vendor_id: str, amount: str, **body: object) -> dict:
    created = await client.post(
        f"{API}/purchase-requests",
        headers=org["employee"].headers,
        json={
            "title": "Licences",
            "category": "software",
            "cost_center_id": org.cost_center_id,
            "justification": "Seats for the new team",
            "preferred_vendor_id": vendor_id,
            "items": [{"description": "Licence", "quantity": "1", "unit_price": amount}],
            **body,
        },
    )
    assert created.status_code == 201, created.text
    r = await client.patch(
        f"{API}/purchase-requests/{created.json()['id']}/submit", headers=org["employee"].headers
    )
    assert r.status_code == 200, r.text
    return r.json()


def _rules(pr: dict) -> set[str]:
    return {h["rule_id"] for h in pr["policy_evaluation"]["hits"]}


# --- Contracts ------------------------------------------------------------------------------------


async def test_contract_lifecycle_drives_vendor_contract_rules(client: AsyncClient) -> None:
    org = await build_org(client)
    vendor = await create_vendor(client, org, categories=("software",), contract_status="none")

    denied = await client.post(
        f"{API}/contracts",
        headers=org["employee"].headers,
        json={
            "vendor_id": vendor["id"],
            "title": "x" * 5,
            "starts_on": "2026-01-01",
            "ends_on": "2026-12-31",
            "currency": "JPY",
        },
    )
    assert denied.status_code == 403
    backwards = await client.post(
        f"{API}/contracts",
        headers=org["procurement"].headers,
        json={
            "vendor_id": vendor["id"],
            "title": "Backwards",
            "starts_on": "2026-12-31",
            "ends_on": "2026-01-01",
            "currency": "JPY",
        },
    )
    assert backwards.status_code == 422 and backwards.json()["error"]["details"]["reason"] == "contract_dates"

    draft = await _contract(client, org, vendor["id"])
    assert draft["status"] == "draft" and draft["number"].startswith("CT-")
    assert draft["allowed_actions"] == ["activate", "edit", "terminate"]

    # A draft is not a contract yet: the legacy vendor field ("none") decides → VEN-004.
    first = await _submitted(client, org, vendor["id"], "300000")
    assert "VEN-004" in _rules(first) and "CON-001" not in _rules(first)

    edited = await client.patch(
        f"{API}/contracts/{draft['id']}",
        headers=org["procurement"].headers,
        json={"title": "Renamed agreement"},
    )
    assert edited.status_code == 200 and edited.json()["title"] == "Renamed agreement"
    active = await client.post(f"{API}/contracts/{draft['id']}/activate", headers=org["procurement"].headers)
    assert active.status_code == 200, active.text
    assert active.json()["status"] == "active"
    locked = await client.patch(
        f"{API}/contracts/{draft['id']}", headers=org["procurement"].headers, json={"title": "Too late"}
    )
    assert locked.status_code == 409 and locked.json()["error"]["details"]["reason"] == "contract_not_draft"

    # Covered by the active contract: no VEN-004; the request counts against the cap.
    second = await _submitted(client, org, vendor["id"], "300000")
    assert "VEN-004" not in _rules(second) and "CON-001" not in _rules(second)
    assert second["contract_number"] == draft["number"]
    listed = await client.get(
        f"{API}/contracts", headers=org["employee"].headers, params={"vendor_id": vendor["id"]}
    )
    assert listed.status_code == 200
    assert Decimal(listed.json()[0]["committed"]) == Decimal("300000")

    # ¥300,000 committed + ¥300,000 > ¥500,000 cap → CON-001 routes procurement.
    third = await _submitted(client, org, vendor["id"], "300000")
    assert "CON-001" in _rules(third)
    assert "procurement_officer" in {s["approver_role"] for s in third["approvals"]}

    terminated = await client.post(
        f"{API}/contracts/{draft['id']}/terminate",
        headers=org["procurement"].headers,
        json={"reason": "Supplier exited the market"},
    )
    assert terminated.status_code == 200 and terminated.json()["status"] == "terminated"
    again = await client.post(f"{API}/contracts/{draft['id']}/activate", headers=org["procurement"].headers)
    assert again.status_code == 409
    fourth = await _submitted(client, org, vendor["id"], "100000")
    assert "VEN-004" in _rules(fourth)
    hit = next(h for h in fourth["policy_evaluation"]["hits"] if h["rule_id"] == "VEN-004")
    assert hit["evidence"]["contract_status"] == "expired"

    trail = (
        await client.get(f"{API}/audit-logs", headers=org.admin.headers, params={"entity_type": "contract"})
    ).json()
    actions = {e["action"] for e in (trail["items"] if isinstance(trail, dict) else trail)}
    assert {"contract.created", "contract.updated", "contract.activated", "contract.terminated"} <= actions


async def test_contract_sweep_reminds_once_then_expires(client: AsyncClient, admin_engine) -> None:  # noqa: ANN001
    org = await build_org(client)
    vendor = await create_vendor(client, org, categories=("software",))
    contract = await _contract(client, org, vendor["id"], ends_on=(TODAY + timedelta(days=20)).isoformat())
    await client.post(f"{API}/contracts/{contract['id']}/activate", headers=org["procurement"].headers)

    for _ in range(2):  # the second run must not remind again
        session = system_session()
        try:
            await sweep(session)
        finally:
            await session.close()
    async with admin_engine.connect() as conn:
        events = (
            await conn.execute(
                text(
                    "SELECT payload FROM notification_outbox "
                    "WHERE aggregate_id = :id AND event_type = 'contract.expiring'"
                ),
                {"id": contract["id"]},
            )
        ).all()
    assert len(events) == 1
    assert events[0][0]["contract_number"] == contract["number"]
    assert events[0][0]["recipient_roles"] == ["procurement_officer"]

    session = system_session()
    try:
        await sweep(session, now=utcnow() + timedelta(days=22))
    finally:
        await session.close()
    after = (await client.get(f"{API}/contracts/{contract['id']}", headers=org["procurement"].headers)).json()
    assert after["status"] == "expired"
    assert after["allowed_actions"] == []


async def test_contract_signed_file_upload_and_download(client: AsyncClient) -> None:
    org = await build_org(client)
    vendor = await create_vendor(client, org, categories=("software",))
    contract = await _contract(client, org, vendor["id"])
    content = pdf()
    body = {"filename": "signed.pdf", "content_base64": base64.b64encode(content).decode()}
    bad = await client.post(
        f"{API}/contracts/{contract['id']}/document",
        headers=org["procurement"].headers,
        json={"filename": "signed.exe", "content_base64": base64.b64encode(b"MZ" + b"\0" * 64).decode()},
    )
    assert bad.status_code == 422
    stored = await client.post(
        f"{API}/contracts/{contract['id']}/document", headers=org["procurement"].headers, json=body
    )
    assert stored.status_code == 200, stored.text
    document = stored.json()["document"]
    assert document["filename"] == "signed.pdf" and document["scan_status"] == "not_scanned"
    await client.post(f"{API}/contracts/{contract['id']}/activate", headers=org["procurement"].headers)
    replace = await client.post(
        f"{API}/contracts/{contract['id']}/document", headers=org["procurement"].headers, json=body
    )
    assert (
        replace.status_code == 409
        and replace.json()["error"]["details"]["reason"] == "contract_document_locked"
    )
    download = await client.get(f"{API}/contracts/{contract['id']}/document", headers=org["employee"].headers)
    if download.status_code == 307:  # object storage: a presigned URL
        assert "X-Amz-Signature" in download.headers["location"]
    else:
        assert download.status_code == 200 and download.content == content


# --- Exchange rates and multi-currency requests ---------------------------------------------------


async def test_foreign_currency_request_uses_the_rate_snapshotted_at_submission(client: AsyncClient) -> None:
    org = await build_org(client)
    vendor = await create_vendor(client, org, categories=("office_supplies",))

    async def usd_request() -> dict:
        created = await client.post(
            f"{API}/purchase-requests",
            headers=org["employee"].headers,
            json={
                "title": "US supplier order",
                "category": "office_supplies",
                "cost_center_id": org.cost_center_id,
                "preferred_vendor_id": vendor["id"],
                "currency": "usd",
                "items": [{"description": "Adapters", "quantity": "4", "unit_price": "250.00"}],
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["currency"] == "USD" and created.json()["base_amount"] is None
        r = await client.patch(
            f"{API}/purchase-requests/{created.json()['id']}/submit", headers=org["employee"].headers
        )
        assert r.status_code == 200, r.text
        return r.json()

    unsupported = await client.post(
        f"{API}/purchase-requests",
        headers=org["employee"].headers,
        json={"title": "Odd currency", "category": "office_supplies", "currency": "XYZ", "items": []},
    )
    assert unsupported.status_code == 422

    blocked = await usd_request()
    assert blocked["status"] == "policy_blocked" and "VAL-004" in _rules(blocked)

    rate = {"currency": "USD", "rate": "150.25", "valid_on": TODAY.isoformat()}
    assert (
        await client.put(f"{API}/fx/rates", headers=org["employee"].headers, json=rate)
    ).status_code == 403
    future = await client.put(
        f"{API}/fx/rates",
        headers=org["finance"].headers,
        json={**rate, "valid_on": (TODAY + timedelta(days=2)).isoformat()},
    )
    assert future.status_code == 422 and future.json()["error"]["details"]["reason"] == "fx_future_date"
    base = await client.put(
        f"{API}/fx/rates", headers=org["finance"].headers, json={**rate, "currency": "JPY"}
    )
    assert base.status_code == 422 and base.json()["error"]["details"]["reason"] == "fx_base_currency"
    stored = await client.put(f"{API}/fx/rates", headers=org["finance"].headers, json=rate)
    assert stored.status_code == 200, stored.text
    assert stored.json()["source"] == "manual"

    converted = await usd_request()
    assert "VAL-004" not in _rules(converted)
    assert Decimal(converted["base_amount"]) == Decimal("150250")  # $1,000.00 × 150.25
    assert Decimal(converted["fx_rate"]) == Decimal("150.25") and converted["fx_source"] == "manual"
    assert converted["status"] == "pending_approval"
    assert [s["approver_role"] for s in converted["approvals"]] == ["manager"]  # ¥150,250 tier

    # Correcting the day's rate does not change a submitted request.
    corrected = await client.put(
        f"{API}/fx/rates", headers=org["finance"].headers, json={**rate, "rate": "151"}
    )
    assert corrected.status_code == 200
    rates = (await client.get(f"{API}/fx/rates", headers=org["employee"].headers)).json()
    assert rates["base_currency"] == "JPY" and "JPY" not in rates["supported_currencies"]
    assert [(r["currency"], Decimal(r["rate"])) for r in rates["latest"]] == [("USD", Decimal("151"))]
    assert len([r for r in rates["history"] if r["currency"] == "USD"]) == 1
    detail = (
        await client.get(f"{API}/purchase-requests/{converted['id']}", headers=org["employee"].headers)
    ).json()
    assert Decimal(detail["base_amount"]) == Decimal("150250")

    budgets = (await client.get(f"{API}/budgets", headers=org["finance"].headers)).json()
    budget = next(b for b in budgets if b["cost_center_id"] == org.cost_center_id)
    assert Decimal(budget["pending"]) == Decimal("150250")

    disabled = await client.post(f"{API}/fx/rates/refresh", headers=org["finance"].headers)
    assert (
        disabled.status_code == 409
        and disabled.json()["error"]["details"]["reason"] == "fx_provider_disabled"
    )


async def test_ecb_refresh_records_cross_rates(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    org = await build_org(client)
    monkeypatch.setattr(get_settings(), "fx_ecb_enabled", True)

    async def fake_fetch(settings=None, client=None):  # noqa: ANN001, ANN202
        return parse_daily((FIXTURES / "fx" / "eurofxref-daily.xml").read_bytes())

    monkeypatch.setattr("app.integrations.ecb.fetch_daily", fake_fetch)
    refreshed = await client.post(f"{API}/fx/rates/refresh", headers=org["finance"].headers)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["reference_date"] == "2026-11-12"
    assert refreshed.json()["updated"] == ["CHF", "EUR", "GBP", "USD"]  # SEK is not a supported currency
    rates = {
        r["currency"]: r
        for r in (await client.get(f"{API}/fx/rates", headers=org["employee"].headers)).json()["history"]
    }
    assert Decimal(rates["USD"]["rate"]) == Decimal("150.00000000") and rates["USD"]["source"] == "ecb"
    assert Decimal(rates["EUR"]["rate"]) == Decimal("165.00000000")


# --- Journal export --------------------------------------------------------------------------------

REGISTERED = "T2010401000001"  # passes the check digit; the NTA registry is off in tests
LEDGER = {
    "payable_account": "2110",
    "input_tax_account": "1550",
    "bank_account": "1110",
    "default_expense_account": "6000",
}


async def test_journal_export_is_balanced_and_exports_each_document_once(client: AsyncClient) -> None:
    org = await build_org(client)
    approver = await add_user(client, org, "second_finance", ["finance_manager"])
    vendor = await create_vendor(client, org, name="Journal supplier", invoice_registration_number=REGISTERED)

    not_configured = await client.post(f"{API}/journal-exports", headers=org["finance"].headers, json={})
    assert not_configured.status_code == 422
    assert not_configured.json()["error"]["details"]["reason"] == "ledger_not_configured"
    assert (
        await client.put(f"{API}/ledger/settings", headers=org["finance"].headers, json=LEDGER)
    ).status_code == 403
    configured = await client.put(f"{API}/ledger/settings", headers=org.admin.headers, json=LEDGER)
    assert configured.status_code == 200, configured.text
    no_scope = await client.put(
        f"{API}/ledger/mappings", headers=org.admin.headers, json=[{"expense_account": "6999"}]
    )
    assert (
        no_scope.status_code == 422
        and no_scope.json()["error"]["details"]["reason"] == "ledger_mapping_scope"
    )
    mapped = await client.put(
        f"{API}/ledger/mappings",
        headers=org.admin.headers,
        json=[
            {"category": "office_supplies", "expense_account": "6100"},
            {"category": "office_supplies", "cost_center_id": org.cost_center_id, "expense_account": "6110"},
        ],
    )
    assert mapped.status_code == 200, mapped.text
    assert len(mapped.json()["mappings"]) == 2

    # One paid invoice (bank file produced) and one matched, unpaid invoice.
    await client.put(
        f"{API}/vendors/{vendor['id']}/bank-account", headers=org["procurement"].headers, json=BANK
    )
    await client.put(
        f"{API}/organizations/current/payment-settings",
        headers=org.admin.headers,
        json={**BANK, "consignor_code": "1234567890", "consignor_name_kana": "テストカイシャ"},
    )
    await _approved_payment(client, org, vendor, "INV-JE-1", approver)
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    transfer = await client.post(
        f"{API}/payments/transfer-file", headers=org["finance"].headers, json={"transfer_date": tomorrow}
    )
    assert transfer.status_code == 200, transfer.text
    request = await _approved_request(
        client, org, vendor["id"], [{"description": "Paper", "quantity": "1", "unit_price": "20000"}]
    )
    order = await _open_order(client, org, request["id"])
    await _receive(client, org, order, order["items"][0], "1")
    await _invoice(
        client,
        org,
        order,
        "INV-JE-2",
        items=[
            {
                "purchase_order_item_id": order["items"][0]["id"],
                "quantity": "1",
                "unit_price": "20000",
                "tax_rate": "0.10",
            }
        ],
        tax="2000",
        registration_number=REGISTERED,
    )

    pending = (await client.get(f"{API}/journal-exports/pending", headers=org["finance"].headers)).json()
    assert pending == {"configured": True, "invoices": 2, "payments": 1}
    assert (
        await client.post(f"{API}/journal-exports", headers=org["employee"].headers, json={})
    ).status_code == 403

    exported = await client.post(
        f"{API}/journal-exports", headers=org["finance"].headers, json={"format": "csv"}
    )
    assert exported.status_code == 201, exported.text
    body = exported.json()
    assert (body["invoice_count"], body["payment_count"], body["entry_count"]) == (2, 1, 3)
    assert body["more_pending"] is False and body["filename"] == f"{body['reference']}.csv"
    rows = list(csv.DictReader(io.StringIO(base64.b64decode(body["content_base64"]).decode("utf-8"))))
    by_entry: dict[str, list[dict]] = {}
    for row in rows:
        by_entry.setdefault(row["entry_id"], []).append(row)
    for lines in by_entry.values():
        assert sum(Decimal(r["debit"]) for r in lines) == sum(Decimal(r["credit"]) for r in lines)
    inv2 = next(lines for lines in by_entry.values() if lines[0]["document"] == "INV-JE-2")
    assert [(r["account"], r["debit"], r["credit"]) for r in inv2] == [
        ("6110", "20000.00", "0"),
        ("1550", "2000.00", "0"),
        ("2110", "0", "22000.00"),
    ]
    payment = next(lines for lines in by_entry.values() if lines[0]["entry_type"] == "payment")
    assert [r["account"] for r in payment] == ["2110", "1110"]

    again = await client.post(
        f"{API}/journal-exports", headers=org["finance"].headers, json={"format": "json"}
    )
    assert (
        again.status_code == 422 and again.json()["error"]["details"]["reason"] == "journal_nothing_to_export"
    )
    history = (await client.get(f"{API}/journal-exports", headers=org["finance"].headers)).json()
    assert [h["reference"] for h in history] == [body["reference"]]
    download = await client.get(
        f"{API}/journal-exports/{body['id']}/download", headers=org["finance"].headers
    )
    assert download.status_code == 200
    assert base64.b64encode(download.content).decode() == body["content_base64"]
    assert download.headers["content-type"].startswith("text/csv")


async def test_journal_export_json_format(client: AsyncClient) -> None:
    org = await build_org(client)
    vendor = await create_vendor(client, org)
    await client.put(f"{API}/ledger/settings", headers=org.admin.headers, json=LEDGER)
    request = await _approved_request(
        client, org, vendor["id"], [{"description": "Pens", "quantity": "1", "unit_price": "10000"}]
    )
    order = await _open_order(client, org, request["id"])
    await _receive(client, org, order, order["items"][0], "1")
    await _invoice(
        client,
        org,
        order,
        "INV-JSON-1",
        items=[{"purchase_order_item_id": order["items"][0]["id"], "quantity": "1", "unit_price": "10000"}],
    )
    exported = await client.post(
        f"{API}/journal-exports", headers=org["finance"].headers, json={"format": "json"}
    )
    assert exported.status_code == 201, exported.text
    document = json.loads(base64.b64decode(exported.json()["content_base64"]))
    assert document["reference"] == exported.json()["reference"]
    (entry,) = document["entries"]
    assert entry["document"] == "INV-JSON-1"
    assert [(line["account"], line["debit"], line["credit"]) for line in entry["lines"]] == [
        ("6000", "10000.00", "0"),
        ("2110", "0", "10000.00"),
    ]


# --- Row-level security ------------------------------------------------------------------------------

RLS_INSERTS = {
    "contracts": (
        "INSERT INTO contracts (id, org_id, number, vendor_id, title, categories, starts_on, ends_on, currency, "
        "status, created_by_id, created_at, updated_at) VALUES (gen_random_uuid(), :org, 'CT-X', :vendor, 'x', "
        "'{}', current_date, current_date, 'JPY', 'draft', :user, now(), now())"
    ),
    "exchange_rates": (
        "INSERT INTO exchange_rates (id, org_id, currency, rate, valid_on, source, created_at) "
        "VALUES (gen_random_uuid(), :org, 'USD', 150, current_date, 'manual', now())"
    ),
    "gl_account_mappings": (
        "INSERT INTO gl_account_mappings (id, org_id, category, expense_account, updated_at) "
        "VALUES (gen_random_uuid(), :org, 'software', '6000', now())"
    ),
    "journal_exports": (
        "INSERT INTO journal_exports (id, org_id, reference, format, invoice_count, payment_count, entry_count, "
        "sha256, content, created_by_id, created_at) VALUES (gen_random_uuid(), :org, 'JE-X', 'csv', 0, 0, 0, "
        "repeat('a', 64), ''::bytea, :user, now())"
    ),
}


async def test_new_finance_tables_are_isolated_by_rls(client: AsyncClient, app_engine, admin_engine) -> None:  # noqa: ANN001
    a = await build_org(client, name="RLS A K.K.")
    b = await build_org(client, name="RLS B K.K.")
    vendors = {a.org_id: await create_vendor(client, a), b.org_id: await create_vendor(client, b)}
    for table, insert in RLS_INSERTS.items():
        for org in (a, b):  # one row per tenant, written as the owner
            async with admin_engine.begin() as conn:
                await conn.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
                await conn.execute(
                    text(insert),
                    {"org": org.org_id, "vendor": vendors[org.org_id]["id"], "user": org["finance"].user_id},
                )
        async with app_engine.connect() as conn:  # the runtime role
            async with conn.begin():
                await conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": a.org_id})
                seen = {str(r[0]) for r in await conn.execute(text(f"SELECT DISTINCT org_id FROM {table}"))}  # noqa: S608
                assert seen == {a.org_id}, table
            async with conn.begin():
                assert (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == 0, table  # noqa: S608
        with pytest.raises(DBAPIError, match="row-level security"):
            async with app_engine.begin() as conn:
                await conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": a.org_id})
                await conn.execute(
                    text(insert),
                    {"org": b.org_id, "vendor": vendors[b.org_id]["id"], "user": b["finance"].user_id},
                )


async def test_journal_exports_are_append_only_for_the_runtime_role(client: AsyncClient, app_engine) -> None:  # noqa: ANN001
    org = await build_org(client)
    async with app_engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": org.org_id})
        for statement in ("UPDATE journal_exports SET format = 'json'", "DELETE FROM journal_exports"):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with conn.begin_nested():
                    await conn.execute(text(statement))
