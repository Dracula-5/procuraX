import uuid
from typing import Annotated
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.deps import DB, CurrentUser, requires
from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import AuthenticationError, NotFoundError, PermissionDeniedError
from app.core.principal import Principal
from app.core.security import decode_mfa_challenge
from app.domain.rbac import Permission
from app.integrations import oidc
from app.modules.audit import service as audit
from app.modules.identity import mfa, service, sessions
from app.modules.identity.schemas import (
    AcceptInvitationRequest,
    AuthConfigOut,
    DemoLoginRequest,
    DemoPersonaOut,
    InvitationCreate,
    InvitationOut,
    LoginRequest,
    MeOut,
    MfaChallenge,
    MfaCodeIn,
    MfaSetupOut,
    MfaVerifyRequest,
    OrgOut,
    OrgUpdate,
    PreferencesUpdate,
    RecoveryCodesOut,
    RegisterRequest,
    RoleOut,
    SessionOut,
    TokenResponse,
    UserOut,
    UserUpdate,
)
from app.modules.identity.service import SignIn
from app.modules.identity.throttle import auth_throttle, limits_for

auth_router = APIRouter(prefix="/auth", tags=["auth"])
org_router = APIRouter(prefix="/organizations", tags=["organizations"])
users_router = APIRouter(tags=["users & roles"])

OIDC_STATE_COOKIE = "procurax_oidc"
CsrfHeader = Annotated[str | None, Header(alias="X-CSRF-Token")]


# --- Cookies, CSRF and client details --------------------------------------------------------------


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _cookie_secure() -> bool:
    settings = get_settings()
    return settings.refresh_cookie_secure or settings.refresh_cookie_samesite == "none"


def _set_refresh_cookie(response: Response, sign_in: SignIn) -> TokenResponse:
    settings = get_settings()
    response.set_cookie(
        settings.refresh_cookie_name,
        sign_in.refresh_token,
        max_age=max(1, int((sign_in.refresh_expires_at - utcnow()).total_seconds())),
        httponly=True,
        secure=_cookie_secure(),
        samesite=settings.refresh_cookie_samesite,
        path=f"{settings.api_prefix}/auth",
    )
    response.headers["Cache-Control"] = "no-store"
    return sign_in.response


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=f"{settings.api_prefix}/auth",
        httponly=True,
        secure=_cookie_secure(),
        samesite=settings.refresh_cookie_samesite,
    )


def _check_origin(request: Request) -> None:
    """Cookie-authenticated endpoints refuse browser requests from origins outside the allow-list.

    Together with the X-CSRF-Token header (which forces a CORS preflight and must match the session),
    this stops another site from riding on the refresh cookie (ADR-019).
    """
    origin = request.headers.get("origin")
    if origin is None:
        return  # not a browser request; the CSRF token still has to match
    settings = get_settings()
    allowed = {o.rstrip("/") for o in settings.cors_origins} | {settings.public_app_url.rstrip("/")}
    allowed.add(f"{request.url.scheme}://{request.url.netloc}")
    if origin.rstrip("/") not in allowed:
        raise PermissionDeniedError("Cross-site request refused", details={"reason": "csrf_origin"})


async def _throttled(purpose: str, request: Request, account: str) -> tuple[list[str], str]:
    checks, keys, success_key = limits_for(purpose, _client(request), account)
    await auth_throttle.check(*checks)
    return keys, success_key


def _reason(exc: AuthenticationError) -> str | None:
    return exc.details.get("reason") if isinstance(exc.details, dict) else None


# --- Sign-in ---------------------------------------------------------------------------------------


@auth_router.get("/config", response_model=AuthConfigOut)
async def auth_config() -> AuthConfigOut:
    """What the sign-in page may offer on this deployment."""
    settings = get_settings()
    return AuthConfigOut(
        registration_enabled=settings.allows_registration,
        demo_mode=settings.demo_mode,
        oidc_enabled=settings.oidc_enabled,
        oidc_display_name=settings.oidc_display_name if settings.oidc_enabled else None,
        mfa_available=settings.mfa_available,
    )


