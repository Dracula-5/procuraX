"""Vendors, budgets, approval policy, and role-based access to each."""

import pytest
from httpx import AsyncClient

from tests.helpers import API, build_org, create_pr, create_vendor, current_fy, submit


@pytest.fixture
async def org(client: AsyncClient):  # noqa: ANN201
    return await build_org(client)


# --- Vendors ---------------------------------------------------------------------------------


async def test_vendor_onboarding_lifecycle(client: AsyncClient, org) -> None:  # noqa: ANN001
    v = await create_vendor(
        client,
        org,
        name="Tokyo Network Systems",
        categories=("hardware", "it_services"),
        status=None,
        invoice_registration_number="T1234567890123",
    )
    assert v["status"] == "pending_review"  # never auto-approved on creation
    h = org["procurement"].headers
    r = await client.post(f"{API}/vendors/{v['id']}/status", headers=h, json={"status": "blocked"})
    assert r.status_code == 422  # reason required
    r = await client.post(f"{API}/vendors/{v['id']}/status", headers=h, json={"status": "approved"})
    assert r.json()["status"] == "approved"

    found = (await client.get(f"{API}/vendors?category=hardware&status=approved", headers=h)).json()
    assert v["id"] in {x["id"] for x in found["items"]}
    none = (await client.get(f"{API}/vendors?category=travel", headers=h)).json()
    assert v["id"] not in {x["id"] for x in none["items"]}


async def test_vendor_validation_and_uniqueness(client: AsyncClient, org) -> None:  # noqa: ANN001
    h = org["procurement"].headers
    await create_vendor(client, org, name="Unique Supplies")
    r = await client.post(
        f"{API}/vendors", headers=h, json={"name": "unique supplies", "categories": ["travel"]}
    )
    assert r.status_code == 409
    r = await client.post(
        f"{API}/vendors",
        headers=h,
        json={"name": "Bad Reg", "categories": ["travel"], "invoice_registration_number": "1234"},
    )
    assert r.status_code == 422


async def test_vendor_permissions(client: AsyncClient, org) -> None:  # noqa: ANN001
    body = {"name": "Nope", "categories": ["travel"]}
    for actor in ("employee", "manager", "finance", "admin", "analyst"):
        assert (await client.post(f"{API}/vendors", headers=org[actor].headers, json=body)).status_code == 403
    assert (await client.get(f"{API}/vendors", headers=org["employee"].headers)).status_code == 200
    v = await create_vendor(client, org)
    r = await client.post(
        f"{API}/vendors/{v['id']}/status",
        headers=org["finance"].headers,
        json={"status": "blocked", "reason": "x"},
    )
    assert r.status_code == 403


# --- Budgets ---------------------------------------------------------------------------------


async def test_budget_utilisation_is_derived_from_requests(client: AsyncClient, org) -> None:  # noqa: ANN001
    await submit(client, org, await create_pr(client, org, amount="20000"))  # approved (auto)
    await submit(client, org, await create_pr(client, org, amount="300000"))  # pending
    [budget] = (
        await client.get(f"{API}/budgets?fiscal_year={current_fy()}", headers=org["finance"].headers)
    ).json()
    assert budget["amount"] == "5000000.00"
    assert budget["committed"] == "20000.00"
    assert budget["pending"] == "300000.00"
    assert budget["available"] == "4980000.00"
    assert budget["utilization"] == "0.0040"


async def test_budget_permissions_and_duplicates(client: AsyncClient, org) -> None:  # noqa: ANN001
    body = {"cost_center_id": org.cost_center_id, "fiscal_year": current_fy() + 1, "amount": "100"}
    for actor in ("employee", "admin", "procurement"):
        assert (await client.post(f"{API}/budgets", headers=org[actor].headers, json=body)).status_code == 403
    assert (await client.get(f"{API}/budgets", headers=org["employee"].headers)).status_code == 403
    assert (await client.post(f"{API}/budgets", headers=org["finance"].headers, json=body)).status_code == 201
    assert (await client.post(f"{API}/budgets", headers=org["finance"].headers, json=body)).status_code == 409
    changes = (await client.get(f"{API}/audit-logs?action=budget.", headers=org.admin.headers)).json()[
        "items"
    ]
    assert {e["action"] for e in changes} == {"budget.created"}


# --- Approval policy -------------------------------------------------------------------------


