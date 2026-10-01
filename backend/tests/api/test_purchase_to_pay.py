from httpx import AsyncClient

from tests.helpers import API, add_user, build_org, create_pr, create_vendor, submit


async def test_approved_request_receipt_invoice_and_analytics(client: AsyncClient) -> None:
    org = await build_org(client)
    vendor = await create_vendor(client, org)
    vendor_fast = await create_vendor(client, org, name="Fast supplier")
    request = await submit(client, org, await create_pr(client, org, amount="600000", vendor_id=vendor["id"]))
    for quote_body in (
        {
            "vendor_id": vendor["id"],
            "amount": "580000",
            "delivery_days": 20,
            "quality_score": "4.5",
            "contract_compliant": True,
        },
        {
            "vendor_id": vendor_fast["id"],
            "amount": "620000",
            "delivery_days": 5,
            "quality_score": "4.0",
            "contract_compliant": False,
        },
    ):
        quote = await client.post(
            f"{API}/purchase-requests/{request['id']}/quotes",
            headers=org["procurement"].headers,
            json=quote_body,
        )
        assert quote.status_code == 201, quote.text
    comparison = await client.get(
        f"{API}/purchase-requests/{request['id']}/quotes", headers=org["procurement"].headers
    )
    assert comparison.status_code == 200
    assert comparison.json()["method"] == "transparent weighted rule baseline"
    assert len(comparison.json()["quotes"]) == 2
    decision = await client.post(
        f"{API}/purchase-requests/{request['id']}/approve", headers=org["manager"].headers
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "pending_approval"
    decision = await client.post(
        f"{API}/purchase-requests/{request['id']}/approve", headers=org["head"].headers
    )
    assert decision.json()["status"] == "approved"

    created = await client.post(
        f"{API}/purchase-requests/{request['id']}/purchase-order", headers=org["procurement"].headers
    )
    assert created.status_code == 201, created.text
    order = created.json()
    assert order["number"].startswith("PO-")
    assert order["total"] == "600000.00"

    sent = await client.post(
        f"{API}/purchase-orders/{order['id']}/transition",
        headers=org["procurement"].headers,
        json={"status": "sent"},
    )
    assert sent.json()["status"] == "sent"
    acknowledged = await client.post(
        f"{API}/purchase-orders/{order['id']}/transition",
        headers=org["procurement"].headers,
        json={"status": "acknowledged"},
    )
    assert acknowledged.json()["status"] == "acknowledged"

    receipt = await client.post(
        f"{API}/purchase-orders/{order['id']}/receipts",
        headers=org["procurement"].headers,
        json={"received_on": "2026-09-30", "quantity": "1", "damaged_quantity": "0"},
    )
    assert receipt.status_code == 201, receipt.text
    assert receipt.json()["purchase_order_status"] == "received"
    excess = await client.post(
        f"{API}/purchase-orders/{order['id']}/receipts",
        headers=org["procurement"].headers,
        json={"received_on": "2026-09-30", "quantity": "1", "damaged_quantity": "0"},
    )
    assert excess.status_code == 422
    invoice = await client.post(
        f"{API}/purchase-orders/{order['id']}/invoices",
        headers=org["procurement"].headers,
        json={
            "invoice_number": "INV-1",
            "invoice_date": "2026-09-30",
            "quantity": "1",
            "subtotal": "600000",
            "tax": "0",
        },
    )
    assert invoice.status_code == 201, invoice.text
    assert invoice.json()["match_status"] == "matched"
    payment = await client.post(
        f"{API}/invoices/{invoice.json()['id']}/payment-requests", headers=org["finance"].headers
    )
    assert payment.status_code == 201, payment.text
    self_approval = await client.post(
        f"{API}/payments/{payment.json()['id']}/decision",
        headers=org["finance"].headers,
        json={"decision": "approve", "comment": "Cannot self-approve"},
    )
    assert self_approval.status_code == 422
    approved_payment = await client.post(
        f"{API}/payments/{payment.json()['id']}/decision",
        headers=org["admin"].headers,
        json={"decision": "approve", "comment": "Reviewed and approved"},
    )
    assert approved_payment.status_code == 403
    payment_approver = await add_user(client, org, "second_finance", ["finance_manager"])
    approved_payment = await client.post(
        f"{API}/payments/{payment.json()['id']}/decision",
        headers=payment_approver.headers,
        json={"decision": "approve", "comment": "Reviewed and approved"},
    )
    assert approved_payment.status_code == 200
    assert approved_payment.json()["status"] == "approved"
    duplicate = await client.post(
        f"{API}/purchase-orders/{order['id']}/invoices",
        headers=org["procurement"].headers,
        json={
            "invoice_number": "inv-1",
            "invoice_date": "2026-09-30",
            "quantity": "1",
            "subtotal": "600000",
            "tax": "0",
        },
    )
    assert duplicate.status_code == 409
    mismatch = await client.post(
        f"{API}/purchase-orders/{order['id']}/invoices",
        headers=org["procurement"].headers,
        json={
            "invoice_number": "INV-MISMATCH",
            "invoice_date": "2026-09-30",
            "quantity": "1",
            "subtotal": "590000",
            "tax": "0",
        },
    )
    assert mismatch.status_code == 201
    assert mismatch.json()["match_status"] == "mismatch"
    payment_for_mismatch = await client.post(
        f"{API}/invoices/{mismatch.json()['id']}/payment-requests", headers=org["finance"].headers
    )
    assert payment_for_mismatch.status_code == 422
    close_with_exception = await client.post(
        f"{API}/purchase-orders/{order['id']}/transition",
        headers=org["procurement"].headers,
        json={"status": "closed"},
    )
    assert close_with_exception.status_code == 422

    analytics = await client.get(f"{API}/spend-analytics", headers=org["analyst"].headers)
    assert analytics.status_code == 200
    assert analytics.json()["invoice_match_summary"] == [
        {"status": "matched", "count": 1, "total": "600000.00"},
        {"status": "mismatch", "count": 1, "total": "590000.00"},
    ]
    assert analytics.json()["approved_spend_by_vendor"] == [
        {
            "vendor_id": vendor["id"],
            "vendor_name": vendor["name"],
            "approved_requests": 1,
            "approved_value": "600000.00",
        }
    ]
    assert analytics.json()["approved_spend_by_department"][0]["approved_value"] == "600000.00"
    assert len(analytics.json()["approved_spend_monthly_last_12_months"]) == 1
    assert analytics.json()["approved_spend_monthly_last_12_months"][0]["approved_value"] == "600000.00"
    assert analytics.json()["request_decision_cycle_hours"]["median"] is not None
    assert analytics.json()["request_decision_cycle_hours"]["p90"] is not None
    filtered = await client.get(
        f"{API}/spend-analytics",
        headers=org["analyst"].headers,
        params={"vendor_id": vendor_fast["id"], "category": "office_supplies"},
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["approved_spend_by_category"] == []
    assert filtered.json()["invoice_match_summary"] == []
    date_filtered = await client.get(
        f"{API}/spend-analytics",
        headers=org["analyst"].headers,
        params={"from_date": "2100-01-01", "to_date": "2100-12-31"},
    )
    assert date_filtered.status_code == 200, date_filtered.text
    assert date_filtered.json()["approved_spend_by_category"] == []
    assert date_filtered.json()["approved_spend_monthly_last_12_months"] == []


async def test_purchase_to_pay_is_tenant_scoped(client: AsyncClient) -> None:
    a = await build_org(client, name="P2P Org A")
    b = await build_org(client, name="P2P Org B")
    vendor = await create_vendor(client, a)
    request = await submit(client, a, await create_pr(client, a, vendor_id=vendor["id"]))
    await client.post(f"{API}/purchase-requests/{request['id']}/approve", headers=a["manager"].headers)
    order = await client.post(
        f"{API}/purchase-requests/{request['id']}/purchase-order", headers=a["procurement"].headers
    )
    assert order.status_code == 201, order.text

    orders_b = await client.get(f"{API}/purchase-orders", headers=b["procurement"].headers)
    assert orders_b.status_code == 200
    assert all(row["id"] != order.json()["id"] for row in orders_b.json())
    invoices_b = await client.get(f"{API}/invoices", headers=b["finance"].headers)
    assert invoices_b.status_code == 200
    assert invoices_b.json() == []
    analytics_b = await client.get(f"{API}/spend-analytics", headers=b["analyst"].headers)
    assert analytics_b.status_code == 200
    assert analytics_b.json()["approved_spend_by_category"] == []
    assert analytics_b.json()["approved_spend_by_department"] == []
    assert analytics_b.json()["approved_spend_by_vendor"] == []
    assert analytics_b.json()["approved_spend_monthly_last_12_months"] == []
    assert analytics_b.json()["purchase_order_summary"] == []


async def test_awarded_quote_prices_the_purchase_order(client: AsyncClient) -> None:
    org = await build_org(client)
    requested_vendor = await create_vendor(client, org, name="Requested supplier")
    awarded_vendor = await create_vendor(client, org, name="Awarded supplier")
    request = await submit(
        client, org, await create_pr(client, org, amount="600000", vendor_id=requested_vendor["id"])
    )
    await client.post(f"{API}/purchase-requests/{request['id']}/approve", headers=org["manager"].headers)
    approved = await client.post(
        f"{API}/purchase-requests/{request['id']}/approve", headers=org["head"].headers
    )
    assert approved.json()["status"] == "approved"
    quote_response = await client.post(
        f"{API}/purchase-requests/{request['id']}/quotes",
        headers=org["procurement"].headers,
        json={
            "vendor_id": awarded_vendor["id"],
            "amount": "550000",
            "delivery_days": 8,
            "contract_compliant": True,
        },
    )
    assert quote_response.status_code == 201, quote_response.text
    quote_id = quote_response.json()["id"]
    award = await client.post(
        f"{API}/purchase-requests/{request['id']}/quotes/{quote_id}/award",
        headers=org["procurement"].headers,
    )
    assert award.status_code == 200, award.text
    order_response = await client.post(
        f"{API}/purchase-requests/{request['id']}/purchase-order", headers=org["procurement"].headers
    )
    assert order_response.status_code == 201, order_response.text
    order = order_response.json()
    assert order["total"] == "550000.00"
    listed = await client.get(f"{API}/purchase-orders", headers=org["procurement"].headers)
    assert listed.json()[0]["awarded_quote_id"] == quote_id
    assert listed.json()[0]["items"][0]["line_total"] == "550000.00"


async def test_multiline_receipts_invoice_match_and_exception_adjudication(client: AsyncClient) -> None:
    org = await build_org(client)
    vendor = await create_vendor(client, org)
    created = await client.post(
        f"{API}/purchase-requests",
        headers=org["employee"].headers,
        json={
            "title": "Two line equipment order",
            "category": "hardware",
            "cost_center_id": org.cost_center_id,
            "justification": "Replace two equipment types",
            "preferred_vendor_id": vendor["id"],
            "items": [
                {"description": "Network switches", "quantity": "2", "unit_price": "200000"},
                {"description": "Access points", "quantity": "1", "unit_price": "200000"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    request = await submit(client, org, created.json())
    await client.post(f"{API}/purchase-requests/{request['id']}/approve", headers=org["manager"].headers)
    approved = await client.post(
        f"{API}/purchase-requests/{request['id']}/approve", headers=org["head"].headers
    )
    assert approved.json()["status"] == "approved"
    order_response = await client.post(
        f"{API}/purchase-requests/{request['id']}/purchase-order", headers=org["procurement"].headers
    )
    order = order_response.json()
    listed = await client.get(f"{API}/purchase-orders", headers=org["procurement"].headers)
    order_detail = listed.json()[0]
    first, second = order_detail["items"]
    sent = await client.post(
        f"{API}/purchase-orders/{order['id']}/transition",
        headers=org["procurement"].headers,
        json={"status": "sent"},
    )
    assert sent.status_code == 200
    acknowledged = await client.post(
        f"{API}/purchase-orders/{order['id']}/transition",
        headers=org["procurement"].headers,
        json={"status": "acknowledged"},
    )
    assert acknowledged.status_code == 200
    missing_line = await client.post(
        f"{API}/purchase-orders/{order['id']}/receipts",
        headers=org["procurement"].headers,
        json={"received_on": "2026-09-30", "quantity": "1"},
    )
    assert missing_line.status_code == 422
    for item, qty in ((first, "2"), (second, "1")):
        receipt = await client.post(
            f"{API}/purchase-orders/{order['id']}/receipts",
            headers=org["procurement"].headers,
            json={"received_on": "2026-09-30", "purchase_order_item_id": item["id"], "quantity": qty},
        )
        assert receipt.status_code == 201, receipt.text
    invoice = await client.post(
        f"{API}/purchase-orders/{order['id']}/invoices",
        headers=org["procurement"].headers,
        json={
            "invoice_number": "INV-LINES-1",
            "invoice_date": "2026-09-30",
            "tax": "0",
            "items": [
                {"purchase_order_item_id": first["id"], "quantity": "2", "unit_price": "200000"},
                {"purchase_order_item_id": second["id"], "quantity": "1", "unit_price": "201000"},
            ],
        },
    )
    assert invoice.status_code == 201, invoice.text
    assert invoice.json()["match_status"] == "mismatch"
    assert len(invoice.json()["line_match_details"]) == 2
    decision = await client.post(
        f"{API}/invoices/{invoice.json()['id']}/exception-decision",
        headers=org["finance"].headers,
        json={"decision": "approve", "comment": "Price difference verified against the signed amendment."},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["match_status"] == "exception_approved"
    payment = await client.post(
        f"{API}/invoices/{invoice.json()['id']}/payment-requests", headers=org["finance"].headers
    )
    assert payment.status_code == 201, payment.text
