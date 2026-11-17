"""Sign-in sessions: rotating refresh tokens in an HttpOnly cookie, with reuse detection.

A sign-in starts a session *family*. Each refresh replaces the refresh token (the old one is marked
replaced); presenting a replaced token again after a short grace period means it was copied, so the whole
family is revoked. The access token (15 minutes, kept in memory by the SPA) carries the family id, so
revoking a family also ends its access tokens at the next request.

Only SHA-256 hashes of refresh tokens are stored. The CSRF token for the cookie endpoints is an HMAC of
the family id with the server secret: nothing extra to store, and it changes with every sign-in.
"""

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import AuthenticationError
from app.core.security import hash_opaque_token, new_opaque_token
from app.modules.identity.models import AuthSession, User

SUPERSEDED_GRACE_SECONDS = 30


def csrf_token(family_id: uuid.UUID) -> str:
    secret = get_settings().jwt_secret.encode("utf-8")
    return hmac.new(secret, f"csrf:{family_id}".encode(), hashlib.sha256).hexdigest()


def csrf_matches(family_id: uuid.UUID, presented: str | None) -> bool:
    return bool(presented) and hmac.compare_digest(csrf_token(family_id), presented or "")


@dataclass(frozen=True)
class Issued:
    refresh_token: str
    family_id: uuid.UUID
    csrf_token: str
    expires_at: datetime


def _row(user: User, family_id: uuid.UUID, family_expires_at: datetime, method: str, user_agent: str | None):
    settings = get_settings()
    token, digest = new_opaque_token()
    now = utcnow()
    row = AuthSession(
        org_id=user.org_id,
        user_id=user.id,
        family_id=family_id,
        token_hash=digest,
        method=method,
        created_at=now,
        last_used_at=now,
        expires_at=min(now + timedelta(hours=settings.refresh_idle_hours), family_expires_at),
        family_expires_at=family_expires_at,
        user_agent=(user_agent or "")[:200] or None,
    )
    return token, row


async def start(session: AsyncSession, user: User, method: str, user_agent: str | None) -> Issued:
    family_id = uuid.uuid4()
    family_expires_at = utcnow() + timedelta(days=get_settings().refresh_token_ttl_days)
    token, row = _row(user, family_id, family_expires_at, method, user_agent)
    session.add(row)
    await session.flush()
    return Issued(token, family_id, csrf_token(family_id), row.expires_at)


async def revoke_family(session: AsyncSession, family_id: uuid.UUID, reason: str) -> int:
    result = await session.execute(
        update(AuthSession)
        .where(AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
        .returning(AuthSession.id)
    )
    return len(result.all())


async def revoke_user(session: AsyncSession, user_id: uuid.UUID, reason: str) -> int:
    result = await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
        .returning(AuthSession.id)
    )
    return len(result.all())


async def rotate(
    session: AsyncSession, refresh_token: str | None, presented_csrf: str | None, user_agent: str | None
) -> tuple[User, Issued]:
    """Exchange a refresh token for a new one (same family). Runs in a system session (no tenant yet)."""
    if not refresh_token:
        raise AuthenticationError("No active session", details={"reason": "session_missing"})
    row = await session.scalar(
        select(AuthSession)
        .where(AuthSession.token_hash == hash_opaque_token(refresh_token))
        .with_for_update()
    )
    if row is None:
        raise AuthenticationError("No active session", details={"reason": "session_missing"})
    if not csrf_matches(row.family_id, presented_csrf):
        raise AuthenticationError("Missing or invalid CSRF token", details={"reason": "csrf_invalid"})
    now = utcnow()
    if row.revoked_at is not None:
        raise AuthenticationError("This session was signed out", details={"reason": "session_revoked"})
    if row.replaced_at is not None:
        if (now - row.replaced_at).total_seconds() <= SUPERSEDED_GRACE_SECONDS:
            # Two tabs refreshed at once: the other one won; the browser already holds the new cookie.
            raise AuthenticationError(
                "Session refreshed elsewhere; retry", details={"reason": "session_superseded"}
            )
        await revoke_family(session, row.family_id, "refresh_token_reuse")
        await session.commit()
        raise AuthenticationError("This session was signed out", details={"reason": "session_reuse_detected"})
    if row.expires_at <= now:
        raise AuthenticationError("The session has expired", details={"reason": "session_expired"})
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        await revoke_family(session, row.family_id, "user_inactive")
        await session.commit()
        raise AuthenticationError("Account is not active", details={"reason": "session_revoked"})
    row.replaced_at = now
    token, new_row = _row(user, row.family_id, row.family_expires_at, row.method, user_agent)
    session.add(new_row)
    await session.flush()
    return user, Issued(token, row.family_id, csrf_token(row.family_id), new_row.expires_at)


async def family_of(session: AsyncSession, refresh_token: str | None) -> uuid.UUID | None:
    if not refresh_token:
        return None
    return await session.scalar(
        select(AuthSession.family_id).where(AuthSession.token_hash == hash_opaque_token(refresh_token))
    )


async def list_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[AuthSession]:
    """The live (latest, not revoked, not expired) token of each of the user's session families."""
    rows = await session.scalars(
        select(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.replaced_at.is_(None),
            AuthSession.expires_at > utcnow(),
        )
        .order_by(AuthSession.last_used_at.desc())
    )
    return list(rows)


async def revoke_own(session: AsyncSession, user_id: uuid.UUID, family_id: uuid.UUID) -> bool:
    owner = await session.scalar(
        select(AuthSession.user_id).where(AuthSession.family_id == family_id).limit(1)
    )
    if owner != user_id:
        return False
    await revoke_family(session, family_id, "signed_out_elsewhere")
    return True
