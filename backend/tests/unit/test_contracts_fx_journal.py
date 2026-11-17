"""Pure domain: contract lifecycle and coverage, exchange rates, CON-001, journal entries, ECB parsing."""

import csv
import io
import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.errors import BusinessRuleViolation, InvalidStateTransitionError
from app.domain import journal
from app.domain.contracts import (
    ContractAction,
    ContractState,
    ContractTerms,
    covering_contract,
    due_for_expiry,
    due_for_reminder,
    transition,
    validate_terms,
    vendor_contract_status,
)
from app.domain.fx import ECB, MANUAL, Rate, cross_rates, pick_rate, to_base
from app.domain.policy.models import ContractFacts, PolicyConfig
from app.integrations.ecb import RatesUnavailableError, parse_daily
from tests.unit.test_policy_engine import facts, rule_ids, run, vendor

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TODAY = date(2026, 11, 13)


def contract(**overrides: object) -> ContractTerms:
    base = ContractTerms(
        id="c1",
        number="CT-2026-000001",
        vendor_id="v1",
        state=ContractState.ACTIVE,
        starts_on=date(2026, 4, 1),
        ends_on=date(2027, 3, 31),
        categories=("software",),
        value_cap=Decimal("1000000"),
    )
    return replace(base, **overrides)


# --- Contracts ------------------------------------------------------------------------------------


def test_contract_lifecycle_transitions() -> None:
    assert transition(ContractState.DRAFT, ContractAction.ACTIVATE) == ContractState.ACTIVE
    assert transition(ContractState.ACTIVE, ContractAction.EXPIRE) == ContractState.EXPIRED
    assert transition(ContractState.ACTIVE, ContractAction.TERMINATE) == ContractState.TERMINATED
    assert transition(ContractState.DRAFT, ContractAction.TERMINATE) == ContractState.TERMINATED
    for state, action in [
        (ContractState.DRAFT, ContractAction.EXPIRE),
        (ContractState.EXPIRED, ContractAction.ACTIVATE),
        (ContractState.TERMINATED, ContractAction.ACTIVATE),
        (ContractState.ACTIVE, ContractAction.ACTIVATE),
    ]:
        with pytest.raises(InvalidStateTransitionError):
            transition(state, action)


def test_contract_terms_are_validated() -> None:
    validate_terms(TODAY, TODAY, None)
    with pytest.raises(BusinessRuleViolation) as dates:
        validate_terms(TODAY, TODAY - timedelta(days=1), None)
    assert dates.value.details["reason"] == "contract_dates"
    with pytest.raises(BusinessRuleViolation) as cap:
        validate_terms(TODAY, TODAY, Decimal("0"))
    assert cap.value.details["reason"] == "contract_cap"


def test_covering_contract_respects_vendor_category_dates_and_state() -> None:
    active = contract()
    assert covering_contract([active], "v1", "software", TODAY) == active
    assert covering_contract([active], "v2", "software", TODAY) is None
    assert covering_contract([active], "v1", "hardware", TODAY) is None
    assert covering_contract([active], "v1", "software", date(2027, 4, 1)) is None
    assert covering_contract([contract(state=ContractState.DRAFT)], "v1", "software", TODAY) is None
    every_category = contract(id="c2", categories=())
    assert covering_contract([every_category], "v1", "hardware", TODAY) == every_category
    # Two in force: the one ending last wins, deterministically.
    later = contract(id="c3", number="CT-2026-000003", ends_on=date(2027, 9, 30))
    assert covering_contract([active, later], "v1", "software", TODAY) == later


def test_vendor_contract_status_from_records_with_legacy_fallback() -> None:
    assert vendor_contract_status([], "v1", "software", TODAY) is None  # no records: use the vendor field
    assert vendor_contract_status([contract(state=ContractState.DRAFT)], "v1", "software", TODAY) is None
    assert vendor_contract_status([contract()], "v1", "software", TODAY) == "active"
    assert (
        vendor_contract_status([contract(state=ContractState.TERMINATED)], "v1", "software", TODAY)
        == "expired"
    )
    assert (
        vendor_contract_status([contract(ends_on=TODAY - timedelta(days=1))], "v1", "software", TODAY)
        == "expired"
    )
    future = contract(starts_on=TODAY + timedelta(days=10))
    assert vendor_contract_status([future], "v1", "software", TODAY) == "none"
    assert vendor_contract_status([contract()], "v1", "hardware", TODAY) == "none"


def test_expiry_and_one_reminder_window() -> None:
    ends = contract(ends_on=TODAY + timedelta(days=30))
    assert due_for_reminder(ends, TODAY, reminded=False)
    assert not due_for_reminder(ends, TODAY, reminded=True)
    assert not due_for_reminder(ends, TODAY - timedelta(days=1), reminded=False)  # 31 days out
    assert not due_for_reminder(replace(ends, state=ContractState.DRAFT), TODAY, reminded=False)
    assert not due_for_expiry(ends, TODAY)
    assert not due_for_expiry(contract(ends_on=TODAY), TODAY)  # the end date itself is still in force
    assert due_for_expiry(contract(ends_on=TODAY - timedelta(days=1)), TODAY)
    assert not due_for_expiry(
        contract(ends_on=TODAY - timedelta(days=1), state=ContractState.TERMINATED), TODAY
    )


