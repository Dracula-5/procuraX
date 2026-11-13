"""Refresh-cookie sessions (rotation, reuse detection, CSRF, sign-out), TOTP sign-in and OIDC SSO."""

import time
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.domain import totp
from app.main import app
from app.modules.identity import mfa, sessions
from app.modules.identity.throttle import PostgresThrottle
from tests.helpers import API, PASSWORD, Org, add_user, bearer, register_org
from tests.oidc_mock import CLIENT_ID, CLIENT_SECRET, OidcMock

COOKIE = "procurax_refresh"


@pytest.fixture
async def fresh() -> AsyncClient:  # a client with its own cookie jar (one "browser")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
async def clear_throttle(admin_engine) -> None:  # noqa: ANN001
    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM auth_throttle"))


async def _login(client: AsyncClient, email: str, password: str = PASSWORD) -> dict:
    r = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


async def _refresh(client: AsyncClient, csrf: str | None, **headers: str) -> object:
    return await client.post(
        f"{API}/auth/refresh", headers={**({"X-CSRF-Token": csrf} if csrf else {}), **headers}
    )


# --- Refresh cookie sessions --------------------------------------------------------------------------


async def test_login_sets_an_httponly_refresh_cookie_and_refresh_rotates_it(fresh: AsyncClient) -> None:
    org = await register_org(fresh, "Session Co")
    first = await _login(fresh, org.admin.email)
    cookie = next(h for h in fresh.cookies.jar if h.name == COOKIE)
    assert cookie.path == "/api/v1/auth" and cookie.has_nonstandard_attr("HttpOnly")
    old_token = cookie.value

    missing = await _refresh(fresh, None)
    assert missing.status_code == 401 and missing.json()["error"]["details"]["reason"] == "csrf_invalid"
    foreign = await _refresh(fresh, first["csrf_token"], Origin="https://evil.example")
    assert foreign.status_code == 403 and foreign.json()["error"]["details"]["reason"] == "csrf_origin"

    renewed = await _refresh(fresh, first["csrf_token"], Origin="http://localhost:5173")
    assert renewed.status_code == 200, renewed.text
    body = renewed.json()
    assert body["user"]["id"] == org.admin.user_id and body["csrf_token"] == first["csrf_token"]
    assert fresh.cookies.get(COOKIE, path="/api/v1/auth") != old_token  # rotated
    me = await fresh.get(f"{API}/auth/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200


async def test_reusing_a_replaced_refresh_token_signs_the_whole_family_out(
    fresh: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await register_org(fresh, "Reuse Co")
    first = await _login(fresh, org.admin.email)
    stolen = fresh.cookies.get(COOKIE, path="/api/v1/auth")
    assert (await _refresh(fresh, first["csrf_token"])).status_code == 200
    current = fresh.cookies.get(COOKIE, path="/api/v1/auth")

    # Within the grace period a second, simultaneous refresh is told to retry; nothing is revoked.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as tab:
        tab.cookies.set(COOKIE, stolen, path="/api/v1/auth")
        raced = await _refresh(tab, first["csrf_token"])
        assert raced.status_code == 401 and raced.json()["error"]["details"]["reason"] == "session_superseded"
        monkeypatch.setattr(sessions, "SUPERSEDED_GRACE_SECONDS", 0)
        replay = await _refresh(tab, first["csrf_token"])
        assert replay.json()["error"]["details"]["reason"] == "session_reuse_detected"
    # The legitimate browser's newer token is now dead too, and so is its access token.
    fresh.cookies.set(COOKIE, current, path="/api/v1/auth")
    after = await _refresh(fresh, first["csrf_token"])
    assert after.status_code == 401 and after.json()["error"]["details"]["reason"] == "session_revoked"
    assert (await fresh.get(f"{API}/auth/me", headers=bearer(first["access_token"]))).status_code == 401


async def test_logout_ends_the_session_and_its_access_token(fresh: AsyncClient) -> None:
    org = await register_org(fresh, "Logout Co")
    body = await _login(fresh, org.admin.email)
    headers = bearer(body["access_token"])
    assert (await fresh.post(f"{API}/auth/logout")).status_code == 401  # CSRF token required
    out = await fresh.post(f"{API}/auth/logout", headers={"X-CSRF-Token": body["csrf_token"]})
    assert out.status_code == 204
    assert COOKIE in out.headers["set-cookie"] and "Max-Age=0" in out.headers["set-cookie"]
    me = await fresh.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 401 and me.json()["error"]["details"]["reason"] == "session_revoked"
    # A token from the register response (another session) still works until it is signed out.
    assert (await fresh.get(f"{API}/auth/me", headers=org.admin.headers)).status_code == 200


async def test_sessions_list_revoke_and_deactivation(client: AsyncClient) -> None:
    org = await register_org(client, "Devices Co")
    employee = await add_user(client, org, "employee", ["employee"])
    laptop = await _login(client, employee.email)
    phone = await _login(client, employee.email)
    listed = (await client.get(f"{API}/auth/sessions", headers=bearer(phone["access_token"]))).json()
    assert len(listed) == 3  # invitation acceptance, laptop, phone
    assert [s["current"] for s in listed].count(True) == 1
    revoked = await client.post(f"{API}/auth/sessions/revoke-others", headers=bearer(phone["access_token"]))
    assert revoked.json() == {"revoked": 2}
    assert (await client.get(f"{API}/auth/me", headers=bearer(laptop["access_token"]))).status_code == 401
    assert (await client.get(f"{API}/auth/me", headers=bearer(phone["access_token"]))).status_code == 200
    # Deactivating the account signs it out everywhere at once.
    off = await client.patch(
        f"{API}/users/{employee.user_id}", headers=org.admin.headers, json={"is_active": False}
    )
    assert off.status_code == 200
    assert (await client.get(f"{API}/auth/me", headers=bearer(phone["access_token"]))).status_code == 401


async def test_auth_sessions_are_isolated_by_rls(client: AsyncClient, app_engine) -> None:  # noqa: ANN001
    a = await register_org(client, "RLS Sessions A")
    b = await register_org(client, "RLS Sessions B")
    async with app_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": a.org_id})
            orgs = {str(r[0]) for r in await conn.execute(text("SELECT DISTINCT org_id FROM auth_sessions"))}
            assert orgs == {a.org_id}
        async with conn.begin():
            assert (await conn.execute(text("SELECT count(*) FROM auth_sessions"))).scalar() == 0
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": a.org_id})
            await conn.execute(
                text(
                    "INSERT INTO auth_sessions (id, org_id, user_id, family_id, token_hash, method, created_at, "
                    "last_used_at, expires_at, family_expires_at) VALUES (gen_random_uuid(), :org, :user, "
                    "gen_random_uuid(), repeat('b', 64), 'password', now(), now(), now(), now())"
                ),
                {"org": b.org_id, "user": b.admin.user_id},
            )
    async with app_engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": a.org_id})
        with pytest.raises(DBAPIError, match="permission denied"):
            async with conn.begin_nested():
                await conn.execute(text("DELETE FROM auth_sessions"))


# --- Shared throttle ------------------------------------------------------------------------------------


async def test_throttle_counts_are_shared_between_processes() -> None:
    replica_a, replica_b = PostgresThrottle(60), PostgresThrottle(60)
    await replica_a.record_failure("login:10.0.0.1:pat@example.com")
    await replica_b.record_failure("login:10.0.0.1:pat@example.com")
    with pytest.raises(RateLimitedError):
        await replica_a.check(("login:10.0.0.1:pat@example.com", 2))
    await replica_b.check(("login:10.0.0.2:pat@example.com", 2))
    await replica_b.reset("login:10.0.0.1:pat@example.com")
    await replica_a.check(("login:10.0.0.1:pat@example.com", 2))


# --- Two-factor sign-in -------------------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


async def _enable_mfa(client: AsyncClient, actor_headers: dict, clock: Clock) -> tuple[str, list[str]]:
    setup = await client.post(f"{API}/auth/mfa/setup", headers=actor_headers)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    wrong = await client.post(f"{API}/auth/mfa/enable", headers=actor_headers, json={"code": "000000"})
    assert wrong.status_code == 422 and wrong.json()["error"]["details"]["reason"] == "mfa_code_invalid"
    enabled = await client.post(
        f"{API}/auth/mfa/enable", headers=actor_headers, json={"code": totp.code_at(secret, clock.now)}
    )
    assert enabled.status_code == 200, enabled.text
    clock.now += 30
    return secret, enabled.json()["recovery_codes"]


async def test_totp_enrolment_and_two_step_sign_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    monkeypatch.setattr(mfa, "now_seconds", clock)
    org = await register_org(client, "MFA Co")
    employee = await add_user(client, org, "employee", ["employee"])
    secret, recovery = await _enable_mfa(client, employee.headers, clock)
    assert len(recovery) == 10
    me = (await client.get(f"{API}/auth/me", headers=employee.headers)).json()
    assert me["mfa_enabled"] is True

    challenge = (
        await client.post(f"{API}/auth/login", json={"email": employee.email, "password": PASSWORD})
    ).json()
    assert challenge["mfa_required"] is True and "access_token" not in challenge
    not_a_token = await client.get(f"{API}/auth/me", headers=bearer(challenge["mfa_token"]))
    assert not_a_token.status_code == 401

    bad = await client.post(
        f"{API}/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": "123456"}
    )
    assert bad.status_code == 401 and bad.json()["error"]["details"]["reason"] == "mfa_code_invalid"
    code = totp.code_at(secret, clock.now)
    ok = await client.post(f"{API}/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": code})
    assert ok.status_code == 200, ok.text
    assert ok.json()["user"]["id"] == employee.user_id
    replay = await client.post(
        f"{API}/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": code}
    )
    assert replay.status_code == 401  # the same code is never accepted twice

    with_recovery = await client.post(
        f"{API}/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "recovery_code": recovery[0]}
    )
    assert with_recovery.status_code == 200
    again = await client.post(
        f"{API}/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "recovery_code": recovery[0]}
    )
    assert again.status_code == 401  # each recovery code works once

    trail = (
        await client.get(f"{API}/audit-logs", headers=org.admin.headers, params={"entity_type": "user"})
    ).json()
    actions = [e["action"] for e in trail["items"]]
    assert "user.mfa_enabled" in actions and "user.mfa_recovery_code_used" in actions
    sessions_list = (
        await client.get(f"{API}/auth/sessions", headers=bearer(ok.json()["access_token"]))
    ).json()
    assert {"password_mfa", "recovery_code"} <= {s["method"] for s in sessions_list}

    clock.now += 30
    off = await client.post(
        f"{API}/auth/mfa/disable", headers=employee.headers, json={"code": totp.code_at(secret, clock.now)}
    )
    assert off.status_code == 204
    plain = await client.post(f"{API}/auth/login", json={"email": employee.email, "password": PASSWORD})
    assert "access_token" in plain.json()