@auth_router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, session: DB, request: Request, response: Response) -> TokenResponse:
    """Create a new organisation (tenant) and its first administrator."""
    if not get_settings().allows_registration:
        raise PermissionDeniedError("Self-service registration is disabled on this public demo")
    return _set_refresh_cookie(response, await service.register(session, data, _agent(request)))


@auth_router.post("/login", response_model=TokenResponse | MfaChallenge)
async def login(data: LoginRequest, request: Request, response: Response) -> TokenResponse | MfaChallenge:
    """Password sign-in. With two-factor sign-in on, the answer is a challenge for POST /auth/mfa/verify.

    Failures are counted in PostgreSQL per address + account, per address and per account.
    """
    keys, success_key = await _throttled("login", request, data.email.lower())
    try:
        result = await service.login(data.email, data.password, _agent(request))
    except AuthenticationError:
        await auth_throttle.record_failure(*keys)
        raise
    await auth_throttle.reset(success_key)
    if isinstance(result, MfaChallenge):
        return result
    return _set_refresh_cookie(response, result)


@auth_router.post("/mfa/verify", response_model=TokenResponse)
async def verify_mfa(data: MfaVerifyRequest, request: Request, response: Response) -> TokenResponse:
    """Second sign-in step: a code from the authenticator app, or one recovery code."""
    user_id = decode_mfa_challenge(data.mfa_token)
    keys, success_key = await _throttled("mfa", request, str(user_id))
    try:
        sign_in = await service.finish_mfa_sign_in(user_id, data.code, data.recovery_code, _agent(request))
    except AuthenticationError:
        await auth_throttle.record_failure(*keys)
        raise
    await auth_throttle.reset(success_key)
    return _set_refresh_cookie(response, sign_in)


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh(request: Request, response: Response, csrf: CsrfHeader = None) -> TokenResponse:
    """New access token from the refresh cookie; the refresh token is rotated (reuse is detected)."""
    _check_origin(request)
    token = request.cookies.get(get_settings().refresh_cookie_name)
    sign_in = await service.refresh(token, csrf, _agent(request))
    return _set_refresh_cookie(response, sign_in)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, csrf: CsrfHeader = None) -> Response:
    """Sign this browser out: the session family is revoked and the cookie cleared."""
    _check_origin(request)
    await service.logout(request.cookies.get(get_settings().refresh_cookie_name), csrf)
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(out)
    return out