# --- Exchange rates -------------------------------------------------------------------------------


def test_pick_rate_takes_latest_on_or_before_and_prefers_manual() -> None:
    rates = [
        Rate("USD", Decimal("149"), date(2026, 11, 10), ECB),
        Rate("USD", Decimal("150"), date(2026, 11, 12), ECB),
        Rate("USD", Decimal("151"), date(2026, 11, 12), MANUAL),
        Rate("USD", Decimal("152"), date(2026, 11, 14), MANUAL),  # future: ignored
        Rate("EUR", Decimal("165"), date(2026, 11, 13), MANUAL),
    ]
    picked = pick_rate(rates, "USD", TODAY)
    assert picked is not None and picked.rate == Decimal("151") and picked.source == MANUAL
    assert pick_rate(rates, "USD", date(2026, 11, 11)).rate == Decimal("149")  # type: ignore[union-attr]
    assert pick_rate(rates, "GBP", TODAY) is None
    assert pick_rate(rates, "USD", date(2026, 11, 1)) is None


def test_to_base_rounds_half_up_to_the_base_minor_unit() -> None:
    assert to_base(Decimal("1000.00"), Decimal("150.2525"), "JPY") == Decimal("150253")
    assert to_base(Decimal("10.00"), Decimal("0.00666667"), "USD") == Decimal("0.07")
    assert to_base(Decimal("0.50"), Decimal("1"), "JPY") == Decimal("1")


def test_cross_rates_from_euro_reference_rates() -> None:
    table = cross_rates({"USD": Decimal("1.1"), "JPY": Decimal("165")}, "JPY")
    assert table == {"EUR": Decimal("165.00000000"), "USD": Decimal("150.00000000")}
    with pytest.raises(ValueError, match="base currency"):
        cross_rates({"USD": Decimal("1.1")}, "JPY")


# --- Policy: CON-001 ------------------------------------------------------------------------------


def _cap(committed: str, cap: str | None = "1000000", **extra: object) -> ContractFacts:
    base = ContractFacts(
        number="CT-2026-000001",
        value_cap=Decimal(cap) if cap is not None else None,
        committed=Decimal(committed),
        cap_currency="JPY",
    )
    return replace(base, **extra)


def test_con_001_routes_procurement_when_the_cap_would_be_exceeded() -> None:
    within = run(facts("300000", vendor=vendor(contract=_cap("700000"))))
    assert "CON-001" not in rule_ids(within)
    over = run(facts("300001", vendor=vendor(contract=_cap("700000"))))
    hit = next(h for h in over.hits if h.rule_id == "CON-001")
    assert hit.severity == "route"
    assert "procurement_officer" in [s.role.value for s in over.required_approvals]
    assert "CON-001" not in rule_ids(run(facts("300001", vendor=vendor(contract=_cap("700000", None)))))


def test_con_001_routes_when_the_cap_currency_has_no_rate_and_can_be_switched_off() -> None:
    unknown = _cap("0", None, cap_currency="USD", cap_convertible=False)
    assert "CON-001" in rule_ids(run(facts("10000", vendor=vendor(contract=unknown))))
    off = PolicyConfig(route_contract_cap_overrun=False)
    assert "CON-001" not in rule_ids(run(facts("300001", vendor=vendor(contract=_cap("700000"))), off))


# --- Journal ---------------------------------------------------------------------------------------

SETTINGS = journal.LedgerSettings(
    payable_account="2110", input_tax_account="1550", bank_account="1110", default_expense_account="6000"
)
MAPPINGS = [
    journal.AccountMapping("office_supplies", None, "6100"),
    journal.AccountMapping(None, "cc-1", "6200"),
    journal.AccountMapping("office_supplies", "cc-1", "6300"),
]


def invoice(**overrides: object) -> journal.InvoiceDoc:
    base = journal.InvoiceDoc(
        invoice_number="INV-1",
        invoice_date=date(2026, 10, 31),
        vendor_name="Minato Office K.K.",
        registration_number="T1234567890123",
        subtotal=Decimal("30000"),
        tax=Decimal("3000"),
        total=Decimal("33000"),
        currency="JPY",
        category="office_supplies",
        cost_center_id="cc-2",
        cost_center_code="CC-200",
    )
    return replace(base, **overrides)


def payment(**overrides: object) -> journal.PaymentDoc:
    base = journal.PaymentDoc(
        invoice_number="INV-1",
        paid_on=date(2026, 11, 20),
        vendor_name="Minato Office K.K.",
        amount=Decimal("33000"),
        currency="JPY",
        transfer_reference="ZG-20261120-0001",
    )
    return replace(base, **overrides)