async def test_wrong_codes_are_throttled_and_admins_can_reset_a_lost_device(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    monkeypatch.setattr(mfa, "now_seconds", clock)
    monkeypatch.setattr(get_settings(), "login_max_failures", 2)
    org = await register_org(client, "Lost Phone Co")
    employee = await add_user(client, org, "employee", ["employee"])
    await _enable_mfa(client, employee.headers, clock)
    challenge = (
        await client.post(f"{API}/auth/login", json={"email": employee.email, "password": PASSWORD})
    ).json()
    for _ in range(2):
        r = await client.post(
            f"{API}/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": "000000"}
        )
        assert r.status_code == 401
    blocked = await client.post(
        f"{API}/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": "000000"}
    )
    assert blocked.status_code == 429

    peer = await add_user(client, org, "peer", ["employee"])
    denied = await client.post(f"{API}/users/{employee.user_id}/mfa/reset", headers=peer.headers)
    assert denied.status_code == 403
    own = await client.post(f"{API}/users/{org.admin.user_id}/mfa/reset", headers=org.admin.headers)
    assert own.status_code == 422
    reset = await client.post(f"{API}/users/{employee.user_id}/mfa/reset", headers=org.admin.headers)
    assert reset.status_code == 204
    assert (await client.get(f"{API}/auth/me", headers=employee.headers)).status_code == 401  # signed out
    users = (await client.get(f"{API}/users", headers=org.admin.headers)).json()
    assert next(u for u in users if u["id"] == employee.user_id)["mfa_enabled"] is False


