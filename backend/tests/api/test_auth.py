from urllib.parse import parse_qs, urlparse

import jwt
from httpx import AsyncClient
from sqlalchemy import text

from tests.helpers import API, PASSWORD, bearer, register_org, unique_email


async def test_register_creates_tenant_admin_and_example_policy(client: AsyncClient) -> None:
    org = await register_org(client, "Registration Test")
    me = (await client.get(f"{API}/auth/me", headers=org.admin.headers)).json()
    assert set(me["roles"]) == {"org_admin", "employee"}
    assert me["organization"]["base_currency"] == "JPY"
    assert me["organization"]["fiscal_year_start_month"] == 4
    assert me["organization"]["is_demo"] is False
    assert "approval:act" not in me["permissions"]  # SoD for the admin

    policy = (await client.get(f"{API}/approval-policies/active", headers=org.admin.headers)).json()
    assert policy["version"] == 1
    assert policy["config"]["auto_approval_limit"] == "30000"


async def test_duplicate_email_is_rejected(client: AsyncClient) -> None:
    email = unique_email("dup")
    payload = {"organization_name": "One", "full_name": "A", "email": email, "password": PASSWORD}
    assert (await client.post(f"{API}/auth/register", json=payload)).status_code == 201
    r = await client.post(f"{API}/auth/register", json={**payload, "email": email.upper()})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "email_taken"


async def test_weak_password_rejected(client: AsyncClient) -> None:
    r = await client.post(
        f"{API}/auth/register",
        json={"organization_name": "Weak", "full_name": "A", "email": unique_email("w"), "password": "short"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


async def test_login_success_and_failure(client: AsyncClient) -> None:
    org = await register_org(client)
    ok = await client.post(f"{API}/auth/login", json={"email": org.admin.email, "password": PASSWORD})
    assert ok.status_code == 200
    assert ok.json()["token_type"] == "bearer"

    bad = await client.post(
        f"{API}/auth/login", json={"email": org.admin.email, "password": "Wr0ngPassword!"}
    )
    unknown = await client.post(
        f"{API}/auth/login", json={"email": unique_email("nobody"), "password": PASSWORD}
    )
    # Same status and message whether or not the account exists (no user enumeration).
    assert bad.status_code == unknown.status_code == 401
    assert bad.json()["error"]["message"] == unknown.json()["error"]["message"]

    actions = [
        e["action"]
        for e in (await client.get(f"{API}/audit-logs?entity_type=user", headers=org.admin.headers)).json()[
            "items"
        ]
    ]
    assert "user.login_failed" in actions and "user.login" in actions


async def test_requests_without_valid_token_are_rejected(client: AsyncClient) -> None:
    assert (await client.get(f"{API}/auth/me")).status_code == 401
    assert (await client.get(f"{API}/auth/me", headers=bearer("not-a-jwt"))).status_code == 401
    forged = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "org": "00000000-0000-0000-0000-000000000000",
            "typ": "access",
            "iat": 0,
            "exp": 9999999999,
        },
        "wrong-secret-wrong-secret-wrong-secret",
        algorithm="HS256",
    )
    r = await client.get(f"{API}/auth/me", headers=bearer(forged))
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


async def test_deactivated_user_token_stops_working(client: AsyncClient) -> None:
    org = await register_org(client)
    r = await client.post(
        f"{API}/invitations",
        headers=org.admin.headers,
        json={"email": unique_email("x"), "roles": ["employee"]},
    )
    token = parse_qs(urlparse(r.json()["invite_url"]).query)["token"][0]
    accepted = (
        await client.post(
            f"{API}/auth/accept-invitation", json={"token": token, "full_name": "X", "password": PASSWORD}
        )
    ).json()
    headers = bearer(accepted["access_token"])
    assert (await client.get(f"{API}/auth/me", headers=headers)).status_code == 200
    r = await client.patch(
        f"{API}/users/{accepted['user']['id']}", headers=org.admin.headers, json={"is_active": False}
    )
    assert r.status_code == 200
    assert (await client.get(f"{API}/auth/me", headers=headers)).status_code == 401