async def test_policy_versioning_keeps_past_decisions_explainable(client: AsyncClient, org) -> None:  # noqa: ANN001
    before = await submit(client, org, await create_pr(client, org, amount="20000"))
    assert before["status"] == "approved" and before["policy_version"] == 1

    active = (await client.get(f"{API}/approval-policies/active", headers=org.admin.headers)).json()
    stricter = {**active["config"], "auto_approval_limit": "0"}
    r = await client.post(
        f"{API}/approval-policies",
        headers=org.admin.headers,
        json={"config": stricter, "notes": "No auto-approval"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 2

    after = await submit(client, org, await create_pr(client, org, amount="20000"))
    assert after["status"] == "pending_approval" and after["policy_version"] == 2
    old = (
        await client.get(f"{API}/purchase-requests/{before['id']}", headers=org["employee"].headers)
    ).json()
    assert old["policy_version"] == 1 and old["status"] == "approved"

    versions = (await client.get(f"{API}/approval-policies", headers=org["employee"].headers)).json()
    assert [(v["version"], v["is_active"]) for v in versions] == [(2, True), (1, False)]
    [event] = (
        await client.get(f"{API}/audit-logs?action=approval_policy", headers=org.admin.headers)
    ).json()["items"]
    assert event["changes"]["before"]["auto_approval_limit"] == "30000"
    assert event["changes"]["after"]["auto_approval_limit"] == "0"


async def test_policy_validation_and_permissions(client: AsyncClient, org) -> None:  # noqa: ANN001
    active = (await client.get(f"{API}/approval-policies/active", headers=org.admin.headers)).json()
    bad = {**active["config"], "auto_approval_limit": "9999999"}
    r = await client.post(f"{API}/approval-policies", headers=org.admin.headers, json={"config": bad})
    assert r.status_code == 422
    for actor in ("employee", "finance", "procurement", "head"):
        r = await client.post(
            f"{API}/approval-policies", headers=org[actor].headers, json={"config": active["config"]}
        )
        assert r.status_code == 403
    rules = (await client.get(f"{API}/approval-policies/rules", headers=org["employee"].headers)).json()
    assert {"VEN-001", "BUD-002", "SPL-001"} <= {r["id"] for r in rules}


# --- Role guards on the rest of the surface ---------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "allowed", "denied"),
    [
        ("GET", "/users", ["admin", "finance", "procurement"], ["employee", "manager", "analyst"]),
        (
            "GET",
            "/audit-logs",
            ["admin", "finance"],
            ["employee", "manager", "procurement", "analyst", "head"],
        ),
        ("GET", "/invitations", ["admin"], ["employee", "finance", "procurement"]),
        ("GET", "/budgets", ["finance", "head", "analyst", "admin"], ["employee", "manager"]),
    ],
)
async def test_role_guards(
    client: AsyncClient, org, method: str, path: str, allowed: list, denied: list
) -> None:  # noqa: ANN001
    for actor in allowed:
        assert (
            await client.request(method, f"{API}{path}", headers=org[actor].headers)
        ).status_code == 200, actor
    for actor in denied:
        assert (
            await client.request(method, f"{API}{path}", headers=org[actor].headers)
        ).status_code == 403, actor


async def test_read_only_analyst_cannot_create_requests(client: AsyncClient, org) -> None:  # noqa: ANN001
    r = await client.post(
        f"{API}/purchase-requests",
        headers=org["analyst"].headers,
        json={"title": "Analyst buy", "category": "travel", "items": []},
    )
    assert r.status_code == 403


async def test_structure_changes_require_admin(client: AsyncClient, org) -> None:  # noqa: ANN001
    body = {"name": "Research", "code": "RND"}
    assert (
        await client.post(f"{API}/departments", headers=org["head"].headers, json=body)
    ).status_code == 403
    r = await client.post(f"{API}/departments", headers=org.admin.headers, json=body)
    assert r.status_code == 201
    assert (await client.post(f"{API}/departments", headers=org.admin.headers, json=body)).status_code == 409
    r = await client.post(
        f"{API}/cost-centers",
        headers=org.admin.headers,
        json={"department_id": r.json()["id"], "code": "RND-001", "name": "Research lab"},
    )
    assert r.status_code == 201


async def test_error_envelope_and_request_id(client: AsyncClient, org) -> None:  # noqa: ANN001
    r = await client.get(
        f"{API}/purchase-requests/00000000-0000-0000-0000-000000000000",
        headers={**org["employee"].headers, "X-Request-ID": "trace-12345678"},
    )
    assert r.status_code == 404
    assert r.headers["x-request-id"] == "trace-12345678"
    assert r.json() == {
        "error": {"code": "not_found", "message": "Purchase request not found", "details": None},
        "request_id": "trace-12345678",
    }
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_health_and_readiness(client: AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}
    ready = await client.get("/ready")
    assert ready.status_code == 200 and ready.json()["database"] == "ok"