# --- Single sign-on (OpenID Connect) ------------------------------------------------------------------------


@pytest.fixture
async def idp(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    async with OidcMock() as mock:
        settings = get_settings()
        monkeypatch.setattr(settings, "oidc_enabled", True)
        monkeypatch.setattr(settings, "oidc_issuer", mock.issuer)
        monkeypatch.setattr(settings, "oidc_client_id", CLIENT_ID)
        monkeypatch.setattr(settings, "oidc_client_secret", CLIENT_SECRET)
        monkeypatch.setattr(settings, "oidc_redirect_uri", "http://test/api/v1/auth/oidc/callback")
        yield mock


async def _sso(client: AsyncClient, idp: OidcMock, **claims: object):  # noqa: ANN202
    start = await client.get(f"{API}/auth/oidc/start")
    assert start.status_code == 303, start.text
    authorize = start.headers["location"]
    query = parse_qs(urlsplit(authorize).query)
    assert query["code_challenge_method"] == ["S256"] and query["client_id"] == [CLIENT_ID]
    code, state = idp.issue_code(authorize, **claims)
    return await client.get(f"{API}/auth/oidc/callback", params={"code": code, "state": state})


async def test_sso_signs_in_an_existing_user_and_hands_over_through_the_refresh_cookie(
    fresh: AsyncClient, idp: OidcMock
) -> None:
    org: Org = await register_org(fresh, "SSO Co")
    config = (await fresh.get(f"{API}/auth/config")).json()
    assert config["oidc_enabled"] is True and config["oidc_display_name"]
    done = await _sso(fresh, idp, email=org.admin.email.upper())
    assert done.status_code == 303, done.text
    location = done.headers["location"]
    assert location.startswith("http://localhost:5173/app#sso=")
    csrf = location.split("#sso=", 1)[1]
    assert idp.token_requests[-1]["code_verifier"]  # PKCE verifier sent to the token endpoint
    tokens = await _refresh(fresh, csrf)
    assert tokens.status_code == 200, tokens.text
    assert tokens.json()["user"]["email"] == org.admin.email
    listed = (await fresh.get(f"{API}/auth/sessions", headers=bearer(tokens.json()["access_token"]))).json()
    assert any(s["method"] == "oidc" and s["current"] for s in listed)


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"email": "nobody@example.com"}, "sso_no_account"),
        ({"email_verified": False}, "sso_email_unverified"),
        ({"nonce": "replayed"}, "sso_token_invalid"),
        ({"aud": "another-client"}, "sso_token_invalid"),
        ({"iss": "https://evil.example"}, "sso_token_invalid"),
    ],
    ids=["unknown-user", "unverified-email", "wrong-nonce", "wrong-audience", "wrong-issuer"],
)
async def test_sso_refusals_go_back_to_the_login_page_with_a_reason(
    fresh: AsyncClient, idp: OidcMock, claims: dict, reason: str
) -> None:
    org = await register_org(fresh, "SSO Refusal Co")
    done = await _sso(fresh, idp, **{"email": org.admin.email, **claims})
    assert done.status_code == 303
    assert done.headers["location"] == f"http://localhost:5173/login?sso_error={reason}"


async def test_sso_state_must_come_from_the_same_browser(fresh: AsyncClient, idp: OidcMock) -> None:
    org = await register_org(fresh, "SSO State Co")
    start = await fresh.get(f"{API}/auth/oidc/start")
    code, state = idp.issue_code(start.headers["location"], email=org.admin.email)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other_browser:
        done = await other_browser.get(f"{API}/auth/oidc/callback", params={"code": code, "state": state})
    assert done.headers["location"].endswith("sso_error=sso_state_missing")


async def test_sso_is_off_by_default(fresh: AsyncClient) -> None:
    assert (await fresh.get(f"{API}/auth/config")).json()["oidc_enabled"] is False
    start = await fresh.get(f"{API}/auth/oidc/start")
    assert start.headers["location"].endswith("sso_error=sso_disabled")
