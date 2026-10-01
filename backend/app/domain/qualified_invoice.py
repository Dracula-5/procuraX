"""Japan Qualified Invoice System (適格請求書等保存方式) registration numbers.

A corporate registration number is "T" + the 13-digit corporate number (法人番号), whose leading
digit is a check digit over the other twelve: 9 − (Σ Pn·Qn mod 9), where Pn is the n-th digit
counted from the right and Qn is 1 for odd n and 2 for even n. Sole proprietors receive numbers
that follow the same format. The check catches most single-digit keying and OCR errors; it does
not prove the number is registered (that needs the NTA public registry, not integrated here).
"""

import re

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
