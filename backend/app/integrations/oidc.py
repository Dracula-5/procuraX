"""OpenID Connect sign-in (authorization code flow with PKCE) against one configured provider.

Behind PROCURAX_OIDC_ENABLED (off by default). Works with any standards-compliant provider (Microsoft
Entra ID, Google Workspace, Okta, Keycloak, ...): the endpoints come from the issuer's discovery document,
and the ID token is verified against the issuer's published keys (signature, issuer, audience, expiry,
nonce). Only an existing, active ProcuraX user whose e-mail matches the verified e-mail claim can sign in;
accounts are never created from SSO (an administrator invites people first).

The browser-held login state (state, nonce, PKCE verifier) travels in an encrypted, short-lived,
HttpOnly cookie, so no server-side table is needed.
"""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt

from app.core import crypto
from app.core.config import Settings

STATE_TTL_SECONDS = 600
_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384"]
_discovery_cache: dict[str, tuple[float, dict]] = {}
_jwks_cache: dict[str, tuple[float, dict]] = {}
_CACHE_SECONDS = 3600


class OidcError(RuntimeError):
    """Sign-in failed; `reason` is a stable code the SPA translates."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class LoginState:
    state: str
    nonce: str
    verifier: str
    expires_at: float


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def new_login_state(now: float | None = None) -> LoginState:
    return LoginState(
        state=secrets.token_urlsafe(24),
        nonce=secrets.token_urlsafe(24),
        verifier=secrets.token_urlsafe(48),
        expires_at=(now or time.time()) + STATE_TTL_SECONDS,
    )


def seal_state(state: LoginState) -> str:
    return crypto.encrypt(json.dumps(state.__dict__))


def open_state(sealed: str | None, returned_state: str | None, now: float | None = None) -> LoginState:
    if not sealed or not returned_state:
        raise OidcError("sso_state_missing", "The sign-in session is missing or has expired")
    try:
        state = LoginState(**json.loads(crypto.decrypt(sealed)))
    except Exception as exc:  # noqa: BLE001 - any tampering or key change means the same thing
        raise OidcError("sso_state_invalid", "The sign-in session is invalid") from exc
    if state.expires_at < (now or time.time()):
        raise OidcError("sso_state_missing", "The sign-in session has expired")
    if not secrets.compare_digest(state.state, returned_state):
        raise OidcError("sso_state_invalid", "The sign-in response does not belong to this session")
    return state


def code_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


async def discover(settings: Settings, client: httpx.AsyncClient) -> dict:
    issuer = (settings.oidc_issuer or "").rstrip("/")
    cached = _discovery_cache.get(issuer)
    if cached and cached[0] > time.time():
        return cached[1]
    try:
        response = await client.get(f"{issuer}/.well-known/openid-configuration")
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError("sso_provider_unavailable", "The identity provider could not be reached") from exc
    if str(document.get("issuer", "")).rstrip("/") != issuer:
        raise OidcError("sso_provider_misconfigured", "The discovery document names a different issuer")
    _discovery_cache[issuer] = (time.time() + _CACHE_SECONDS, document)
    return document


def authorization_url(settings: Settings, discovery: dict, state: LoginState) -> str:
    query = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": settings.oidc_scopes,
        "state": state.state,
        "nonce": state.nonce,
        "code_challenge": code_challenge(state.verifier),
        "code_challenge_method": "S256",
    }
    return f"{discovery['authorization_endpoint']}?{urlencode(query)}"


async def _jwks(uri: str, client: httpx.AsyncClient, *, refresh: bool = False) -> dict:
    cached = _jwks_cache.get(uri)
    if cached and cached[0] > time.time() and not refresh:
        return cached[1]
    try:
        response = await client.get(uri)
        response.raise_for_status()
        keys = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError(
            "sso_provider_unavailable", "The identity provider's keys could not be fetched"
        ) from exc
    _jwks_cache[uri] = (time.time() + _CACHE_SECONDS, keys)
    return keys


async def exchange_code(
    settings: Settings, discovery: dict, code: str, state: LoginState, client: httpx.AsyncClient
) -> dict:
    """Redeem the code and return the verified ID-token claims."""
    try:
        response = await client.post(
            discovery["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oidc_redirect_uri,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret or "",
                "code_verifier": state.verifier,
            },
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise OidcError("sso_provider_unavailable", "The identity provider could not be reached") from exc
    if response.status_code != 200:
        raise OidcError("sso_code_rejected", "The identity provider rejected the sign-in code")
    id_token = response.json().get("id_token")
    if not isinstance(id_token, str):
        raise OidcError("sso_code_rejected", "The identity provider returned no ID token")
    return await verify_id_token(settings, discovery, id_token, state.nonce, client)


async def verify_id_token(
    settings: Settings, discovery: dict, id_token: str, nonce: str, client: httpx.AsyncClient
) -> dict:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise OidcError("sso_token_invalid", "The ID token is malformed") from exc
    if header.get("alg") not in _ALGORITHMS:
        raise OidcError("sso_token_invalid", "The ID token uses an unsupported algorithm")
    keys = await _jwks(discovery["jwks_uri"], client)
    match = [k for k in keys.get("keys", []) if k.get("kid") == header.get("kid")]
    if not match:  # the provider may have rotated its keys
        keys = await _jwks(discovery["jwks_uri"], client, refresh=True)
        match = [k for k in keys.get("keys", []) if k.get("kid") == header.get("kid")]
    if not match:
        raise OidcError("sso_token_invalid", "The ID token's signing key is unknown")
    try:
        key = jwt.PyJWK(match[0]).key
        claims = jwt.decode(
            id_token,
            key=key,
            algorithms=[header["alg"]],
            audience=settings.oidc_client_id,
            issuer=discovery["issuer"],
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=60,
        )
    except jwt.PyJWTError as exc:
        raise OidcError("sso_token_invalid", "The ID token failed verification") from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OidcError("sso_token_invalid", "The ID token was issued for another sign-in")
    return claims


def verified_email(claims: dict, allowed_domains: list[str]) -> str:
    email = str(claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") is not True:
        raise OidcError("sso_email_unverified", "The identity provider did not confirm an e-mail address")
    domain = email.rsplit("@", 1)[-1]
    if allowed_domains and domain not in {d.lower().lstrip("@") for d in allowed_domains}:
        raise OidcError("sso_domain_not_allowed", "This e-mail domain may not sign in with SSO")
    return email
