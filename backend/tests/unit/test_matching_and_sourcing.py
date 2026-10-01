"""Pure three-way match and sourcing-rank rules (no database)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import RateLimitedError
from app.core.rate_limit import FailureThrottle
from app.domain.matching import (
    InvalidInvoice,
    InvoiceLine,
    OrderLine,
    billed_quantities,
    evaluate_registration,
    evaluate_tax,
    match_invoice,
)
from app.domain.sourcing import QuoteCandidate, rank_quotes

D = Decimal
LINE = OrderLine(id=uuid.uuid4(), line_no=1, quantity=D("10"), unit_price=D("100"))


def _match(quantity: str, price: str = "100", billed: str = "0", accepted: str = "10", **kw) -> object:
    return match_invoice(
        order_lines=[LINE],
        accepted_by_line={LINE.id: D(accepted)},
        billed_by_line={LINE.id: D(billed)},
        invoice_lines=[InvoiceLine(LINE.id, D(quantity), D(price), kw.pop("tax_rate", None))],
        declared_tax=D(kw.pop("tax", "0")),
        currency=kw.pop("currency", "JPY"),
        stated_registration=kw.pop("registration", None),
        vendor_registration=kw.pop("vendor_registration", None),
    )


def test_quantity_is_checked_against_accepted_minus_already_billed() -> None:
    assert _match("4", billed="6").status == "matched"
    assert _match("3", billed="6").line_details[0]["quantity_status"] == "partial"
    over = _match("5", billed="6")
    assert over.status == "mismatch"
    assert over.line_details[0]["quantity_status"] == "over_billed"


def test_price_difference_is_an_exception() -> None:
    result = _match("1", price="101")
    assert result.status == "mismatch"
    assert result.line_details[0]["price_status"] == "mismatch"


def test_invalid_invoice_structure_is_rejected() -> None:
    with pytest.raises(InvalidInvoice):
        match_invoice(
            order_lines=[LINE],
            accepted_by_line={},
            billed_by_line={},
            invoice_lines=[InvoiceLine(uuid.uuid4(), D("1"), D("100"))],
            declared_tax=D(0),
            currency="JPY",
            stated_registration=None,
            vendor_registration=None,
        )
    with pytest.raises(InvalidInvoice):
        evaluate_tax([InvoiceLine(LINE.id, D("1"), D("100"), D("0.05"))], D("5"), "JPY")


@pytest.mark.parametrize(
    ("declared", "status"), [("8", "matched"), ("9", "matched"), ("7", "mismatch"), ("10", "mismatch")]
)
def test_tax_accepts_any_issuer_rounding_per_rate(declared: str, status: str) -> None:
    # 10% of 85 = 8.5 JPY: floor 8, half-up 9, ceiling 9 are all lawful issuer choices.
    lines = [InvoiceLine(LINE.id, D("1"), D("85"), D("0.10"))]
    assert evaluate_tax(lines, D(declared), "JPY")["status"] == status


def test_tax_rounds_once_per_rate_not_per_line() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    lines = [InvoiceLine(a, D("1"), D("105"), D("0.08")), InvoiceLine(b, D("1"), D("105"), D("0.08"))]
    # Per-rate: 8% of 210 = 16.8 → 16..17. Per-line flooring would give 8 + 8 = 16 only.
    result = evaluate_tax(lines, D("17"), "JPY")
    assert result["status"] == "matched"
    assert result["expected_range"] == ["16", "17"]


def test_usd_tax_uses_cents() -> None:
    lines = [InvoiceLine(LINE.id, D("1"), D("10.05"), D("0.10"))]
    assert evaluate_tax(lines, D("1.01"), "USD")["expected_range"] == ["1.00", "1.01"]


def test_registration_rules_apply_to_jpy_only() -> None:
    reg = "T7123456789012"
    assert evaluate_registration(reg, reg, D("10"), "JPY")["status"] == "verified"
    assert evaluate_registration(None, reg, D("10"), "JPY")["status"] == "missing"
    assert evaluate_registration(None, reg, D("0"), "JPY")["status"] == "not_required"
    assert evaluate_registration(reg, None, D("10"), "JPY")["status"] == "mismatch"
    assert evaluate_registration(None, None, D("10"), "USD")["status"] == "not_applicable"
    assert _match("1", tax="10", registration=reg, vendor_registration="T9999999999999").status == "mismatch"
    bad_check_digit = evaluate_registration("T1234567890123", "T1234567890123", D("10"), "JPY")
    assert bad_check_digit["reason"] == "Registration number fails the check-digit test"


def test_void_invoices_do_not_consume_billable_quantity() -> None:
    detail = [{"purchase_order_item_id": str(LINE.id), "invoiced_quantity": "4"}]
    billed = billed_quantities(
        [("matched", detail, D("4")), ("exception_rejected", detail, D("4")), ("mismatch", detail, D("4"))],
        [LINE],
    )
    assert billed[LINE.id] == D("8")
    # Legacy aggregate invoices on a single-line order are attributed to that line.
    assert billed_quantities([("matched", [], D("2"))], [LINE])[LINE.id] == D("2")


def _quote(amount: str, days: int, quality: str = "4", contract: bool = True, **kw) -> QuoteCandidate:
    return QuoteCandidate(
        id=uuid.uuid4(),
        vendor_id=uuid.uuid4(),
        amount=D(amount),
        delivery_days=days,
        quality_score=D(quality),
        contract_compliant=contract,
        valid_until=kw.get("valid_until"),
        vendor_approved=kw.get("vendor_approved", True),
    )


def test_ranking_scores_only_eligible_quotes_deterministically() -> None:
    cheap, slow, expired, blocked, over = (
        _quote("100", 10),
        _quote("120", 2, contract=False),
        _quote("1", 1, valid_until=date(2020, 1, 1)),
        _quote("90", 1, vendor_approved=False),
        _quote("500", 1),
    )
    ranked = rank_quotes(
        [slow, expired, cheap, blocked, over], approved_total=D("200"), today=date(2026, 10, 1)
    )
    eligible = [r for r in ranked if r.eligible]
    assert [r.id for r in eligible] == [cheap.id, slow.id]
    assert [r.rank for r in eligible] == [1, 2]
    reasons = {r.id: r.reasons for r in ranked if not r.eligible}
    assert reasons == {
        expired.id: ["Quote has expired"],
        blocked.id: ["Vendor is no longer approved"],
        over.id: ["Quote exceeds the approved request value"],
    }
    assert (
        rank_quotes([slow, expired, cheap, blocked, over], approved_total=D("200"), today=date(2026, 10, 1))
        == ranked
    )


def test_failure_throttle_blocks_after_limit_and_resets() -> None:
    throttle = FailureThrottle(max_failures=2, window_seconds=60)
    throttle.record_failure("a")
    throttle.check(("a", 2))
    throttle.record_failure("a")
    with pytest.raises(RateLimitedError) as error:
        throttle.check(("a", 2))
    assert error.value.details["retry_after_seconds"] > 0
    throttle.check(("b", 2))
    throttle.reset("a")
    throttle.check(("a", 2))
