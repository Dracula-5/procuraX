"""Deterministic field parser for already-extracted invoice text; it does not perform OCR.

`ocr_tolerant=True` adds field-specific clean-up for OCR output: it strips stray punctuation
from code-like fields, maps letter/digit confusions inside amounts, and repairs a registration
number's leading "T" only when the corporate-number check digit confirms the result. A value
that cannot be confirmed is returned as None rather than guessed; humans review every field.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.domain.qualified_invoice import is_valid_registration_number

_LABELS = {
    "vendor": "Vendor",
    "invoice_number": "Invoice Number",
    "invoice_date": "Invoice Date",
    "registration_number": "Registration Number",
    "purchase_order_number": "PO Number",
    "currency": "Currency",
    "quantity": "Quantity",
    "subtotal": "Subtotal",
    "tax": "Tax",
    "total": "Total",
}
_AMOUNT_FIELDS = {"quantity", "subtotal", "tax", "total"}
_CODE_FIELDS = {"invoice_number", "purchase_order_number", "invoice_date"}
_NUMBER = re.compile(r"^[¥￥$€£\s]*([0-9][0-9,]*(?:\.[0-9]{1,3})?)[\s]*$")
_OCR_NOISE = " .:;,'`\"|_"
_DIGIT_CONFUSIONS = str.maketrans(
    {"O": "0", "o": "0", "D": "0", "l": "1", "I": "1", "|": "1", "S": "5", "B": "8"}
)
_T_CONFUSIONS = set("T1I7lJ|")


def _registration(value: str) -> str | None:
    compact = re.sub(r"[\s\-]", "", value).strip(_OCR_NOISE)
    if len(compact) == 14 and compact[0] in _T_CONFUSIONS and compact[1:].isdigit():
        candidate = "T" + compact[1:]
        return candidate if is_valid_registration_number(candidate) else None
    return None


def _clean(field: str, value: str) -> str | None:
    if field == "registration_number":
        return _registration(value)
    if field == "currency":
        match = re.match(r"^\s*([A-Z]{3})\b", value)
        return match.group(1) if match else None
    if field in _CODE_FIELDS:
        return value.strip(_OCR_NOISE) or None
    if field in _AMOUNT_FIELDS:
        return value.rstrip(_OCR_NOISE).translate(_DIGIT_CONFUSIONS)
    return value


def extract_invoice_text(text: str, *, ocr_tolerant: bool = False) -> dict[str, str | None]:
    """Extract exact labeled lines and normalize numeric fields; missing/ambiguous values remain None."""
    output: dict[str, str | None] = {field: None for field in _LABELS}
    for field, label in _LABELS.items():
        pattern = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)
        values = [match.group(1).strip() for match in pattern.finditer(text) if match.group(1).strip()]
        if len(values) != 1:
            continue
        cleaned = _clean(field, values[0]) if ocr_tolerant else values[0]
        if cleaned is None:
            continue
        value = cleaned
        if field in _AMOUNT_FIELDS:
            match = _NUMBER.fullmatch(value)
            if match is None:
                continue
            try:
                value = format(Decimal(match.group(1).replace(",", "")).normalize(), "f")
            except InvalidOperation:
                continue
        output[field] = value
    return output
