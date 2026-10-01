"""Purchase-to-pay controls: cumulative billing, close rules, tax, qualified invoices, quote line
pricing, the human-in-the-loop award record and the login throttle."""

import asyncio
from datetime import date

from httpx import AsyncClient

from app.modules.identity.router import login_throttle
from tests.helpers import API, PASSWORD, Org, build_org, create_vendor, submit

REGISTRATION = "T7123456789012"  # valid check digit


async def _approved_request(client: AsyncClient, org: Org, vendor_id: str, items: list[dict]) -> dict:
    created = await client.post(
        f"{API}/purchase-requests",
        headers=org["employee"].headers,
        json={
            "title": "Controlled purchase",
            "category": "office_supplies",
            "cost_center_id": org.cost_center_id,
            "justification": "Replacement equipment for the team",
            "preferred_vendor_id": vendor_id,
            "items": items,
        },
    )
    assert created.status_code == 201, created.text
    request = await submit(client, org, created.json())
    if request["status"] == "approved":  # low-value requests can be auto-approved by policy
        return request
    for approver in ("manager", "head"):
        response = await client.post(
            f"{API}/purchase-requests/{request['id']}/approve", headers=org[approver].headers
        )
        if response.json().get("status") == "approved":
            return response.json()
    raise AssertionError(f"request was not approved: {response.json()}")


async def _open_order(client: AsyncClient, org: Org, request_id: str) -> dict:
    created = await client.post(
        f"{API}/purchase-requests/{request_id}/purchase-order", headers=org["procurement"].headers
    )
    assert created.status_code == 201, created.text
    for target in ("sent", "acknowledged"):
        moved = await client.post(
            f"{API}/purchase-orders/{created.json()['id']}/transition",
            headers=org["procurement"].headers,
            json={"status": target},
        )
        assert moved.status_code == 200, moved.text
    orders = (await client.get(f"{API}/purchase-orders", headers=org["procurement"].headers)).json()
    return next(order for order in orders if order["id"] == created.json()["id"])


async def _receive(client: AsyncClient, org: Org, order: dict, line: dict, quantity: str) -> None:
    response = await client.post(
        f"{API}/purchase-orders/{order['id']}/receipts",
        headers=org["procurement"].headers,
        json={"received_on": "2026-09-30", "purchase_order_item_id": line["id"], "quantity": quantity},
    )
    assert response.status_code == 201, response.text


