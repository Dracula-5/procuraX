"""Zengin transfer-file export of approved payments."""

import base64
from datetime import date, timedelta

from httpx import AsyncClient

from tests.api.test_p2p_controls import _approved_request, _invoice, _open_order, _receive
from tests.helpers import API, Org, add_user, build_org, create_vendor

BANK = {
    "bank_code": "0999",
    "bank_name_kana": "テストギンコウ",
    "branch_code": "001",
    "branch_name_kana": "ホンテン",
    "account_type": "1",
    "account_number": "1234567",
    "holder_name_kana": "カ)ミナトジム",
}


async def _approved_payment(client: AsyncClient, org: Org, vendor: dict, number: str, approver) -> dict:
    request = await _approved_request(
        client, org, vendor["id"], [{"description": "Toner", "quantity": "2", "unit_price": "15000"}]
    )
    order = await _open_order(client, org, request["id"])
    await _receive(client, org, order, order["items"][0], "2")
    invoice = await _invoice(
        client,
        org,
        order,
        number,
        items=[{"purchase_order_item_id": order["items"][0]["id"], "quantity": "2", "unit_price": "15000"}],
    )
    payment = await client.post(
        f"{API}/invoices/{invoice['id']}/payment-requests", headers=org["finance"].headers
    )
    assert payment.status_code == 201, payment.text
    decided = await client.post(
        f"{API}/payments/{payment.json()['id']}/decision",
        headers=approver.headers,
        json={"decision": "approve", "comment": "Reviewed and approved"},
    )
    assert decided.status_code == 200, decided.text
    return payment.json()


async def test_approved_payments_export_once_to_a_zengin_file(client: AsyncClient) -> None:
    org = await build_org(client)
    approver = await add_user(client, org, "second_finance", ["finance_manager"])
    paid_vendor = await create_vendor(client, org, name="Banked supplier")
    unbanked_vendor = await create_vendor(client, org, name="Unbanked supplier")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    not_configured = await client.post(
        f"{API}/payments/transfer-file", headers=org["finance"].headers, json={"transfer_date": tomorrow}
    )
    assert not_configured.status_code == 422

    kanji = await client.put(
        f"{API}/vendors/{paid_vendor['id']}/bank-account",
        headers=org["procurement"].headers,
        json={**BANK, "holder_name_kana": "株式会社ミナト"},
    )
    assert kanji.status_code == 422
    stored = await client.put(
        f"{API}/vendors/{paid_vendor['id']}/bank-account", headers=org["procurement"].headers, json=BANK
    )
    assert stored.status_code == 200, stored.text
    assert stored.json()["bank_account"]["holder_name_kana"] == "ｶ)ﾐﾅﾄｼﾞﾑ"
    assert stored.json()["bank_account"]["account_number"] == "****567"
    forbidden = await client.put(
        f"{API}/organizations/current/payment-settings",
        headers=org["finance"].headers,
        json={**BANK, "consignor_code": "1234567890", "consignor_name_kana": "テストカイシャ"},
    )
    assert forbidden.status_code == 403
    settings = await client.put(
        f"{API}/organizations/current/payment-settings",
        headers=org.admin.headers,
        json={**BANK, "consignor_code": "1234567890", "consignor_name_kana": "テストカイシャ"},
    )
    assert settings.status_code == 200, settings.text

    paid = await _approved_payment(client, org, paid_vendor, "INV-ZG-1", approver)
    await _approved_payment(client, org, unbanked_vendor, "INV-ZG-2", approver)

    past = await client.post(
        f"{API}/payments/transfer-file", headers=org["finance"].headers, json={"transfer_date": "2020-01-01"}
    )
    assert past.status_code == 422
    exported = await client.post(
        f"{API}/payments/transfer-file", headers=org["finance"].headers, json={"transfer_date": tomorrow}
    )
    assert exported.status_code == 200, exported.text
    body = exported.json()
    assert body["payments"] == 1 and body["total"] == 30000
    assert body["skipped"][0]["reason"] == "No bank account for Unbanked supplier"
    lines = base64.b64decode(body["content_base64"]).split(b"\r\n")[:-1]
    assert [len(line) for line in lines] == [120, 120, 120, 120]
    assert lines[1].decode("cp932")[80:90] == "0000030000"
    assert lines[1].decode("cp932")[91:101].rstrip() == "INVZG1"

    payments = (await client.get(f"{API}/payments", headers=org["finance"].headers)).json()
    status = {p["id"]: (p["status"], p["export_reference"]) for p in payments}
    assert status[paid["id"]] == ("exported", body["reference"])
    again = await client.post(
        f"{API}/payments/transfer-file", headers=org["finance"].headers, json={"transfer_date": tomorrow}
    )
    assert again.status_code == 422  # only the unbanked payment remains; nothing exportable
    employee = await client.post(
        f"{API}/payments/transfer-file", headers=org["employee"].headers, json={"transfer_date": tomorrow}
    )
    assert employee.status_code == 403
