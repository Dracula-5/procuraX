"""TOTP multi-factor authentication: enrolment, the second sign-in step, recovery codes, admin reset.

Secrets are encrypted with the data key (Fernet; key rotation keeps old secrets readable). Recovery codes
are stored as HMAC-SHA256 under a pepper derived from each configured data key, so they verify across a key
rotation. A TOTP code is accepted once (the last accepted time step is stored under a row lock).
"""

import hashlib
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import AuthenticationError, BusinessRuleViolation, ConflictError, NotFoundError
from app.core.principal import Principal
from app.domain import totp
from app.modules.audit import service as audit
from app.modules.identity import sessions
from app.modules.identity.models import User


def now_seconds() -> float:  # replaced in tests
    return time.time()


def _peppers() -> list[bytes]:
    return [
        hashlib.sha256(f"procurax-mfa-recovery:{key.strip()}".encode()).digest()
        for key in get_settings().data_encryption_key.split(",")
        if key.strip()
    ]


def _hash_codes(codes: list[str]) -> list[str]:
    pepper = _peppers()[0]
    return [totp.hash_recovery_code(code, pepper) for code in codes]


def _use_recovery(user: User, code: str) -> bool:
    for pepper in _peppers():
        remaining = totp.use_recovery_code(code, list(user.mfa_recovery_hashes or []), pepper)
        if remaining is not None:
            user.mfa_recovery_hashes = remaining
            return True
    return False


def _use_totp(user: User, secret_encrypted: str | None, code: str) -> bool:
    if not secret_encrypted:
        return False
    step = totp.verify(crypto.decrypt(secret_encrypted), code, now_seconds(), user.mfa_last_step)
    if step is None:
        return False
    user.mfa_last_step = step
    return True


async def _locked_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise NotFoundError("User not found")
    return user


class InvalidCodeError(BusinessRuleViolation):
    pass


def _invalid_code() -> InvalidCodeError:
    return InvalidCodeError("The code is not valid", details={"reason": "mfa_code_invalid"})


async def setup(session: AsyncSession, principal: Principal) -> dict:
    if not get_settings().mfa_available:
        raise ConflictError(
            "Two-factor sign-in needs PROCURAX_DATA_ENCRYPTION_KEY to be set on this server",
            details={"reason": "mfa_unavailable"},
        )
    user = await _locked_user(session, principal.user_id)
    if user.mfa_enabled_at is not None:
        raise ConflictError("Two-factor sign-in is already on", details={"reason": "mfa_already_enabled"})
    secret = totp.new_secret()
    user.mfa_pending_secret_encrypted = crypto.encrypt(secret)
    await session.commit()
    return {
        "secret": secret,
        "otpauth_uri": totp.provisioning_uri(secret, user.email, get_settings().mfa_issuer),
        "digits": totp.DIGITS,
        "period": totp.PERIOD_SECONDS,
    }


async def enable(session: AsyncSession, principal: Principal, code: str) -> list[str]:
    user = await _locked_user(session, principal.user_id)
    if user.mfa_enabled_at is not None:
        raise ConflictError("Two-factor sign-in is already on", details={"reason": "mfa_already_enabled"})
    if not user.mfa_pending_secret_encrypted:
        raise ConflictError("Start the set-up first", details={"reason": "mfa_setup_missing"})
    if not _use_totp(user, user.mfa_pending_secret_encrypted, code):
        raise _invalid_code()
    codes = totp.new_recovery_codes()
    user.mfa_secret_encrypted = user.mfa_pending_secret_encrypted
    user.mfa_pending_secret_encrypted = None
    user.mfa_enabled_at = utcnow()
    user.mfa_recovery_hashes = _hash_codes(codes)
    await audit.record(
        session,
        org_id=user.org_id,
        actor=principal,
        action="user.mfa_enabled",
        entity_type="user",
        entity_id=user.id,
        summary="Two-factor sign-in turned on",
    )
    await session.commit()
    return codes