async def test_invitation_is_single_use_and_revocable(client: AsyncClient) -> None:
    org = await register_org(client)
    r = await client.post(
        f"{API}/invitations",
        headers=org.admin.headers,
        json={"email": unique_email("inv"), "roles": ["employee", "manager"]},
    )
    assert r.status_code == 201
    token = parse_qs(urlparse(r.json()["invite_url"]).query)["token"][0]
    body = {"token": token, "full_name": "Invitee", "password": PASSWORD}
    first = await client.post(f"{API}/auth/accept-invitation", json=body)
    assert first.status_code == 201
    assert set(first.json()["user"]["roles"]) == {"employee", "manager"}
    assert (await client.post(f"{API}/auth/accept-invitation", json=body)).status_code == 404

    r = await client.post(
        f"{API}/invitations",
        headers=org.admin.headers,
        json={"email": unique_email("rev"), "roles": ["employee"]},
    )
    token2 = parse_qs(urlparse(r.json()["invite_url"]).query)["token"][0]
    assert (
        await client.delete(f"{API}/invitations/{r.json()['id']}", headers=org.admin.headers)
    ).status_code == 204
    r = await client.post(
        f"{API}/auth/accept-invitation", json={"token": token2, "full_name": "Late", "password": PASSWORD}
    )
    assert r.status_code == 404


async def test_invitation_token_is_stored_hashed(client: AsyncClient, admin_engine) -> None:  # noqa: ANN001
    org = await register_org(client)
    r = await client.post(
        f"{API}/invitations",
        headers=org.admin.headers,
        json={"email": unique_email("h"), "roles": ["employee"]},
    )
    token = parse_qs(urlparse(r.json()["invite_url"]).query)["token"][0]
    async with admin_engine.connect() as conn:
        stored = await conn.scalar(
            text("SELECT token_hash FROM invitations WHERE id = :id"), {"id": r.json()["id"]}
        )
    assert stored != token and len(stored) == 64


async def test_platform_admin_role_cannot_be_granted_from_a_tenant(client: AsyncClient) -> None:
    org = await register_org(client)
    r = await client.post(
        f"{API}/invitations",
        headers=org.admin.headers,
        json={"email": unique_email("pa"), "roles": ["platform_admin"]},
    )
    assert r.status_code == 403


async def test_last_admin_cannot_be_removed(client: AsyncClient) -> None:
    org = await register_org(client)
    r = await client.patch(
        f"{API}/users/{org.admin.user_id}", headers=org.admin.headers, json={"roles": ["employee"]}
    )
    assert r.status_code == 422
    r = await client.patch(
        f"{API}/users/{org.admin.user_id}", headers=org.admin.headers, json={"is_active": False}
    )
    assert r.status_code == 422


async def test_demo_login_only_works_for_demo_tenant(client: AsyncClient, admin_engine) -> None:  # noqa: ANN001
    org = await register_org(client)
    r = await client.post(f"{API}/auth/demo-login", json={"email": org.admin.email})
    assert r.status_code == 404

    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE organizations SET is_demo = true WHERE id = :id"), {"id": org.org_id})
    try:
        personas = (await client.get(f"{API}/auth/demo-personas")).json()
        assert org.admin.email in {p["email"] for p in personas}
        r = await client.post(f"{API}/auth/demo-login", json={"email": org.admin.email})
        assert r.status_code == 200
        assert r.json()["user"]["organization"]["is_demo"] is True
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("UPDATE organizations SET is_demo = false WHERE id = :id"), {"id": org.org_id}
            )


async def test_manager_assignment_cannot_create_cycle(client: AsyncClient) -> None:
    org = await register_org(client)
    ids = []
    for key in ("a", "b"):
        r = await client.post(
            f"{API}/invitations",
            headers=org.admin.headers,
            json={"email": unique_email(key), "roles": ["employee"]},
        )
        token = parse_qs(urlparse(r.json()["invite_url"]).query)["token"][0]
        body = (
            await client.post(
                f"{API}/auth/accept-invitation", json={"token": token, "full_name": key, "password": PASSWORD}
            )
        ).json()
        ids.append(body["user"]["id"])
    a, b = ids
    assert (
        await client.patch(f"{API}/users/{a}", headers=org.admin.headers, json={"manager_id": b})
    ).status_code == 200
    r = await client.patch(f"{API}/users/{b}", headers=org.admin.headers, json={"manager_id": a})
    assert r.status_code == 422
    r = await client.patch(f"{API}/users/{a}", headers=org.admin.headers, json={"manager_id": a})
    assert r.status_code == 422


async def test_meta_reports_demo_settings_and_registration_can_be_disabled(client: AsyncClient) -> None:
    from app.core.config import get_settings

    meta = await client.get(f"{API}/meta")
    assert meta.status_code == 200
    assert meta.json()["registration_enabled"] is True
    settings = get_settings()
    settings.registration_enabled = False
    try:
        assert (await client.get(f"{API}/meta")).json()["registration_enabled"] is False
        blocked = await client.post(
            f"{API}/auth/register",
            json={
                "organization_name": "Visitor Co",
                "full_name": "Visitor",
                "email": "visitor@example.com",
                "password": "Str0ngPassw0rd1",
            },
        )
        assert blocked.status_code == 403
    finally:
        settings.registration_enabled = None
