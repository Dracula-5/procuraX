"""Builders that drive the public API the way a real tenant would (register → invite → accept)."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from httpx import AsyncClient, Response

from app.domain.fiscal import fiscal_year_of

API = "/api/v1"
PASSWORD = "Str0ngPassw0rd1"


def current_fy() -> int:
    return fiscal_year_of(datetime.now(UTC).astimezone(ZoneInfo("Asia/Tokyo")).date(), 4)


def unique_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Actor:
    user_id: str
    email: str
    headers: dict[str, str]


@dataclass
class Org:
    org_id: str
    admin: Actor
    department_id: str
    cost_center_id: str
    actors: dict[str, Actor] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Actor:
        return self.admin if key == "admin" else self.actors[key]


async def register_org(client: AsyncClient, name: str = "Test Co") -> Org:
    email = unique_email("admin")
    r = await client.post(
        f"{API}/auth/register",
        json={"organization_name": name, "full_name": "Ada Admin", "email": email, "password": PASSWORD},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = bearer(body["access_token"])
    departments = (await client.get(f"{API}/departments", headers=headers)).json()
    cost_centers = (await client.get(f"{API}/cost-centers", headers=headers)).json()
    return Org(
        org_id=body["user"]["organization"]["id"],
        admin=Actor(body["user"]["id"], email, headers),
        department_id=departments[0]["id"],
        cost_center_id=cost_centers[0]["id"],
    )


async def add_user(
    client: AsyncClient,
    org: Org,
    key: str,
    roles: list[str],
    *,
    manager: Actor | None = None,
    department_id: str | None = None,
) -> Actor:
    email = unique_email(key)
    r = await client.post(
        f"{API}/invitations",
        headers=org.admin.headers,
        json={
            "email": email,
            "roles": roles,
            "manager_id": manager.user_id if manager else None,
            "department_id": department_id or org.department_id,
        },
    )
    assert r.status_code == 201, r.text
    token = parse_qs(urlparse(r.json()["invite_url"]).query)["token"][0]
    r = await client.post(
        f"{API}/auth/accept-invitation",
        json={"token": token, "full_name": key.replace("_", " ").title(), "password": PASSWORD},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    actor = Actor(body["user"]["id"], email, bearer(body["access_token"]))
    org.actors[key] = actor
    return actor


async def build_org(
    client: AsyncClient, *, budget: str | None = "5000000", name: str = "Acme Test K.K."
) -> Org:
    """One user per persona. Reporting line: employee/peer → manager → head (heads the
    default department). The default cost centre gets a current-FY budget."""
    org = await register_org(client, name)
    head = await add_user(client, org, "head", ["department_head", "employee"])
    manager = await add_user(client, org, "manager", ["manager", "employee"], manager=head)
    await add_user(client, org, "employee", ["employee"], manager=manager)
    await add_user(client, org, "peer", ["employee"], manager=manager)
    await add_user(client, org, "procurement", ["procurement_officer"])
    await add_user(client, org, "finance", ["finance_manager"])
    await add_user(client, org, "analyst", ["read_only_analyst"])
    r = await client.patch(
        f"{API}/departments/{org.department_id}",
        headers=org.admin.headers,
        json={"head_user_id": head.user_id},
    )
    assert r.status_code == 200, r.text
    if budget is not None:
        r = await client.post(
            f"{API}/budgets",
            headers=org["finance"].headers,
            json={"cost_center_id": org.cost_center_id, "fiscal_year": current_fy(), "amount": budget},
        )
        assert r.status_code == 201, r.text
    return org


async def create_vendor(
    client: AsyncClient,
    org: Org,
    *,
    name: str | None = None,
    categories: tuple[str, ...] = ("office_supplies",),
    status: str | None = "approved",
    **extra: object,
) -> dict:
    r = await client.post(
        f"{API}/vendors",
        headers=org["procurement"].headers,
        json={"name": name or f"Vendor {uuid.uuid4().hex[:8]}", "categories": list(categories), **extra},
    )
    assert r.status_code == 201, r.text
    vendor = r.json()
    if status and status != "pending_review":
        body = {"status": status, "reason": "test setup"}
        r = await client.post(
            f"{API}/vendors/{vendor['id']}/status", headers=org["procurement"].headers, json=body
        )
        assert r.status_code == 200, r.text
        vendor = r.json()
    return vendor


async def create_pr(
    client: AsyncClient,
    org: Org,
    actor: str = "employee",
    *,
    amount: str = "100000",
    category: str = "office_supplies",
    vendor_id: str | None = None,
    emergency: bool = False,
    justification: str = "Replacement equipment for the team",
    cost_center: bool = True,
) -> dict:
    r = await client.post(
        f"{API}/purchase-requests",
        headers=org[actor].headers,
        json={
            "title": "Test purchase",
            "category": category,
            "cost_center_id": org.cost_center_id if cost_center else None,
            "justification": justification,
            "preferred_vendor_id": vendor_id,
            "is_emergency": emergency,
            "items": [{"description": "Item", "quantity": "1", "unit_price": amount}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def submit(client: AsyncClient, org: Org, pr: dict, actor: str = "employee") -> dict:
    r = await client.patch(f"{API}/purchase-requests/{pr['id']}/submit", headers=org[actor].headers)
    assert r.status_code == 200, r.text
    return r.json()


async def act(
    client: AsyncClient, org: Org, pr: dict, actor: str, action: str = "approve", **body: object
) -> Response:
    return await client.post(
        f"{API}/purchase-requests/{pr['id']}/{action}", headers=org[actor].headers, json=body or None
    )


def current_round(pr: dict) -> list[dict]:
    return [s for s in pr["approvals"] if s["round"] == pr["submission_count"]]
