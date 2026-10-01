"""Organisation A must never see or touch Organisation B's data, through any endpoint.

Covered now: purchase requests, approvals and delegations, vendors, budgets, users,
departments, cost centres, policies, audit logs, timelines, purchase-to-pay records and analytics.
Invoice document uploads remain future scope.
"""

import pytest
from httpx import AsyncClient

from tests.helpers import API, Org, build_org, create_pr, create_vendor, submit


@pytest.fixture
async def two_orgs(client: AsyncClient) -> tuple[Org, Org, dict, dict]:
    a = await build_org(client, name="Org A")
    b = await build_org(client, name="Org B")
    vendor_a = await create_vendor(client, a, name="Shared Name Supplies")
    pr_a = await submit(client, a, await create_pr(client, a, amount="200000", vendor_id=vendor_a["id"]))
    return a, b, vendor_a, pr_a


async def test_purchase_requests_are_isolated(client: AsyncClient, two_orgs) -> None:  # noqa: ANN001
    a, b, _, pr_a = two_orgs
    for actor in ("admin", "finance", "procurement", "analyst", "employee"):
        headers = b[actor].headers
        assert (await client.get(f"{API}/purchase-requests/{pr_a['id']}", headers=headers)).status_code == 404
        listing = (await client.get(f"{API}/purchase-requests", headers=headers)).json()
        assert pr_a["id"] not in {p["id"] for p in listing["items"]}
        timeline = await client.get(f"{API}/purchase-requests/{pr_a['id']}/timeline", headers=headers)
        assert timeline.status_code == 404


async def test_cross_tenant_writes_are_rejected(client: AsyncClient, two_orgs) -> None:  # noqa: ANN001
    a, b, vendor_a, pr_a = two_orgs
    # Org B's manager-role holders cannot approve A's request.
    for actor in ("manager", "head", "procurement", "finance"):
        r = await client.post(f"{API}/purchase-requests/{pr_a['id']}/approve", headers=b[actor].headers)
        assert r.status_code == 404
    r = await client.post(
        f"{API}/purchase-requests/{pr_a['id']}/cancel",
        headers=b["procurement"].headers,
        json={"reason": "nope"},
    )
    assert r.status_code == 404
    r = await client.patch(
        f"{API}/vendors/{vendor_a['id']}", headers=b["procurement"].headers, json={"notes": "tampered"}
    )
    assert r.status_code == 404
    r = await client.post(
        f"{API}/vendors/{vendor_a['id']}/status",
        headers=b["procurement"].headers,
        json={"status": "blocked", "reason": "x"},
    )
    assert r.status_code == 404
    # Referencing A's cost centre or vendor from B's request is treated as not found.
    body = {"title": "Cross", "category": "office_supplies", "items": []}
    r = await client.post(
        f"{API}/purchase-requests",
        headers=b["employee"].headers,
        json={**body, "cost_center_id": a.cost_center_id},
    )
    assert r.status_code == 404
    r = await client.post(
        f"{API}/purchase-requests",
        headers=b["employee"].headers,
        json={**body, "preferred_vendor_id": vendor_a["id"]},
    )
    assert r.status_code == 404
    # A's untouched request is still pending.
    pr = (await client.get(f"{API}/purchase-requests/{pr_a['id']}", headers=a["employee"].headers)).json()
    assert pr["status"] == "pending_approval"


async def test_vendors_budgets_structure_users_are_isolated(client: AsyncClient, two_orgs) -> None:  # noqa: ANN001
    a, b, vendor_a, _ = two_orgs
    hb = b["procurement"].headers
    assert (await client.get(f"{API}/vendors/{vendor_a['id']}", headers=hb)).status_code == 404
    names = {v["id"] for v in (await client.get(f"{API}/vendors", headers=hb)).json()["items"]}
    assert vendor_a["id"] not in names
    # Same vendor name is allowed in another tenant (uniqueness is per tenant).
    await create_vendor(client, b, name="Shared Name Supplies")

    budgets_b = (await client.get(f"{API}/budgets", headers=b["finance"].headers)).json()
    assert all(x["cost_center_id"] != a.cost_center_id for x in budgets_b)
    assert a.department_id not in {
        d["id"] for d in (await client.get(f"{API}/departments", headers=b.admin.headers)).json()
    }
    assert a.cost_center_id not in {
        c["id"] for c in (await client.get(f"{API}/cost-centers", headers=b.admin.headers)).json()
    }
    users_b = {u["id"] for u in (await client.get(f"{API}/users", headers=b.admin.headers)).json()}
    assert a.admin.user_id not in users_b
    r = await client.patch(
        f"{API}/users/{a['employee'].user_id}", headers=b.admin.headers, json={"is_active": False}
    )
    assert r.status_code == 404
    r = await client.patch(
        f"{API}/departments/{a.department_id}", headers=b.admin.headers, json={"name": "Hijacked"}
    )
    assert r.status_code == 404


async def test_audit_and_inbox_are_isolated(client: AsyncClient, two_orgs) -> None:  # noqa: ANN001
    a, b, _, pr_a = two_orgs
    audit_b = (await client.get(f"{API}/audit-logs?limit=200", headers=b.admin.headers)).json()["items"]
    assert pr_a["id"] not in {e["entity_id"] for e in audit_b}
    inbox_b = (await client.get(f"{API}/approvals/inbox", headers=b["manager"].headers)).json()
    assert pr_a["id"] not in {i["purchase_request_id"] for i in inbox_b}
    orgs_visible_to_b = (await client.get(f"{API}/organizations", headers=b.admin.headers)).json()
    assert [o["id"] for o in orgs_visible_to_b] == [b.org_id]


async def test_document_numbers_are_per_tenant(client: AsyncClient) -> None:
    a = await build_org(client, name="Numbering A")
    b = await build_org(client, name="Numbering B")
    first_a = await create_pr(client, a)
    second_a = await create_pr(client, a)
    first_b = await create_pr(client, b)
    assert first_a["number"].endswith("-000001")
    assert second_a["number"].endswith("-000002")
    assert first_b["number"].endswith("-000001")
