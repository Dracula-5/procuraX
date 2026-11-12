"""Zengin name conversion parity with the UI, and field-level errors for the settings screens."""

import json
from pathlib import Path

import pytest

from app.domain.zengin import BankAccount, ZenginError, to_zengin_kana, validate_account, validate_consignor

# Shared with frontend/src/lib/zengin.test.ts, which checks the TypeScript mirror against the same cases.
CASES = Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "zengin.cases.json"
ACCOUNT = BankAccount("0999", "ﾃｽﾄｷﾞﾝｺｳ", "001", "ﾎﾝﾃﾝ", "1", "1234567", "ｶ)ﾐﾅﾄ")


def _cases() -> list[dict]:
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["input"])
def test_python_conversion_matches_the_shared_cases(case: dict) -> None:
    if case["value"] is not None:
        assert to_zengin_kana(case["input"]) == case["value"]
    else:
        with pytest.raises(ZenginError) as exc:
            to_zengin_kana(case["input"])
        assert exc.value.kind == "invalid_characters"
        assert str(exc.value).endswith("".join(case["invalid"]))


def test_shared_cases_cover_both_outcomes() -> None:
    cases = _cases()
    assert any(c["value"] is None for c in cases) and any(c["value"] for c in cases)


@pytest.mark.parametrize(
    ("change", "field", "kind"),
    [
        ({"bank_code": "99"}, None, "format"),  # zero-padded, so short codes are valid
        ({"bank_code": "12a4"}, "bank_code", "format"),
        ({"account_type": "3"}, "account_type", "format"),
        ({"branch_name_kana": "ｱ" * 16}, "branch_name_kana", "too_long"),
        ({"holder_name_kana": "ミナト商事"}, "holder_name_kana", "invalid_characters"),
    ],
)
def test_account_errors_name_the_field(change: dict, field: str | None, kind: str) -> None:
    account = BankAccount(**{**ACCOUNT.__dict__, **change})
    if field is None:
        validate_account(account)
        return
    with pytest.raises(ZenginError) as exc:
        validate_account(account)
    assert (exc.value.field, exc.value.kind) == (field, kind)


def test_consignor_is_checked_against_the_header_record_limits() -> None:
    validate_consignor("1234567890", "ｱ" * 40)
    for code, name, field, kind in [
        ("123456789", "ﾐﾅﾄ", "consignor_code", "format"),
        ("12345678901", "ﾐﾅﾄ", "consignor_code", "format"),
        ("1234567890", "ｱ" * 41, "consignor_name_kana", "too_long"),
        ("1234567890", "東京", "consignor_name_kana", "invalid_characters"),
    ]:
        with pytest.raises(ZenginError) as exc:
            validate_consignor(code, name)
        assert (exc.value.field, exc.value.kind) == (field, kind)
