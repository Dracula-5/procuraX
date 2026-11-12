"""NTA registry interpretation and Peppol / JP PINT UBL parsing (pure)."""

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.einvoice import EInvoiceError, map_to_order_lines, parse_ubl_invoice
from app.domain.matching import evaluate_registration
from app.domain.qualified_invoice import RegistryRecord, registry_status
from app.integrations.nta_registry import RegistryUnavailableError, parse_announcement

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 11, 13, tzinfo=UTC)
PRICE_1000 = '<cbc:PriceAmount currencyID="JPY">1000</cbc:PriceAmount>'
PRICE_10000_PER_10 = (
    '<cbc:PriceAmount currencyID="JPY">10000</cbc:PriceAmount>'  # ¥10,000 per base quantity of 10
    '<cbc:BaseQuantity unitCode="H87">10</cbc:BaseQuantity>'
)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / "nta" / name).read_text(encoding="utf-8"))


def test_parse_active_revoked_and_missing_announcements() -> None:
    active, payload = parse_announcement("T2010401000001", _fixture("announcement_active.json"), NOW)
    assert active.found and active.name == "株式会社ミナト商事（架空）"
    assert active.registered_on == date(2023, 10, 1) and active.revoked_on is None
    assert payload is not None and payload["latest"] == "1"
    revoked, _ = parse_announcement("T1010401000002", _fixture("announcement_revoked.json"), NOW)
    assert revoked.revoked_on == date(2026, 4, 1)  # the latest record wins
    missing, payload = parse_announcement("T9010401000003", _fixture("announcement_not_found.json"), NOW)
    assert not missing.found and payload is None
    with pytest.raises(RegistryUnavailableError):
        parse_announcement("T9010401000003", {"announcement": "nope"}, NOW)


@pytest.mark.parametrize(
    ("record", "on", "expected"),
    [
        (RegistryRecord("T1", False, NOW), date(2026, 5, 1), "not_found"),
        (
            RegistryRecord("T1", True, NOW, registered_on=date(2024, 1, 1)),
            date(2023, 12, 31),
            "not_yet_registered",
        ),
        (RegistryRecord("T1", True, NOW, registered_on=date(2024, 1, 1)), date(2024, 1, 1), "active"),
        (RegistryRecord("T1", True, NOW, revoked_on=date(2026, 4, 1)), date(2026, 3, 31), "active"),
        (RegistryRecord("T1", True, NOW, revoked_on=date(2026, 4, 1)), date(2026, 4, 1), "revoked"),
        (RegistryRecord("T1", True, NOW, expired_on=date(2025, 1, 1)), date(2025, 6, 1), "expired"),
    ],
)
def test_registry_status_on_the_invoice_date(record: RegistryRecord, on: date, expected: str) -> None:
    assert registry_status(record, on) == expected


def test_registry_evidence_is_recorded_and_can_turn_a_match_into_an_exception() -> None:
    number = "T2010401000001"
    active = RegistryRecord(number, True, NOW, name="X", registered_on=date(2023, 10, 1))
    ok = evaluate_registration(
        number,
        number,
        Decimal("100"),
        "JPY",
        invoice_date=date(2026, 10, 31),
        registry=active,
        registry_source="nta_web_api",
    )
    assert ok["status"] == "verified" and ok["registry_checked"]
    assert ok["registry"]["source"] == "nta_web_api" and ok["registry"]["status"] == "active"
    revoked = RegistryRecord(number, True, NOW, revoked_on=date(2026, 4, 1))
    bad = evaluate_registration(
        number,
        number,
        Decimal("100"),
        "JPY",
        invoice_date=date(2026, 10, 31),
        registry=revoked,
        registry_source="cache",
    )
    assert bad["status"] == "mismatch" and "revoked" in bad["reason"]
    # No registry evidence: the local checks alone decide, and the record says why.
    local = evaluate_registration(number, number, Decimal("100"), "JPY", registry_source="unavailable")
    assert (
        local["status"] == "verified"
        and local["registry"] == {"source": "unavailable"}
        and not local["registry_checked"]
    )


def _sample(**replace: str) -> bytes:
    text = (FIXTURES / "einvoice" / "jp_pint_invoice.xml").read_text(encoding="utf-8")
    for old, new in replace.items():
        text = text.replace(old, new)
    return text.encode()


def test_jp_pint_sample_parses_into_invoice_and_lines() -> None:
    invoice = parse_ubl_invoice(_sample())
    assert invoice.is_jp_pint and invoice.invoice_number == "MINATO-2026-0042"
    assert invoice.issue_date == date(2026, 10, 31) and invoice.currency == "JPY"
    assert invoice.order_reference == "PO-2026-000001"
    assert invoice.supplier_registration == "T2010401000001"
    assert invoice.supplier_name == "株式会社ミナト商事（架空）"
    assert invoice.tax_amount == Decimal("3800")
    assert [(ln.order_line_ref, ln.quantity, ln.unit_price, ln.tax_rate) for ln in invoice.lines] == [
        ("1", Decimal("2"), Decimal("15000"), Decimal("0.1")),
        ("2", Decimal("10"), Decimal("1000"), Decimal("0.08")),
    ]


def test_price_base_quantity_is_honoured() -> None:
    xml = _sample(**{PRICE_1000: PRICE_10000_PER_10})
    assert parse_ubl_invoice(xml).lines[1].unit_price == Decimal("1000")


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ((FIXTURES / "einvoice" / "billion_laughs.xml").read_bytes(), "einvoice_unsafe_xml"),
        ((FIXTURES / "einvoice" / "external_entity.xml").read_bytes(), "einvoice_unsafe_xml"),
        (b"<not-closed", "einvoice_not_xml"),
        (
            b'<CreditNote xmlns="urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"/>',
            "einvoice_not_ubl_invoice",
        ),
        (b"", "einvoice_size"),
    ],
    ids=["billion-laughs", "external-entity", "not-xml", "credit-note", "empty"],
)
def test_unsafe_or_unsupported_documents_are_rejected(content: bytes, reason: str) -> None:
    with pytest.raises(EInvoiceError) as exc:
        parse_ubl_invoice(content)
    assert exc.value.reason == reason


def test_lines_map_to_order_lines_by_reference_or_position() -> None:
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    invoice = parse_ubl_invoice(_sample())
    mapped = map_to_order_lines(invoice, {1: first, 2: second})
    assert [m["purchase_order_item_id"] for m in mapped] == [first, second]
    unreferenced = parse_ubl_invoice(
        _sample(**{"<cbc:LineID>1</cbc:LineID>": "", "<cbc:LineID>2</cbc:LineID>": ""})
    )
    assert [m["purchase_order_item_id"] for m in map_to_order_lines(unreferenced, {2: second, 1: first})] == [
        first,
        second,
    ]
    with pytest.raises(EInvoiceError) as exc:
        map_to_order_lines(invoice, {1: first})  # line 2 is not on this order
    assert exc.value.reason == "einvoice_line_reference"


def test_production_requires_an_application_id_when_the_registry_is_on() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    prod = {
        "env": "prod",
        "jwt_secret": "p" * 48,
        "database_url": "postgresql://u:p@db.example/x",
        "public_app_url": "https://procurax.example",
        "demo_mode": False,
    }
    with pytest.raises(ValidationError, match="PROCURAX_NTA_APPLICATION_ID is required"):
        Settings(**prod, nta_registry_enabled=True)
    Settings(**prod, nta_registry_enabled=True, nta_application_id="issued-by-nta")
