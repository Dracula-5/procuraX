import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import AuthenticationError

# Argon2id with library defaults (RFC 9106 low-memory profile). The test suite creates
# hundreds of users, so it uses a cheap profile; production never does.
_hasher = (
    PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    if get_settings().env == "test"
    else PasswordHasher()
)

# A valid hash of a random value: verifying against it when the user does not exist keeps
# login timing similar for existing and non-existing accounts (limits user enumeration).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(*, user_id: uuid.UUID, org_id: uuid.UUID) -> tuple[str, int]:
    settings = get_settings()
    ttl = settings.access_token_ttl_minutes * 60
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), ttl


def decode_access_token(token: str) -> tuple[uuid.UUID, uuid.UUID]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub", "org", "typ"]},
        )
        if payload.get("typ") != "access":
            raise AuthenticationError("Invalid token type")
        return uuid.UUID(payload["sub"]), uuid.UUID(payload["org"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise AuthenticationError("Invalid or expired token") from exc


def new_opaque_token() -> tuple[str, str]:
    """Returns (token_for_user, sha256_for_storage). Only the hash is persisted."""
    token = secrets.token_urlsafe(32)
    return token, hash_opaque_token(token)


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
