from httpx import AsyncClient

from tests.helpers import API, build_org, create_pr, create_vendor, submit


async def test_assistant_uses_supported_queries_and_returns_evidence(client: AsyncClient) -> None:
    org = await build_org(client)
    vendor = await create_vendor(client, org, categories=("software",))
    request = await submit(
        client,
        org,
        await create_pr(client, org, amount="300000", category="software", vendor_id=vendor["id"]),
    )
    approvers = {
        "manager": "manager",
        "department_head": "head",
        "procurement_officer": "procurement",
        "finance_manager": "finance",
    }
    for _ in range(5):
        pending = next((step for step in request["approvals"] if step["status"] == "pending"), None)
        if pending is None:
            break
        decision = await client.post(
            f"{API}/purchase-requests/{request['id']}/approve",
            headers=org[approvers[pending["approver_role"]]].headers,
        )
        assert decision.status_code == 200, decision.text
        request = decision.json()
    assert request["status"] == "approved"

    result = await client.post(
        f"{API}/procurement-assistant/query",
        headers=org["analyst"].headers,
        json={"question": "Show software purchases above ¥100,000"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["intent"] == "approved_software_spend"
    assert [row["id"] for row in result.json()["evidence"]] == [request["id"]]
    assert "no generated SQL" in result.json()["strategy"]

    vendors = await client.post(
        f"{API}/procurement-assistant/query",
        headers=org["analyst"].headers,
        json={"question": "Which vendors are approved for software?"},
    )
    assert vendors.status_code == 200
    assert vendor["id"] in {row["id"] for row in vendors.json()["evidence"]}

    unsupported = await client.post(
        f"{API}/procurement-assistant/query",
        headers=org["analyst"].headers,
        json={"question": "Predict next year's optimal contract strategy"},
    )
    assert unsupported.status_code == 200
    assert unsupported.json()["intent"] is None
    assert unsupported.json()["evidence"] == []

    knowledge = await client.post(
        f"{API}/procurement-assistant/knowledge",
        headers=org["manager"].headers,
        json={"question": "What amount threshold requires procurement review?"},
    )
    assert knowledge.status_code == 200, knowledge.text
    assert "Active approval policy v1 (JPY)" in knowledge.json()["answer"]
    citations = knowledge.json()["citations"]
    assert citations[0]["id"] == "approval-policy-v1"
    assert any(citation["id"] == "APR-003" for citation in citations)


async def test_tenant_knowledge_document_versions_are_cited_and_isolated(client: AsyncClient) -> None:
    org = await build_org(client)
    other = await build_org(client, name="Other knowledge tenant")
    path = f"{API}/procurement-assistant/knowledge/documents"
    first = await client.post(
        path,
        headers=org["admin"].headers,
        json={
            "title": "Travel purchasing guide",
            "source_reference": "intranet://procurement/travel-v1",
            "content": (
                "Travel bookings must use approved travel suppliers and be booked at least fourteen days in advance "
                "unless an emergency exception is approved by the department head."
            ),
        },
    )
    assert first.status_code == 201, first.text
    assert first.json()["version"] == 1
    second = await client.post(
        path,
        headers=org["admin"].headers,
        json={
            "title": "Travel purchasing guide",
            "source_reference": "intranet://procurement/travel-v2",
            "content": (
                "Travel bookings should use approved suppliers and be booked at least twenty-one days ahead "
                "unless a documented exception is approved by the department head."
            ),
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2
    listing = await client.get(path, headers=org["admin"].headers)
    assert listing.status_code == 200
    assert [(row["version"], row["is_active"]) for row in listing.json()] == [(2, True), (1, False)]
    query = await client.post(
        f"{API}/procurement-assistant/knowledge",
        headers=org["employee"].headers,
        json={"question": "How far in advance should travel be booked?"},
    )
    assert query.status_code == 200, query.text
    doc_citations = [citation for citation in query.json()["citations"] if citation["id"].startswith("doc:")]
    assert doc_citations
    assert "twenty-one days" in " ".join(citation["excerpt"] for citation in doc_citations)
    other_listing = await client.get(path, headers=other["admin"].headers)
    assert other_listing.status_code == 200
    assert other_listing.json() == []
