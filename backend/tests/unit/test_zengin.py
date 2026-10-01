"""Zengin 総合振込 record layout and character rules."""

from datetime import date
from decimal import Decimal

import pytest

from app.domain.zengin import BankAccount, Transfer, ZenginError, build_transfer_file, to_zengin_kana

PAYEE = BankAccount("0999", "ﾃｽﾄｷﾞﾝｺｳ", "001", "ﾎﾝﾃﾝ", "1", "1234567", "ｶ)ﾐﾅﾄｼﾞﾑ")


def test_names_are_converted_to_half_width_bank_characters() -> None:
    assert to_zengin_kana("カブシキガイシャ　ミナト") == "ｶﾌﾞｼｷｶﾞｲｼﾔ ﾐﾅﾄ"  # small kana enlarged
    assert to_zengin_kana("ぎんこう") == "ｷﾞﾝｺｳ"
    assert to_zengin_kana("ｶ)abc-1") == "ｶ)ABC-1"
    with pytest.raises(ZenginError, match="株式会社"):
        to_zengin_kana("株式会社ミナト")


def test_file_has_fixed_120_byte_records_and_matching_trailer() -> None:
    content, records = build_transfer_file(
        consignor_code="1234567890",
        consignor_name_kana="ﾌﾟﾛｷｭﾗｸｽ",
        transfer_date=date(2026, 10, 30),
        payer=PAYEE,
        transfers=[Transfer(PAYEE, Decimal("110000"), "INV-1"), Transfer(PAYEE, Decimal("5500"), "INV-2")],
    )
    lines = content.split(b"\r\n")[:-1]
    assert [len(line) for line in lines] == [120] * 5
    assert [line[:1] for line in lines] == [b"1", b"2", b"2", b"8", b"9"]
    header = lines[0].decode("cp932")
    assert header[1:3] == "21" and header[14:54].rstrip() == "ﾌﾟﾛｷﾕﾗｸｽ" and header[54:58] == "1030"
    assert lines[1].decode("cp932")[80:90] == "0000110000"
    assert lines[3].decode("cp932")[1:19] == "000002" + "000000115500"
    assert len(records) == 2


@pytest.mark.parametrize("amount", ["0", "-1", "10.5", "10000000000"])
def test_invalid_amounts_are_rejected(amount: str) -> None:
    with pytest.raises(ZenginError):
        build_transfer_file(
            consignor_code="1234567890",
            consignor_name_kana="ﾃｽﾄ",
            transfer_date=date(2026, 10, 30),
            payer=PAYEE,
            transfers=[Transfer(PAYEE, Decimal(amount))],
        )