async def _invoice(client: AsyncClient, org: Org, order: dict, number: str, **body: object) -> dict:
    response = await client.post(
        f"{API}/purchase-orders/{order['id']}/invoices",
        headers=org["procurement"].headers,
        json={"invoice_number": number, "invoice_date": "2026-09-30", **body},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _close(client: AsyncClient, org: Org, order: dict) -> int:
    response = await client.post(
        f"{API}/purchase-orders/{order['id']}/transition",
        headers=org["procurement"].headers,
        json={"status": "closed"},
    )
    return response.status_code


async def test_second_invoice_for_already_billed_goods_is_over_billing(client: AsyncClient) -> None:
    org = await build_org(client)
    vendor = await create_vendor(client, org)
    request = await _approved_request(
        client, org, vendor["id"], [{"description": "Chairs", "quantity": "2", "unit_price": "30000"}]
    )
    order = await _open_order(client, org, request["id"])
    line = order["items"][0]
    await _receive(client, org, order, line, "2")
    items = [{"purchase_order_item_id": line["id"], "quantity": "2", "unit_price": "30000"}]

    first = await _invoice(client, org, order, "INV-A", items=items)
    assert first["match_status"] == "matched"
    # Same price, same quantity, new invoice number: before cumulative matching this also "matched".
    second = await _invoice(client, org, order, "INV-B", items=items)
    assert second["match_status"] == "mismatch"
    detail = second["line_match_details"][0]
    assert detail["previously_invoiced_quantity"] == "2.000"
    assert detail["billable_quantity"] == "0.000"
    assert detail["quantity_status"] == "over_billed"
    blocked = await client.post(
        f"{API}/invoices/{second['id']}/payment-requests", headers=org["finance"].headers
    )
    assert blocked.status_code == 422

    # A pending exception blocks closing; once rejected, the void invoice no longer does.
    assert await _close(client, org, order) == 422
    rejected = await client.post(
        f"{API}/invoices/{second['id']}/exception-decision",
        headers=org["finance"].headers,
        json={"decision": "reject", "comment": "Duplicate billing of goods already invoiced."},
    )
    assert rejected.json()["match_status"] == "exception_rejected"
    assert await _close(client, org, order) == 200


async def test_partial_invoices_accumulate_and_unbilled_goods_block_closing(client: AsyncClient) -> None:
    org = await build_org(client)
    vendor = await create_vendor(client, org)
    request = await _approved_request(
        client, org, vendor["id"], [{"description": "Monitors", "quantity": "3", "unit_price": "20000"}]
    )
    order = await _open_order(client, org, request["id"])
    line = order["items"][0]
    await _receive(client, org, order, line, "3")

    first = await _invoice(
        client,
        org,
        order,
        "INV-P1",
        items=[{"purchase_order_item_id": line["id"], "quantity": "1", "unit_price": "20000"}],
    )
    assert first["match_status"] == "matched"
    assert first["line_match_details"][0]["quantity_status"] == "partial"
    assert first["line_match_details"][0]["remaining_billable_quantity"] == "2.000"
    assert await _close(client, org, order) == 422  # two accepted units are not yet invoiced

    second = await _invoice(
        client,
        org,
        order,
        "INV-P2",
        items=[{"purchase_order_item_id": line["id"], "quantity": "2", "unit_price": "20000"}],
    )
    assert second["match_status"] == "matched"
    assert second["line_match_details"][0]["previously_invoiced_quantity"] == "1.000"
    assert await _close(client, org, order) == 200


async def test_concurrent_invoices_cannot_both_bill_the_same_quantity(client: AsyncClient) -> None:
    org = await build_org(client)
    vendor = await create_vendor(client, org)
    request = await _approved_request(
        client, org, vendor["id"], [{"description": "Desk", "quantity": "1", "unit_price": "50000"}]
    )
    order = await _open_order(client, org, request["id"])
    line = order["items"][0]
    await _receive(client, org, order, line, "1")
    items = [{"purchase_order_item_id": line["id"], "quantity": "1", "unit_price": "50000"}]
    results = await asyncio.gather(
        _invoice(client, org, order, "INV-RACE-1", items=items),
        _invoice(client, org, order, "INV-RACE-2", items=items),
    )
    assert sorted(r["match_status"] for r in results) == ["matched", "mismatch"]


async def test_consumption_tax_and_qualified_invoice_registration(client: AsyncClient) -> None:
    org = await build_org(client)
    vendor = await create_vendor(client, org, invoice_registration_number=REGISTRATION)
    request = await _approved_request(
        client,
        org,
        vendor["id"],
        [
            {"description": "Office supplies", "quantity": "3", "unit_price": "333"},
            {"description": "Bottled water", "quantity": "7", "unit_price": "111"},
        ],
    )
    order = await _open_order(client, org, request["id"])
    supplies, water = order["items"]
    await _receive(client, org, order, supplies, "3")
    await _receive(client, org, order, water, "7")

    def lines(supplies_qty: str = "1") -> list[dict]:
        return [
            {
                "purchase_order_item_id": supplies["id"],
                "quantity": supplies_qty,
                "unit_price": "333",
                "tax_rate": "0.10",
            },
            {"purchase_order_item_id": water["id"], "quantity": "1", "unit_price": "111", "tax_rate": "0.08"},
        ]

    # 10% of 333 = 33.3 and 8% of 111 = 8.88 → floor 41, ceiling 43; rounding is the issuer's choice.
    ok = await _invoice(
        client, org, order, "INV-T1", items=lines(), tax="42", registration_number=REGISTRATION
    )
    assert ok["match_status"] == "matched", ok["match_details"]
    assert ok["match_details"]["tax"]["expected_range"] == ["41", "43"]
    assert ok["match_details"]["qualified_invoice"]["status"] == "verified"

    wrong_tax = await _invoice(
        client, org, order, "INV-T2", items=lines(), tax="50", registration_number=REGISTRATION
    )
    assert wrong_tax["match_status"] == "mismatch"
    assert wrong_tax["match_details"]["tax"]["status"] == "mismatch"

    wrong_number = await _invoice(
        client, org, order, "INV-T3", items=lines(), tax="41", registration_number="T9999999999999"
    )
    assert wrong_number["match_status"] == "mismatch"
    assert wrong_number["match_details"]["qualified_invoice"]["status"] == "mismatch"

    missing_number = await _invoice(client, org, order, "INV-T4", items=lines(), tax="41")
    assert missing_number["match_status"] == "mismatch"
    assert missing_number["match_details"]["qualified_invoice"]["status"] == "missing"

    mixed = await client.post(
        f"{API}/purchase-orders/{order['id']}/invoices",
        headers=org["procurement"].headers,
        json={
            "invoice_number": "INV-T5",
            "invoice_date": "2026-09-30",
            "tax": "33",
            "items": [
                {
                    "purchase_order_item_id": supplies["id"],
                    "quantity": "1",
                    "unit_price": "333",
                    "tax_rate": "0.10",
                },
                {"purchase_order_item_id": water["id"], "quantity": "1", "unit_price": "111"},
            ],
        },
    )
    assert mixed.status_code == 422  # a rate on some lines but not others is ambiguous
    bad_format = await client.post(
        f"{API}/purchase-orders/{order['id']}/invoices",
        headers=org["procurement"].headers,
        json={
            "invoice_number": "INV-T6",
            "invoice_date": "2026-09-30",
            "registration_number": "1234",
            "items": lines(),
        },
    )
    assert bad_format.status_code == 422


async def test_quote_line_prices_flow_exactly_into_purchase_order(client: AsyncClient) -> None:
    org = await build_org(client)
    vendor = await create_vendor(client, org)
    request = await _approved_request(
        client,
        org,
        vendor["id"],
        [
            {"description": "Laptops", "quantity": "2", "unit_price": "40000"},
            {"description": "Docks", "quantity": "2", "unit_price": "10000"},
        ],
    )
    quotes_url = f"{API}/purchase-requests/{request['id']}/quotes"
    base = {"vendor_id": vendor["id"], "delivery_days": 5, "contract_compliant": True}
    inconsistent = await client.post(
        quotes_url,
        headers=org["procurement"].headers,
        json={
            **base,
            "amount": "90000",
            "line_prices": [{"line_no": 1, "unit_price": "38000"}, {"line_no": 2, "unit_price": "9000"}],
        },
    )
    assert inconsistent.status_code == 422  # 2×38000 + 2×9000 = 94000, not 90000
    missing_line = await client.post(
        quotes_url,
        headers=org["procurement"].headers,
        json={**base, "amount": "76000", "line_prices": [{"line_no": 1, "unit_price": "38000"}]},
    )
    assert missing_line.status_code == 422
    quote = await client.post(
        quotes_url,
        headers=org["procurement"].headers,
        json={
            **base,
            "amount": "94000",
            "line_prices": [{"line_no": 1, "unit_price": "38000"}, {"line_no": 2, "unit_price": "9000"}],
        },
    )
    assert quote.status_code == 201, quote.text
    award = await client.post(f"{quotes_url}/{quote.json()['id']}/award", headers=org["procurement"].headers)
    assert award.status_code == 200, award.text
    assert award.json()["award_context"]["followed_recommendation"] is True
    created = await client.post(
        f"{API}/purchase-requests/{request['id']}/purchase-order", headers=org["procurement"].headers
    )
    assert created.json()["pricing_basis"] == "quote_line_prices"
    assert created.json()["total"] == "94000.00"
    orders = (await client.get(f"{API}/purchase-orders", headers=org["procurement"].headers)).json()
    order = next(o for o in orders if o["id"] == created.json()["id"])
    assert [(i["unit_price"], i["line_total"]) for i in order["items"]] == [
        ("38000.00", "76000.00"),
        ("9000.00", "18000.00"),
    ]


async def test_override_of_recommendation_needs_reason_and_is_reported(client: AsyncClient) -> None:
    org = await build_org(client)
    cheap = await create_vendor(client, org, name="Recommended supplier")
    preferred = await create_vendor(client, org, name="Incumbent supplier")
    request = await _approved_request(
        client, org, cheap["id"], [{"description": "Toner", "quantity": "10", "unit_price": "9000"}]
    )
    quotes_url = f"{API}/purchase-requests/{request['id']}/quotes"
    best = await client.post(
        quotes_url,
        headers=org["procurement"].headers,
        json={
            "vendor_id": cheap["id"],
            "amount": "80000",
            "delivery_days": 3,
            "quality_score": "4.5",
            "contract_compliant": True,
        },
    )
    other = await client.post(
        quotes_url,
        headers=org["procurement"].headers,
        json={
            "vendor_id": preferred["id"],
            "amount": "88000",
            "delivery_days": 9,
            "quality_score": "4",
            "contract_compliant": False,
        },
    )
    expired = await client.post(
        quotes_url,
        headers=org["procurement"].headers,
        json={
            "vendor_id": preferred["id"],
            "amount": "1000",
            "delivery_days": 1,
            "quote_valid_until": "2020-01-01",
        },
    )
    comparison = (await client.get(quotes_url, headers=org["procurement"].headers)).json()
    assert comparison["recommended_quote_id"] == best.json()["id"]
    by_id = {q["id"]: q for q in comparison["quotes"]}
    assert by_id[expired.json()["id"]]["eligible"] is False
    assert by_id[expired.json()["id"]]["score"] is None
    assert by_id[other.json()["id"]]["rank"] == 2

    no_reason = await client.post(
        f"{quotes_url}/{other.json()['id']}/award", headers=org["procurement"].headers
    )
    assert no_reason.status_code == 422
    assert no_reason.json()["error"]["details"]["recommended_quote_id"] == best.json()["id"]
    overridden = await client.post(
        f"{quotes_url}/{other.json()['id']}/award",
        headers=org["procurement"].headers,
        json={"override_reason": "Incumbent holds the service contract for these printers."},
    )
    assert overridden.status_code == 200, overridden.text
    context = overridden.json()["award_context"]
    assert context["followed_recommendation"] is False
    assert context["awarded_rank"] == 2

    report = await client.get(f"{API}/decision-support/sourcing", headers=org["analyst"].headers)
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["awards_with_recommendation"] == 1
    assert body["overridden"] == 1
    assert body["agreement_rate"] == 0.0
    assert body["overrides"][0]["recommended_vendor"] == "Recommended supplier"
    assert body["overrides"][0]["delivery_outcome"] == "no_purchase_order"
    employee = await client.get(f"{API}/decision-support/sourcing", headers=org["employee"].headers)
    assert employee.status_code == 403

    # Outcome: the overridden award is ordered, delivered today (within 9 quoted days) and invoiced.
    order = await _open_order(client, org, request["id"])
    line = order["items"][0]
    received = await client.post(
        f"{API}/purchase-orders/{order['id']}/receipts",
        headers=org["procurement"].headers,
        json={
            "received_on": date.today().isoformat(),
            "purchase_order_item_id": line["id"],
            "quantity": "10",
            "damaged_quantity": "1",
        },
    )
    assert received.status_code == 201, received.text
    await _invoice(
        client,
        org,
        order,
        "INV-OUTCOME",
        items=[{"purchase_order_item_id": line["id"], "quantity": "9", "unit_price": line["unit_price"]}],
    )
    outcome = (await client.get(f"{API}/decision-support/sourcing", headers=org["analyst"].headers)).json()
    overridden_bucket = outcome["outcomes"]["overridden"]
    assert overridden_bucket["awards"] == 1
    assert overridden_bucket["invoiced"] == 1
    assert overridden_bucket["first_pass_invoice_match_rate"] == 1.0
    assert overridden_bucket["damaged_quantity_rate"] == 0.1
    # One damaged unit leaves the line partially accepted, so delivery is still pending.
    assert overridden_bucket["pending"] == 1
    assert outcome["overrides"][0]["delivery_outcome"] == "pending"
    assert outcome["outcomes"]["followed"]["awards"] == 0


async def test_repeated_failed_logins_are_throttled(client: AsyncClient) -> None:
    org = await build_org(client)
    original = login_throttle.max_failures
    login_throttle.max_failures = 3
    login_throttle._failures.clear()
    try:
        email = org["employee"].email
        for _ in range(3):
            bad = await client.post(
                f"{API}/auth/login", json={"email": email, "password": "wrong-password-1"}
            )
            assert bad.status_code == 401
        blocked = await client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0
        other_user = await client.post(
            f"{API}/auth/login", json={"email": org["peer"].email, "password": PASSWORD}
        )
        assert other_user.status_code == 200
    finally:
        login_throttle.max_failures = original
        login_throttle._failures.clear()