async def _require_second_factor(user: User, code: str | None, recovery_code: str | None) -> str:
    if user.mfa_enabled_at is None:
        raise ConflictError("Two-factor sign-in is off", details={"reason": "mfa_not_enabled"})
    if code and _use_totp(user, user.mfa_secret_encrypted, code):
        return "totp"
    if recovery_code and _use_recovery(user, recovery_code):
        return "recovery_code"
    raise _invalid_code()


async def disable(
    session: AsyncSession, principal: Principal, code: str | None, recovery_code: str | None
) -> None:
    user = await _locked_user(session, principal.user_id)
    used = await _require_second_factor(user, code, recovery_code)
    _clear(user)
    await audit.record(
        session,
        org_id=user.org_id,
        actor=principal,
        action="user.mfa_disabled",
        entity_type="user",
        entity_id=user.id,
        summary="Two-factor sign-in turned off",
        meta={"confirmed_with": used},
    )
    await session.commit()


async def regenerate_recovery_codes(session: AsyncSession, principal: Principal, code: str) -> list[str]:
    user = await _locked_user(session, principal.user_id)
    await _require_second_factor(user, code, None)
    codes = totp.new_recovery_codes()
    user.mfa_recovery_hashes = _hash_codes(codes)
    await audit.record(
        session,
        org_id=user.org_id,
        actor=principal,
        action="user.mfa_recovery_codes_regenerated",
        entity_type="user",
        entity_id=user.id,
        summary="New recovery codes issued (the old ones no longer work)",
    )
    await session.commit()
    return codes


async def verify_sign_in(
    session: AsyncSession, user_id: uuid.UUID, code: str | None, recovery_code: str | None
) -> User:
    """The second step of a password sign-in. Raises AuthenticationError on a wrong code."""
    user = await _locked_user(session, user_id)
    if not user.is_active or user.deleted_at is not None or user.mfa_enabled_at is None:
        raise AuthenticationError("Sign in again", details={"reason": "mfa_challenge_invalid"})
    try:
        used = await _require_second_factor(user, code, recovery_code)
    except BusinessRuleViolation as exc:
        raise AuthenticationError("The code is not valid", details={"reason": "mfa_code_invalid"}) from exc
    if used == "recovery_code":
        await audit.record(
            session,
            org_id=user.org_id,
            actor=None,
            actor_type="system",
            action="user.mfa_recovery_code_used",
            entity_type="user",
            entity_id=user.id,
            summary=f"Signed in with a recovery code ({len(user.mfa_recovery_hashes or [])} left)",
        )
    session.info["mfa_method"] = "recovery_code" if used == "recovery_code" else "password_mfa"
    return user


def _clear(user: User) -> None:
    user.mfa_secret_encrypted = None
    user.mfa_pending_secret_encrypted = None
    user.mfa_enabled_at = None
    user.mfa_last_step = None
    user.mfa_recovery_hashes = None


async def admin_reset(session: AsyncSession, principal: Principal, user_id: uuid.UUID) -> None:
    """For a lost device: turn MFA off for a colleague and sign them out everywhere."""
    user = await session.scalar(
        select(User).where(User.id == user_id, User.org_id == principal.org_id).with_for_update()
    )
    if user is None:
        raise NotFoundError("User not found")
    if user.id == principal.user_id:
        raise BusinessRuleViolation(
            "Turn off your own two-factor sign-in from your security settings",
            details={"reason": "mfa_reset_self"},
        )
    if user.mfa_enabled_at is None:
        raise ConflictError("Two-factor sign-in is off for this user", details={"reason": "mfa_not_enabled"})
    _clear(user)
    revoked = await sessions.revoke_user(session, user.id, "mfa_reset")
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="user.mfa_reset",
        entity_type="user",
        entity_id=user.id,
        summary=f"Two-factor sign-in reset for {user.email}; {revoked} session(s) signed out",
    )
    await session.commit()
