"""NTA registry lookup during invoice matching, and Peppol / JP PINT UBL invoice import."""

import base64
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.config import get_settings
from tests.api.test_p2p_controls import _approved_request, _invoice, _open_order, _receive
from tests.helpers import API, build_org, create_vendor
from tests.nta_mock import APP_ID, NtaMock

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ACTIVE, REVOKED, UNREGISTERED, FAILING = (
    "T2010401000001",
    "T1010401000002",
    "T9010401000003",
    "T7010401000005",
)
PRICE_15000 = '<cbc:PriceAmount currencyID="JPY">15000</cbc:PriceAmount>'
PRICE_16000 = '<cbc:PriceAmount currencyID="JPY">16000</cbc:PriceAmount>'
LINES = [
    {"description": "Toner cartridge", "quantity": "2", "unit_price": "15000"},
    {"description": "Bottled tea for meetings", "quantity": "10", "unit_price": "1000"},
]


@pytest.fixture
async def nta(monkeypatch: pytest.MonkeyPatch, admin_engine):  # noqa: ANN001, ANN201
    async with admin_engine.begin() as conn:  # the cache is shared public data; start each test empty
        await conn.execute(text("DELETE FROM qualified_invoice_registry_cache"))
    async with NtaMock() as mock:
        settings = get_settings()
        monkeypatch.setattr(settings, "nta_registry_enabled", True)
        monkeypatch.setattr(settings, "nta_application_id", APP_ID)
        monkeypatch.setattr(settings, "nta_api_base_url", mock.base_url)
        yield mock


async def _received_order(client: AsyncClient, registration: str):  # noqa: ANN202
    org = await build_org(client)
    vendor = await create_vendor(client, org, invoice_registration_number=registration)
    request = await _approved_request(client, org, vendor["id"], LINES)
    order = await _open_order(client, org, request["id"])
    for line in order["items"]:
        await _receive(client, org, order, line, line["quantity"])
    return org, order


def _lines(order: dict) -> list[dict]:
    return [
        {
            "purchase_order_item_id": item["id"],
            "quantity": item["quantity"],
            "unit_price": item["unit_price"],
            "tax_rate": rate,
        }
        for item, rate in zip(order["items"], ["0.10", "0.08"], strict=True)
    ]


async def _manual_invoice(client: AsyncClient, org, order: dict, number: str, registration: str) -> dict:  # noqa: ANN001
    return await _invoice(
        client, org, order, number, items=_lines(order), tax="3800", registration_number=registration
    )


# --- NTA registry ------------------------------------------------------------------------------------


async def test_registry_lookup_is_recorded_then_served_from_cache(client: AsyncClient, nta: NtaMock) -> None:
    org, order = await _received_order(client, ACTIVE)
    first = await _manual_invoice(client, org, order, "INV-NTA-1", ACTIVE)
    qualified = first["match_details"]["qualified_invoice"]
    assert first["match_status"] == "matched"
    assert qualified["status"] == "verified" and qualified["registry_checked"] is True
    assert qualified["registry"]["source"] == "nta_web_api"
    assert qualified["registry"]["name"] == "株式会社ミナト商事（架空）"
    assert nta.requests[-1]["number"] == [ACTIVE] and nta.requests[-1]["id"] == [APP_ID]

    calls = len(nta.requests)
    org2, order2 = await _received_order(client, ACTIVE)
    second = await _manual_invoice(client, org2, order2, "INV-NTA-2", ACTIVE)
    assert second["match_details"]["qualified_invoice"]["registry"]["source"] == "cache"
    assert len(nta.requests) == calls  # no second API call within the cache window


async def test_revoked_or_unknown_numbers_become_exceptions(client: AsyncClient, nta: NtaMock) -> None:
    for number, reason in ((REVOKED, "revoked"), (UNREGISTERED, "not in the NTA")):
        org, order = await _received_order(client, number)
        invoice = await _manual_invoice(client, org, order, f"INV-{number}", number)
        assert invoice["match_status"] == "mismatch", invoice
        assert reason in invoice["match_details"]["qualified_invoice"]["reason"]


