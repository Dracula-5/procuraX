"""Pure pieces of the auth hardening: TOTP (RFC 6238 vectors), recovery codes, tokens, OIDC helpers."""

import base64
import uuid

import pytest

from app.core.errors import AuthenticationError
from app.core.security import (
    create_access_token,
    create_mfa_challenge,
    decode_access_token,
    decode_mfa_challenge,
)
from app.domain import totp
from app.integrations import oidc

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


@pytest.mark.parametrize(
    ("unix_time", "expected"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_totp_matches_rfc_6238_sha1_vectors(unix_time: int, expected: str) -> None:
    assert totp.code_at(RFC_SECRET, unix_time, digits=8) == expected
    assert totp.code_at(RFC_SECRET, unix_time) == expected[-6:]


def test_totp_accepts_one_step_of_drift_and_never_the_same_step_twice() -> None:
    t = 1_700_000_000
    code = totp.code_at(RFC_SECRET, t)
    step = totp.verify(RFC_SECRET, code, t + 29, None)
    assert step == totp.step_at(t)
    assert totp.verify(RFC_SECRET, code, t + 30, None) == step  # previous step still accepted
    assert totp.verify(RFC_SECRET, code, t + 90, None) is None  # too old
    assert totp.verify(RFC_SECRET, code, t, last_used_step=step) is None  # replay refused
    later = totp.code_at(RFC_SECRET, t + 30)
    assert totp.verify(RFC_SECRET, later, t + 30, last_used_step=step) == step + 1
    for bad in ("", "12345", "1234567", "abcdef"):
        assert totp.verify(RFC_SECRET, bad, t, None) is None


def test_secret_and_provisioning_uri() -> None:
    secret = totp.new_secret()
    assert len(secret) == 32 and set(secret) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    uri = totp.provisioning_uri(secret, "pat@example.com", "ProcuraX")
    assert uri.startswith("otpauth://totp/ProcuraX:pat@example.com?")
    assert f"secret={secret}" in uri and "issuer=ProcuraX" in uri and "digits=6" in uri and "period=30" in uri


def test_recovery_codes_are_single_use_and_peppered() -> None:
    codes = totp.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(len(c) == 11 and c[5] == "-" for c in codes)
    pepper, other = b"p" * 32, b"q" * 32
    stored = [totp.hash_recovery_code(c, pepper) for c in codes]
    assert totp.use_recovery_code(codes[3], stored, other) is None  # needs the server's pepper
    remaining = totp.use_recovery_code(codes[3].upper().replace("-", " "), stored, pepper)
    assert remaining is not None and len(remaining) == 9
    assert totp.use_recovery_code(codes[3], remaining, pepper) is None  # used up


def test_access_token_carries_the_session_and_token_types_do_not_mix() -> None:
    user, org, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token, ttl = create_access_token(user_id=user, org_id=org, session_id=sid)
    claims = decode_access_token(token)
    assert (claims.user_id, claims.org_id, claims.session_id) == (user, org, sid)
    assert ttl == 15 * 60
    challenge, _ = create_mfa_challenge(user_id=user)
    assert decode_mfa_challenge(challenge) == user
    with pytest.raises(AuthenticationError):
        decode_access_token(challenge)  # a half-finished sign-in is not an access token
    with pytest.raises(AuthenticationError):
        decode_mfa_challenge(token)


def test_pkce_challenge_matches_rfc_7636_example() -> None:
    assert (
        oidc.code_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_login_state_is_sealed_bound_and_short_lived() -> None:
    state = oidc.new_login_state(now=1000)
    sealed = oidc.seal_state(state)
    assert state.verifier not in sealed and state.nonce not in sealed
    assert oidc.open_state(sealed, state.state, now=1001) == state
    for sealed_value, returned, now, reason in [
        (None, state.state, 1001, "sso_state_missing"),
        (sealed, "someone-else", 1001, "sso_state_invalid"),
        (sealed[:-4] + "AAAA", state.state, 1001, "sso_state_invalid"),
        (sealed, state.state, 1000 + oidc.STATE_TTL_SECONDS + 1, "sso_state_missing"),
    ]:
        with pytest.raises(oidc.OidcError) as exc:
            oidc.open_state(sealed_value, returned, now=now)
        assert exc.value.reason == reason


def test_only_verified_emails_in_allowed_domains_sign_in() -> None:
    assert oidc.verified_email({"email": "Pat@Example.com", "email_verified": True}, []) == "pat@example.com"
    assert oidc.verified_email({"email": "pat@example.com", "email_verified": True}, ["@example.com"])
    for claims, domains, reason in [
        ({"email": "pat@example.com"}, [], "sso_email_unverified"),
        ({"email": "pat@example.com", "email_verified": "true"}, [], "sso_email_unverified"),
        ({"email_verified": True}, [], "sso_email_unverified"),
        ({"email": "pat@other.test", "email_verified": True}, ["example.com"], "sso_domain_not_allowed"),
    ]:
        with pytest.raises(oidc.OidcError) as exc:
            oidc.verified_email(claims, domains)
        assert exc.value.reason == reason