@auth_router.post("/accept-invitation", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def accept_invitation(
    data: AcceptInvitationRequest, request: Request, response: Response
) -> TokenResponse:
    keys, _ = await _throttled("invitation", request, "-")
    try:
        sign_in = await service.accept_invitation(data, _agent(request))
    except NotFoundError:
        await auth_throttle.record_failure(*keys)
        raise
    return _set_refresh_cookie(response, sign_in)


@auth_router.get("/me", response_model=MeOut)
async def me(session: DB, principal: CurrentUser) -> MeOut:
    return await service.me(session, principal)


@auth_router.patch("/me/preferences", response_model=MeOut)
async def update_preferences(data: PreferencesUpdate, session: DB, principal: CurrentUser) -> MeOut:
    """The caller's own UI preferences (currently the language)."""
    return await service.update_preferences(session, principal, data)


@auth_router.get("/demo-personas", response_model=list[DemoPersonaOut])
async def demo_personas() -> list[DemoPersonaOut]:
    """Personas of the fictional demo organisation (empty when demo mode is off)."""
    return await service.demo_personas()


@auth_router.post("/demo-login", response_model=TokenResponse)
async def demo_login(data: DemoLoginRequest, request: Request, response: Response) -> TokenResponse:
    return _set_refresh_cookie(response, await service.demo_login(data.email, _agent(request)))


# --- Sessions ----------------------------------------------------------------------------------------


@auth_router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(session: DB, principal: CurrentUser) -> list[SessionOut]:
    """Where the caller is signed in (one row per browser or device)."""
    rows = await sessions.list_for_user(session, principal.user_id)
    return [
        SessionOut(
            id=r.family_id,
            method=r.method,
            created_at=r.created_at,
            last_used_at=r.last_used_at,
            expires_at=r.expires_at,
            user_agent=r.user_agent,
            current=r.family_id == principal.session_id,
        )
        for r in rows
    ]


@auth_router.delete("/sessions/{family_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(family_id: uuid.UUID, session: DB, principal: CurrentUser) -> None:
    if not await sessions.revoke_own(session, principal.user_id, family_id):
        raise NotFoundError("Session not found")
    await session.commit()


@auth_router.post("/sessions/revoke-others", response_model=dict[str, int])
async def revoke_other_sessions(session: DB, principal: CurrentUser) -> dict[str, int]:
    """Sign out every other browser and device (e.g. after a lost laptop)."""
    count = 0
    for row in await sessions.list_for_user(session, principal.user_id):
        if row.family_id != principal.session_id:
            count += await sessions.revoke_family(session, row.family_id, "signed_out_elsewhere")
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="user.sessions_revoked",
        entity_type="user",
        entity_id=principal.user_id,
        summary=f"Signed out of {count} other session(s)",
    )
    await session.commit()
    return {"revoked": count}


# --- Two-factor sign-in (TOTP) -------------------------------------------------------------------------


@auth_router.post("/mfa/setup", response_model=MfaSetupOut)
async def mfa_setup(session: DB, principal: CurrentUser) -> MfaSetupOut:
    """A new secret for the authenticator app; it takes effect once a code from it is confirmed."""
    return MfaSetupOut(**await mfa.setup(session, principal))


@auth_router.post("/mfa/enable", response_model=RecoveryCodesOut)
async def mfa_enable(
    data: MfaCodeIn, session: DB, principal: CurrentUser, request: Request
) -> RecoveryCodesOut:
    keys, success_key = await _throttled("mfa-manage", request, str(principal.user_id))
    try:
        codes = await mfa.enable(session, principal, data.code or "")
    except mfa.InvalidCodeError:
        await auth_throttle.record_failure(*keys)
        raise
    await auth_throttle.reset(success_key)
    return RecoveryCodesOut(recovery_codes=codes)


@auth_router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_disable(data: MfaCodeIn, session: DB, principal: CurrentUser, request: Request) -> None:
    keys, success_key = await _throttled("mfa-manage", request, str(principal.user_id))
    try:
        await mfa.disable(session, principal, data.code, data.recovery_code)
    except mfa.InvalidCodeError:
        await auth_throttle.record_failure(*keys)
        raise
    await auth_throttle.reset(success_key)


@auth_router.post("/mfa/recovery-codes", response_model=RecoveryCodesOut)
async def mfa_recovery_codes(
    data: MfaCodeIn, session: DB, principal: CurrentUser, request: Request
) -> RecoveryCodesOut:
    keys, success_key = await _throttled("mfa-manage", request, str(principal.user_id))
    try:
        codes = await mfa.regenerate_recovery_codes(session, principal, data.code or "")
    except mfa.InvalidCodeError:
        await auth_throttle.record_failure(*keys)
        raise
    await auth_throttle.reset(success_key)
    return RecoveryCodesOut(recovery_codes=codes)


# --- Single sign-on (OpenID Connect) -----------------------------------------------------------------


def _sso_failure(reason: str) -> RedirectResponse:
    settings = get_settings()
    out = RedirectResponse(
        f"{settings.public_app_url.rstrip('/')}/login?sso_error={quote(reason)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    out.delete_cookie(OIDC_STATE_COOKIE, path=f"{settings.api_prefix}/auth/oidc")
    return out


def _oidc_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=get_settings().oidc_timeout_seconds, follow_redirects=False)


@auth_router.get("/oidc/start", response_model=None)
async def oidc_start() -> RedirectResponse:
    """Send the browser to the identity provider (authorization code flow with PKCE)."""
    settings = get_settings()
    if not settings.oidc_enabled:
        return _sso_failure("sso_disabled")
    state = oidc.new_login_state()
    try:
        async with _oidc_client() as client:
            discovery = await oidc.discover(settings, client)
    except oidc.OidcError as exc:
        return _sso_failure(exc.reason)
    out = RedirectResponse(
        oidc.authorization_url(settings, discovery, state), status_code=status.HTTP_303_SEE_OTHER
    )
    out.set_cookie(
        OIDC_STATE_COOKIE,
        oidc.seal_state(state),
        max_age=oidc.STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="lax",  # the provider sends the browser back with a top-level GET
        path=f"{settings.api_prefix}/auth/oidc",
    )
    return out


@auth_router.get("/oidc/callback", response_model=None)
async def oidc_callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
) -> RedirectResponse:
    """The provider's answer. Signs in an existing, active user whose verified e-mail matches."""
    settings = get_settings()
    if not settings.oidc_enabled:
        return _sso_failure("sso_disabled")
    if error or not code:
        return _sso_failure("sso_cancelled")
    keys, _ = await _throttled("sso", request, "-")
    try:
        login_state = oidc.open_state(request.cookies.get(OIDC_STATE_COOKIE), state)
        async with _oidc_client() as client:
            discovery = await oidc.discover(settings, client)
            claims = await oidc.exchange_code(settings, discovery, code, login_state, client)
        email = oidc.verified_email(claims, settings.oidc_allowed_domains)
        sign_in = await service.sso_sign_in(email, _agent(request))
    except oidc.OidcError as exc:
        await auth_throttle.record_failure(*keys)
        return _sso_failure(exc.reason)
    except AuthenticationError as exc:
        await auth_throttle.record_failure(*keys)
        return _sso_failure(_reason(exc) or "sso_failed")
    out = RedirectResponse(
        # The fragment never reaches a server; the SPA keeps the CSRF token and asks for an access token.
        f"{settings.public_app_url.rstrip('/')}/app#sso={sign_in.response.csrf_token}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    out.delete_cookie(OIDC_STATE_COOKIE, path=f"{settings.api_prefix}/auth/oidc")
    _set_refresh_cookie(out, sign_in)
    return out


@org_router.get("", response_model=list[dict])
async def list_organizations(session: DB, principal: CurrentUser) -> list[dict]:
    """Platform admins see tenant metadata for all organisations; everyone else sees their own."""
    if principal.has(Permission.PLATFORM_ADMIN):
        return await service.list_all_orgs_for_platform()
    org = await service.current_org(session, principal)
    return [OrgOut.model_validate(org).model_dump()]


@org_router.get("/current", response_model=OrgOut)
async def get_current_org(session: DB, principal: CurrentUser) -> OrgOut:
    return OrgOut.model_validate(await service.current_org(session, principal))


@org_router.patch("/current", response_model=OrgOut)
async def update_current_org(
    data: OrgUpdate,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.ORG_MANAGE))],
) -> OrgOut:
    return OrgOut.model_validate(await service.update_org(session, principal, data))


