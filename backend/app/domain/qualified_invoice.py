"""Japan Qualified Invoice System (適格請求書等保存方式) registration numbers.

A corporate registration number is "T" + the 13-digit corporate number (法人番号), whose leading
digit is a check digit over the other twelve: 9 − (Σ Pn·Qn mod 9), where Pn is the n-th digit
counted from the right and Qn is 1 for odd n and 2 for even n. Sole proprietors receive numbers
that follow the same format. The check catches most single-digit keying and OCR errors; it does
not prove the number is registered. That needs the NTA's public registry: `registry_status` interprets
a record fetched from it (adapter: app/integrations/nta_registry.py) for the invoice date.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

_PATTERN = re.compile(r"^T[0-9]{13}$")


def check_digit(base_twelve_digits: str) -> int:
    total = sum(
        int(digit) * (1 if position % 2 else 2)
        for position, digit in enumerate(reversed(base_twelve_digits), start=1)
    )
    return 9 - total % 9


def is_valid_registration_number(value: str) -> bool:
    if not _PATTERN.fullmatch(value):
        return False
    return int(value[1]) == check_digit(value[2:])


@dataclass(frozen=True)
class RegistryRecord:
    """What the NTA qualified-invoice issuer publication says about one registration number."""

    registration_number: str
    found: bool
    fetched_at: datetime
    name: str | None = None
    registered_on: date | None = None
    revoked_on: date | None = None  # 取消年月日 (cancelled by the tax office)
    expired_on: date | None = None  # 失効年月日 (lapsed, e.g. the business closed)


def registry_status(record: RegistryRecord, on: date) -> str:
    """Whether the number identified an active qualified-invoice issuer on `on` (the invoice date).

    Returns active, not_found, not_yet_registered, revoked or expired. An invoice issued before the
    registration took effect, or after it ended, is not a qualified invoice for input tax credit.
    """
    if not record.found:
        return "not_found"
    if record.registered_on is not None and on < record.registered_on:
        return "not_yet_registered"
    if record.revoked_on is not None and on >= record.revoked_on:
        return "revoked"
    if record.expired_on is not None and on >= record.expired_on:
        return "expired"
    return "active"
