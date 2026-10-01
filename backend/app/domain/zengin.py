"""Zengin (全銀協) 総合振込 transfer file: the domestic bulk-payment upload accepted by Japanese banks.

Pure functions, no I/O. A file is a header record, one data record per transfer, a trailer and
an end record, each exactly 120 bytes in Shift_JIS, separated by CRLF. Names must be written
in half-width katakana, upper-case ASCII letters, digits, space and a small symbol set; this
module converts full-width katakana and hiragana and rejects anything else (for example kanji),
because a bank rejects the whole file when one record holds an invalid character.

ProcuraX produces the file for a human to upload to the bank. It never moves money itself.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

RECORD_LENGTH = 120
_FULL = "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲンヰヱ"
_HALF = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜｦﾝｲｴ"
_SMALL = dict(zip("ァィゥェォッャュョヮ", "ｱｲｳｴｵﾂﾔﾕﾖﾜ", strict=True))
_TO_HALF = {**dict(zip(_FULL, _HALF, strict=True)), **_SMALL, "゙": "ﾞ", "゚": "ﾟ", "ー": "-"}
_ALLOWED = set(_HALF) | set("ﾞﾟ") | set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ().-/,\\｢｣")


class ZenginError(ValueError):
    """A value cannot be represented in a Zengin record."""


def to_zengin_kana(text: str) -> str:
    """Convert a name to the Zengin character set or raise ZenginError naming the bad characters."""
    # NFKC folds half-width kana and full-width ASCII to standard forms; NFD then splits ガ into カ + ゛.
    normalized = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", text)).upper()
    out = []
    invalid = []
    for char in normalized:
        if "ぁ" <= char <= "ゖ":  # hiragana → katakana
            char = chr(ord(char) + 0x60)
        mapped = _TO_HALF.get(char, char)
        if all(piece in _ALLOWED for piece in mapped):
            out.append(mapped)
        else:
            invalid.append(char)
    if invalid:
        raise ZenginError(f"Not allowed in a bank transfer name: {''.join(dict.fromkeys(invalid))}")
    return "".join(out)


def _alnum(value: str, length: int, label: str) -> str:
    if not value.isdigit() or len(value) > length:
        raise ZenginError(f"{label} must be up to {length} digits")
    return value.zfill(length)


def _text(value: str, length: int, label: str) -> str:
    kana = to_zengin_kana(value)
    if len(kana) > length:
        raise ZenginError(f"{label} is longer than {length} characters after conversion: {kana}")
    return kana.ljust(length)


@dataclass(frozen=True)
class BankAccount:
    bank_code: str  # 4 digits (金融機関コード)
    bank_name_kana: str
    branch_code: str  # 3 digits (支店コード)
    branch_name_kana: str
    account_type: str  # 1 普通 (ordinary), 2 当座 (checking), 4 貯蓄 (savings)
    account_number: str  # up to 7 digits
    holder_name_kana: str


@dataclass(frozen=True)
class Transfer:
    payee: BankAccount
    amount: Decimal
    customer_code: str = ""  # 顧客コード: we use the invoice reference for reconciliation


def validate_account(account: BankAccount) -> None:
    _alnum(account.bank_code, 4, "Bank code")
    _alnum(account.branch_code, 3, "Branch code")
    _alnum(account.account_number, 7, "Account number")
    if account.account_type not in {"1", "2", "4"}:
        raise ZenginError("Account type must be 1 (ordinary), 2 (checking) or 4 (savings)")
    _text(account.bank_name_kana, 15, "Bank name")
    _text(account.branch_name_kana, 15, "Branch name")
    _text(account.holder_name_kana, 30, "Account holder")


def header_record(
    consignor_code: str, consignor_name_kana: str, transfer_date: date, payer: BankAccount
) -> str:
    record = (
        "1"  # data type: header
        + "21"  # record kind: 総合振込 (general transfer)
        + "0"  # code set: JIS/Shift_JIS
        + _alnum(consignor_code, 10, "Consignor code")
        + _text(consignor_name_kana, 40, "Consignor name")
        + transfer_date.strftime("%m%d")
        + _alnum(payer.bank_code, 4, "Payer bank code")
        + _text(payer.bank_name_kana, 15, "Payer bank name")
        + _alnum(payer.branch_code, 3, "Payer branch code")
        + _text(payer.branch_name_kana, 15, "Payer branch name")
        + payer.account_type
        + _alnum(payer.account_number, 7, "Payer account number")
        + " " * 17
    )
    assert len(record) == RECORD_LENGTH
    return record


def data_record(transfer: Transfer) -> str:
    payee = transfer.payee
    if transfer.amount <= 0 or transfer.amount != transfer.amount.to_integral_value():
        raise ZenginError("Transfer amounts must be positive whole yen")
    amount = int(transfer.amount)
    if amount > 9_999_999_999:
        raise ZenginError("Transfer amount exceeds the 10-digit field")
    customer = "".join(ch for ch in transfer.customer_code.upper() if ch.isascii() and ch.isalnum())[:10]
    record = (
        "2"
        + _alnum(payee.bank_code, 4, "Bank code")
        + _text(payee.bank_name_kana, 15, "Bank name")
        + _alnum(payee.branch_code, 3, "Branch code")
        + _text(payee.branch_name_kana, 15, "Branch name")
        + " " * 4  # clearing house number (unused for electronic transfers)
        + payee.account_type
        + _alnum(payee.account_number, 7, "Account number")
        + _text(payee.holder_name_kana, 30, "Account holder")
        + f"{amount:010d}"
        + "0"  # new-code flag
        + customer.ljust(10)
        + " " * 10  # customer code 2
        + "7"  # transfer method: electronic (電信)
        + " "  # identification flag
        + " " * 7
    )
    assert len(record) == RECORD_LENGTH
    return record


def build_transfer_file(
    *,
    consignor_code: str,
    consignor_name_kana: str,
    transfer_date: date,
    payer: BankAccount,
    transfers: list[Transfer],
) -> tuple[bytes, list[str]]:
    """Return the Shift_JIS file bytes and the data records (kept as an audit snapshot)."""
    if not transfers:
        raise ZenginError("No transfers to export")
    data = [data_record(t) for t in transfers]
    total = sum(int(t.amount) for t in transfers)
    if len(data) > 999_999 or total > 999_999_999_999:
        raise ZenginError("File exceeds trailer count or amount limits")
    lines = [
        header_record(consignor_code, consignor_name_kana, transfer_date, payer),
        *data,
        "8" + f"{len(data):06d}" + f"{total:012d}" + " " * 101,
        "9" + " " * 119,
    ]
    encoded = [line.encode("cp932") for line in lines]
    if any(len(line) != RECORD_LENGTH for line in encoded):
        raise ZenginError("A record is not 120 bytes in Shift_JIS")
    return b"\r\n".join(encoded) + b"\r\n", data