@users_router.get("/roles", response_model=list[RoleOut])
async def list_roles(_: CurrentUser) -> list[RoleOut]:
    return service.list_roles()


@users_router.get("/permissions", response_model=dict[str, str])
async def list_permissions(_: CurrentUser) -> dict[str, str]:
    return service.permission_catalogue()


@users_router.get("/users", response_model=list[UserOut])
async def list_users(
    session: DB, principal: Annotated[Principal, Depends(requires(Permission.USER_READ))]
) -> list[UserOut]:
    return await service.list_users(session, principal)


@users_router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.USER_MANAGE))],
) -> UserOut:
    return await service.update_user(session, principal, user_id, data)


@users_router.get("/invitations", response_model=list[InvitationOut])
async def list_invitations(
    session: DB, principal: Annotated[Principal, Depends(requires(Permission.USER_MANAGE))]
) -> list[InvitationOut]:
    return await service.list_invitations(session, principal)


@users_router.post("/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
async def create_invitation(
    data: InvitationCreate,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.USER_MANAGE))],
) -> InvitationOut:
    return await service.invite(session, principal, data)


@users_router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.USER_MANAGE))],
) -> None:
    await service.revoke_invitation(session, principal, invitation_id)


@users_router.post("/users/{user_id}/mfa/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_mfa(
    user_id: uuid.UUID,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.USER_MANAGE))],
) -> None:
    """Turn off a colleague's two-factor sign-in (lost device) and sign them out everywhere."""
    await mfa.admin_reset(session, principal, user_id)