async def test_an_unreachable_registry_falls_back_to_local_checks(
    client: AsyncClient, nta: NtaMock, admin_engine
) -> None:
    org, order = await _received_order(client, FAILING)
    invoice = await _manual_invoice(client, org, order, "INV-DOWN-1", FAILING)
    assert invoice["match_status"] == "matched"  # check digit + vendor master, as before
    assert invoice["match_details"]["qualified_invoice"]["registry"] == {"source": "unavailable"}

    # With an older cached answer, that answer is used and marked stale.
    org, order = await _received_order(client, ACTIVE)
    await _manual_invoice(client, org, order, "INV-STALE-1", ACTIVE)
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE qualified_invoice_registry_cache SET fetched_at = now() - interval '30 days'")
        )
    nta.down = True
    org, order = await _received_order(client, ACTIVE)
    stale = await _manual_invoice(client, org, order, "INV-STALE-2", ACTIVE)
    assert stale["match_details"]["qualified_invoice"]["registry"]["source"] == "cache_stale"


async def test_disabled_registry_keeps_the_previous_behaviour(client: AsyncClient) -> None:
    org, order = await _received_order(client, ACTIVE)
    invoice = await _manual_invoice(client, org, order, "INV-OFF-1", ACTIVE)
    assert invoice["match_details"]["qualified_invoice"]["registry"] == {"source": "disabled"}
    assert invoice["match_status"] == "matched"


# --- Peppol / JP PINT UBL import ---------------------------------------------------------------------


def _xml(order: dict, **replace: str) -> str:
    text_ = (
        (FIXTURES / "einvoice" / "jp_pint_invoice.xml")
        .read_text(encoding="utf-8")
        .replace("PO-2026-000001", order["number"])
    )
    for old, new in replace.items():
        text_ = text_.replace(old, new)
    return base64.b64encode(text_.encode()).decode()


async def _import(client: AsyncClient, org, xml_b64: str, **extra: str):  # noqa: ANN001, ANN202
    return await client.post(
        f"{API}/einvoices", headers=org["procurement"].headers, json={"xml_base64": xml_b64, **extra}
    )


async def test_jp_pint_invoice_imports_and_runs_the_same_three_way_match(client: AsyncClient) -> None:
    org, order = await _received_order(client, ACTIVE)
    imported = await _import(client, org, _xml(order))
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["match_status"] == "matched"
    assert body["match_details"]["tax"]["status"] == "matched"  # 10 % and 8 % lines, 3,800 yen
    source = body["match_details"]["source"]
    assert source["type"] == "peppol_ubl" and source["jp_pint"] is True and len(source["xml_sha256"]) == 64
    assert {d["tax_rate"] for d in body["line_match_details"]} == {"0.1", "0.08"}

    again = await _import(client, org, _xml(order))
    assert again.status_code == 409  # same vendor + invoice number: the duplicate guard applies


async def test_ubl_price_differences_are_exceptions_and_bad_documents_are_refused(
    client: AsyncClient,
) -> None:
    org, order = await _received_order(client, ACTIVE)
    mismatch = await _import(
        client,
        org,
        _xml(order, **{PRICE_15000: PRICE_16000}),
    )
    assert mismatch.status_code == 201 and mismatch.json()["match_status"] == "mismatch"

    cases = [
        (
            _xml(order, MINATO="OTHER", **{"<cbc:DocumentCurrencyCode>JPY": "<cbc:DocumentCurrencyCode>USD"}),
            "einvoice_currency",
        ),
        (_xml(order, MINATO="X", **{order["number"]: "PO-0000-999999"}), "einvoice_order_not_found"),
        (
            _xml(order, MINATO="Y", **{"<cbc:LineID>2</cbc:LineID>": "<cbc:LineID>9</cbc:LineID>"}),
            "einvoice_line_reference",
        ),
        (
            base64.b64encode((FIXTURES / "einvoice" / "billion_laughs.xml").read_bytes()).decode(),
            "einvoice_unsafe_xml",
        ),
        ("not base64!", "einvoice_encoding"),
    ]
    for payload, reason in cases:
        refused = await _import(client, org, payload)
        assert refused.status_code == 422, (reason, refused.text)
        assert refused.json()["error"]["details"]["reason"] == reason
    other = await build_org(client, name="Other e-invoice K.K.")
    assert (await _import(client, other, _xml(order))).json()["error"]["details"][
        "reason"
    ] == "einvoice_order_not_found"
    assert (
        await client.post(
            f"{API}/einvoices", headers=org["employee"].headers, json={"xml_base64": _xml(order)}
        )
    ).status_code == 403