def test_ledger_settings_need_every_control_account() -> None:
    assert journal.ledger_settings_from(None) is None
    assert journal.ledger_settings_from({"payable_account": "2110"}) is None
    full = {f: f"A{i}" for i, f in enumerate(journal.ACCOUNT_FIELDS)}
    assert journal.ledger_settings_from({**full, "bank_account": "  "}) is None
    assert journal.ledger_settings_from(full) == journal.LedgerSettings(**full)


def test_expense_account_most_specific_mapping_wins() -> None:
    assert journal.expense_account(MAPPINGS, SETTINGS, "office_supplies", "cc-1") == "6300"
    assert journal.expense_account(MAPPINGS, SETTINGS, "software", "cc-1") == "6200"
    assert journal.expense_account(MAPPINGS, SETTINGS, "office_supplies", "cc-9") == "6100"
    assert journal.expense_account(MAPPINGS, SETTINGS, "software", None) == "6000"
    assert journal.expense_account([], SETTINGS, None, None) == "6000"


def test_build_journal_balanced_entries_in_a_stable_order() -> None:
    lines = journal.build_journal(
        "JE-1",
        [
            invoice(
                invoice_number="INV-2",
                invoice_date=date(2026, 11, 1),
                tax=Decimal("0"),
                total=Decimal("30000"),
            ),
            invoice(),
        ],
        [payment()],
        SETTINGS,
        MAPPINGS,
    )
    entries: dict[str, list[journal.JournalLine]] = {}
    for line in lines:
        entries.setdefault(line.entry_id, []).append(line)
    assert list(entries) == ["JE-1-0001", "JE-1-0002", "JE-1-0003"]
    assert journal.entry_count(lines) == 3
    for entry in entries.values():
        assert sum(x.debit for x in entry) == sum(x.credit for x in entry)
    first = entries["JE-1-0001"]  # INV-1 (31 Oct) before INV-2 (1 Nov)
    assert [(x.account, x.debit, x.credit) for x in first] == [
        ("6100", Decimal("30000"), Decimal("0")),
        ("1550", Decimal("3000"), Decimal("0")),
        ("2110", Decimal("0"), Decimal("33000")),
    ]
    assert first[0].registration_number == "T1234567890123" and first[0].cost_center == "CC-200"
    assert [x.account for x in entries["JE-1-0002"]] == ["6100", "2110"]  # no tax line without tax
    pay = entries["JE-1-0003"]
    assert [(x.account, x.debit, x.credit) for x in pay] == [
        ("2110", Decimal("33000"), Decimal("0")),
        ("1110", Decimal("0"), Decimal("33000")),
    ]
    assert "ZG-20261120-0001" in pay[0].description


def test_invoice_whose_parts_do_not_add_up_is_refused() -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        journal.build_journal("JE-1", [invoice(total=Decimal("33001"))], [], SETTINGS)
    assert exc.value.details["reason"] == "journal_invoice_amounts"


def test_csv_and_json_rendering() -> None:
    lines = journal.build_journal("JE-1", [invoice(vendor_name="=HYPERLINK(1)")], [payment()], SETTINGS)
    rows = list(csv.reader(io.StringIO(journal.render("csv", "JE-1", lines).decode("utf-8"))))
    assert tuple(rows[0]) == journal.CSV_COLUMNS
    assert len(rows) == 1 + len(lines)
    assert rows[1][journal.CSV_COLUMNS.index("vendor")] == "'=HYPERLINK(1)"  # no formula injection
    body = json.loads(journal.render("json", "JE-1", lines))
    assert body["reference"] == "JE-1"
    assert [e["entry_type"] for e in body["entries"]] == ["invoice", "payment"]
    assert body["entries"][0]["lines"][0] == {
        "account": "6000",
        "debit": "30000",
        "credit": "0",
        "description": "Invoice INV-1 expense",
    }
    with pytest.raises(BusinessRuleViolation):
        journal.render("xml", "JE-1", lines)


# --- ECB --------------------------------------------------------------------------------------------


def test_parse_ecb_daily_file() -> None:
    on, rates = parse_daily((FIXTURES / "fx" / "eurofxref-daily.xml").read_bytes())
    assert on == date(2026, 11, 12)
    assert rates == {
        "USD": Decimal("1.1000"),
        "JPY": Decimal("165.00"),
        "GBP": Decimal("0.85000"),
        "CHF": Decimal("0.9500"),
        "SEK": Decimal("11.500"),
    }  # the malformed XXX rate is skipped
    assert cross_rates(rates, "JPY")["USD"] == Decimal("150.00000000")


@pytest.mark.parametrize(
    "body",
    [
        b"not xml",
        b"<Envelope/>",
        (FIXTURES / "einvoice" / "billion_laughs.xml").read_bytes(),
        (FIXTURES / "einvoice" / "external_entity.xml").read_bytes(),
    ],
    ids=["garbage", "no-date", "billion-laughs", "external-entity"],
)
def test_parse_ecb_refuses_bad_or_hostile_documents(body: bytes) -> None:
    with pytest.raises(RatesUnavailableError):
        parse_daily(body)
