"""Encryption for secrets the application stores in its own database (e.g. tenant webhook URLs).

Fernet (AES-128-CBC + HMAC-SHA256). Each secret in PROCURAX_DATA_ENCRYPTION_KEY (comma-separated
for rotation) is turned into a Fernet key with HKDF-SHA256, so any high-entropy string works,
including a platform's generated value. The first secret encrypts; all of them decrypt.
"""

import base64
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings


def _fernet_key(secret: str) -> bytes:
    derived = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"procurax-data-encryption-v1"
    ).derive(secret.encode())
    return base64.urlsafe_b64encode(derived)


@lru_cache(maxsize=8)
def _cipher(configured: str) -> MultiFernet:
    secrets = [k.strip() for k in configured.split(",") if k.strip()]
    return MultiFernet([Fernet(_fernet_key(s)) for s in secrets])


def encrypt(plaintext: str) -> str:
    return _cipher(get_settings().data_encryption_key).encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Raises ValueError when no configured secret can decrypt the value (e.g. it was removed)."""
    try:
        return _cipher(get_settings().data_encryption_key).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored secret cannot be decrypted with the configured keys") from exc
