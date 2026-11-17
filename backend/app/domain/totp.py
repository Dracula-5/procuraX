"""Time-based one-time passwords (RFC 6238 / RFC 4226) and recovery codes. Pure functions, no I/O.

HMAC-SHA1, 30-second steps, 6 digits: what every authenticator app (Google Authenticator, Microsoft
Authenticator, 1Password, ...) uses by default. A code is accepted for the current step and one step either
side (clock drift), and never twice: the caller stores the last accepted step and passes it back.

Recovery codes are 10 random base32 characters (50 bits), shown once, stored as HMAC-SHA256 with a server
key (a pepper), so a database leak alone does not reveal them, and each can be used once.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote, urlencode

PERIOD_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1
RECOVERY_CODE_COUNT = 10
_B32 = "abcdefghijklmnopqrstuvwxyz234567"


def new_secret() -> str:
    """A 160-bit secret, base32 without padding (the form authenticator apps accept)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    cleaned = secret.replace(" ", "").upper()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def hotp(key: bytes, counter: int, digits: int = DIGITS) -> str:
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def step_at(unix_time: float) -> int:
    return int(unix_time // PERIOD_SECONDS)


def code_at(secret: str, unix_time: float, digits: int = DIGITS) -> str:
    return hotp(_key(secret), step_at(unix_time), digits)


def verify(secret: str, code: str, unix_time: float, last_used_step: int | None) -> int | None:
    """The matching time step, or None. Steps at or before `last_used_step` are refused (no replay)."""
    candidate = code.replace(" ", "").strip()
    if len(candidate) != DIGITS or not candidate.isdigit():
        return None
    key = _key(secret)
    now = step_at(unix_time)
    for step in range(now - DRIFT_STEPS, now + DRIFT_STEPS + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(hotp(key, step), candidate):
            return step
    return None


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    """otpauth:// URI for QR codes and manual entry (Key Uri Format)."""
    label = quote(f"{issuer}:{account}", safe="@:")
    query = urlencode(
        {"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS, "period": PERIOD_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"


def new_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Codes like `k3m7q-x2c9d`: 10 base32 characters, grouped for reading."""
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(_B32) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def normalize_recovery_code(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch in _B32)


def hash_recovery_code(code: str, pepper: bytes) -> str:
    return hmac.new(pepper, normalize_recovery_code(code).encode("ascii"), hashlib.sha256).hexdigest()


def use_recovery_code(code: str, stored_hashes: list[str], pepper: bytes) -> list[str] | None:
    """The remaining hashes after using `code`, or None when it matches none of them."""
    digest = hash_recovery_code(code, pepper)
    for i, stored in enumerate(stored_hashes):
        if hmac.compare_digest(stored, digest):
            return stored_hashes[:i] + stored_hashes[i + 1 :]
    return None
