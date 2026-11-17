"""Deterministic field parser for already-extracted invoice text; it does not perform OCR.

`ocr_tolerant=True` adds field-specific clean-up for OCR output: it strips stray punctuation
from code-like fields, maps letter/digit confusions inside amounts, and repairs a registration
number's leading "T" only when the corporate-number check digit confirms the result. A value
that cannot be confirmed is returned as None rather than guessed; humans review every field.

Japanese invoices (適格請求書) are read with Japanese labels as well (請求書番号, 登録番号, 合計, ...), a
full-width colon, 円 amounts and 2026年10月31日 dates; the text is NFKC-normalised first, which folds
full-width digits and letters that OCR returns for Japanese layouts.
"""

from __future__ import annotations

import re
import unicodedata
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
# Japanese labels as printed on qualified invoices; several wordings are common for some fields.
_JA_LABELS = {
    "vendor": ("取引先", "発行者", "請求元"),
    "invoice_number": ("請求書番号", "請求番号"),
    "invoice_date": ("請求日", "発行日"),
    "registration_number": ("登録番号", "適格請求書発行事業者登録番号"),
    "purchase_order_number": ("注文番号", "発注番号"),
    "currency": ("通貨",),
    "quantity": ("数量",),
    "subtotal": ("小計", "税抜金額"),
    "tax": ("消費税", "消費税額"),
    "total": ("合計", "合計金額", "請求金額"),
}
_JA_DATE = re.compile(r"^\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*$")
_AMOUNT_FIELDS = {"quantity", "subtotal", "tax", "total"}
_CODE_FIELDS = {"invoice_number", "purchase_order_number", "invoice_date"}
_NUMBER = re.compile(r"^[¥￥$€£\s]*([0-9][0-9,]*(?:\.[0-9]{1,3})?)[\s]*(?:円|個|点)?\s*$")
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
        cleaned = value.rstrip(_OCR_NOISE).translate(_DIGIT_CONFUSIONS)
        if cleaned.endswith("円") and re.fullmatch(r"[0-9]{1,3}(?:[.,][0-9]{3})+円", cleaned):
            # Yen has no minor unit, so a dot inside a 円 amount can only be a misread thousands comma.
            cleaned = cleaned.replace(".", ",")
        return cleaned
    return value


def _japanese_date(value: str) -> str:
    match = _JA_DATE.match(value)
    if not match:
        return value
    year, month, day = (int(g) for g in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def extract_invoice_text(text: str, *, ocr_tolerant: bool = False) -> dict[str, str | None]:
    """Extract exact labeled lines and normalize numeric fields; missing/ambiguous values remain None."""
    # NFKC folds full-width forms (：, ２０２６, Ｔ) to ASCII; Japanese labels stay as they are.
    text = unicodedata.normalize("NFKC", text)
    output: dict[str, str | None] = {field: None for field in _LABELS}
    for field, label in _LABELS.items():
        labels = "|".join(re.escape(x) for x in (label, *_JA_LABELS.get(field, ())))
        # OCR often drops the colon after a Japanese label; tolerate that for Japanese labels only.
        japanese = "|".join(re.escape(x) for x in _JA_LABELS.get(field, ()))
        prefix = (
            rf"(?:(?:{labels})\s*:\s*|(?:{japanese})\s+)"
            if ocr_tolerant and japanese
            else rf"(?:{labels})\s*:\s*"
        )
        pattern = re.compile(rf"^\s*{prefix}(.*?)\s*$", re.IGNORECASE | re.MULTILINE)
        values = [match.group(1).strip() for match in pattern.finditer(text) if match.group(1).strip()]
        if len(values) != 1:
            continue
        raw = _japanese_date(values[0]) if field == "invoice_date" else values[0]
        if field == "currency" and raw in ("円", "日本円"):
            raw = "JPY"
        cleaned = _clean(field, raw) if ocr_tolerant else raw
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
