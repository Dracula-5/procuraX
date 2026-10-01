"""Deterministic three-way match: purchase order ↔ accepted receipts ↔ invoice.

Pure functions, no I/O. The API layer gathers the PO lines, accepted quantities and quantities
already billed on earlier invoices, then this module decides the match status and explains it.

Rules (each failure is an exception that needs a finance decision before payment):
- Price: each invoice unit price must equal the PO line unit price.
- Quantity: an invoice line may bill at most the accepted quantity not yet billed on earlier,
  non-rejected invoices. Billing less is a valid partial invoice; billing more is over-billing.
- Consumption tax (only when the invoice states tax rates): Japan's Qualified Invoice System
  rounds tax once per rate per invoice, and the issuer may round down, half-up or up. The
  declared tax must therefore fall between the rounded-down and rounded-up totals per rate.
- Registration number (JPY invoices): a stated number must pass the corporate-number check
  digit and equal the vendor master's
  qualified-invoice registration number; tax charged without one is flagged because the
  input tax credit cannot be fully claimed. The public NTA registry is not queried.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from app.domain.money import minor_units
from app.domain.qualified_invoice import is_valid_registration_number

ALLOWED_TAX_RATES = (Decimal("0"), Decimal("0.08"), Decimal("0.10"))
VOID_INVOICE_STATES = frozenset({"exception_rejected", "duplicate"})
PAYABLE_INVOICE_STATES = frozenset({"matched", "exception_approved"})
CENT = Decimal("0.01")
QUANTITY = Decimal("0.001")  # Matches the Numeric(14, 3) quantity columns.


def _qty(value: Decimal) -> str:
    return str(value.quantize(QUANTITY))


@dataclass(frozen=True)
class OrderLine:
    id: uuid.UUID
    line_no: int
    quantity: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class InvoiceLine:
    purchase_order_item_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal | None = None


@dataclass
class MatchResult:
    status: str
    exceptions: list[str]
    line_details: list[dict]
    tax: dict
    qualified_invoice: dict
    subtotal: Decimal
    quantity: Decimal


class InvalidInvoice(ValueError):
    """The invoice is structurally invalid and must not be stored."""


def _money(value: Decimal, currency: str, rounding: str = ROUND_FLOOR) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-minor_units(currency)), rounding=rounding)


def evaluate_tax(lines: list[InvoiceLine], declared_tax: Decimal, currency: str) -> dict:
    """Compare declared tax with per-rate rounding bounds; uncompared when no rates are stated."""
    rates = [line.tax_rate for line in lines]
    if all(rate is None for rate in rates):
        return {"compared": False, "status": "not_compared", "reason": "No tax rates stated on invoice lines"}
    if any(rate is None for rate in rates):
        raise InvalidInvoice("State a tax rate on every invoice line or on none of them")
    groups: dict[Decimal, Decimal] = {}
    for line in lines:
        assert line.tax_rate is not None
        if line.tax_rate not in ALLOWED_TAX_RATES:
            raise InvalidInvoice("Tax rate must be 0, 0.08 (reduced) or 0.10 (standard)")
        groups[line.tax_rate] = groups.get(line.tax_rate, Decimal(0)) + line.quantity * line.unit_price
    low = high = Decimal(0)
    breakdown = []
    for rate in sorted(groups):
        base = groups[rate]
        floor_tax = _money(base * rate, currency, ROUND_FLOOR)
        ceil_tax = _money(base * rate, currency, ROUND_CEILING)
        low += floor_tax
        high += ceil_tax
        breakdown.append(
            {"rate": str(rate), "taxable_amount": str(base.quantize(CENT)), "tax_floor": str(floor_tax)}
        )
    matched = low <= declared_tax <= high
    return {
        "compared": True,
        "status": "matched" if matched else "mismatch",
        "by_rate": breakdown,
        "expected_range": [str(low), str(high)],
        "declared": str(declared_tax),
        "rounding": "once per rate per invoice; floor, half-up or ceiling accepted",
    }


def evaluate_registration(
    stated: str | None, vendor_registration: str | None, declared_tax: Decimal, currency: str
) -> dict:
    if currency != "JPY":
        return {"status": "not_applicable", "reason": "Qualified Invoice System applies to JPY invoices"}
    base = {"stated": stated, "vendor_master": vendor_registration, "registry_checked": False}
    if stated is None:
        if declared_tax > 0:
            return {**base, "status": "missing", "reason": "Tax charged without a registration number"}
        return {**base, "status": "not_required"}
    if not is_valid_registration_number(stated):
        return {**base, "status": "mismatch", "reason": "Registration number fails the check-digit test"}
    if vendor_registration is None:
        return {**base, "status": "mismatch", "reason": "Vendor master has no registration number"}
    if stated != vendor_registration:
        return {**base, "status": "mismatch", "reason": "Registration number differs from vendor master"}
    return {**base, "status": "verified"}


def match_invoice(
    *,
    order_lines: list[OrderLine],
    accepted_by_line: dict[uuid.UUID, Decimal],
    billed_by_line: dict[uuid.UUID, Decimal],
    invoice_lines: list[InvoiceLine],
    declared_tax: Decimal,
    currency: str,
    stated_registration: str | None,
    vendor_registration: str | None,
) -> MatchResult:
    by_id = {line.id: line for line in order_lines}
    seen: set[uuid.UUID] = set()
    exceptions: list[str] = []
    details: list[dict] = []
    for line in invoice_lines:
        po_line = by_id.get(line.purchase_order_item_id)
        if po_line is None:
            raise InvalidInvoice("Invoice line references a line outside this purchase order")
        if po_line.id in seen:
            raise InvalidInvoice("Provide at most one invoice line per purchase order line")
        seen.add(po_line.id)
        accepted = accepted_by_line.get(po_line.id, Decimal(0))
        billed = billed_by_line.get(po_line.id, Decimal(0))
        billable = max(accepted - billed, Decimal(0))
        price_ok = line.unit_price == po_line.unit_price
        if not price_ok:
            exceptions.append(f"Line {po_line.line_no}: unit price differs from the purchase order")
        if line.quantity > billable:
            quantity_status = "over_billed"
            exceptions.append(
                f"Line {po_line.line_no}: invoiced {line.quantity} exceeds accepted quantity not yet "
                f"invoiced ({_qty(billable)})"
            )
        elif line.quantity < billable:
            quantity_status = "partial"
        else:
            quantity_status = "matched"
        details.append(
            {
                "purchase_order_item_id": str(po_line.id),
                "line_no": po_line.line_no,
                "ordered_quantity": _qty(po_line.quantity),
                "accepted_quantity": _qty(accepted),
                "previously_invoiced_quantity": _qty(billed),
                "billable_quantity": _qty(billable),
                "invoiced_quantity": _qty(line.quantity),
                "remaining_billable_quantity": _qty(max(billable - line.quantity, Decimal(0))),
                "po_unit_price": str(po_line.unit_price),
                "invoice_unit_price": str(line.unit_price),
                "tax_rate": str(line.tax_rate) if line.tax_rate is not None else None,
                "line_subtotal": str((line.quantity * line.unit_price).quantize(CENT)),
                "price_status": "matched" if price_ok else "mismatch",
                "quantity_status": quantity_status,
            }
        )
    tax = evaluate_tax(invoice_lines, declared_tax, currency)
    if tax["status"] == "mismatch":
        exceptions.append(
            f"Declared tax {tax['declared']} is outside the expected range "
            f"{tax['expected_range'][0]}–{tax['expected_range'][1]}"
        )
    qualified = evaluate_registration(stated_registration, vendor_registration, declared_tax, currency)
    if qualified["status"] in {"mismatch", "missing"}:
        exceptions.append(f"Qualified invoice: {qualified['reason']}")
    subtotal = sum((line.quantity * line.unit_price for line in invoice_lines), Decimal(0))
    return MatchResult(
        status="mismatch" if exceptions else "matched",
        exceptions=exceptions,
        line_details=details,
        tax=tax,
        qualified_invoice=qualified,
        subtotal=subtotal.quantize(CENT),
        quantity=sum((line.quantity for line in invoice_lines), Decimal(0)),
    )


def billed_quantities(
    prior_invoices: list[tuple[str, list[dict], Decimal]], order_lines: list[OrderLine]
) -> dict[uuid.UUID, Decimal]:
    """Quantity already billed per PO line on earlier invoices that are not void.

    `prior_invoices` holds (match_status, line_match_details, aggregate quantity). Invoices
    recorded before line-level matching have no line details; on a single-line order their
    aggregate quantity is attributed to that line.
    """
    billed: dict[uuid.UUID, Decimal] = {}
    for status, line_details, quantity in prior_invoices:
        if status in VOID_INVOICE_STATES:
            continue
        if line_details:
            for detail in line_details:
                line_id = uuid.UUID(detail["purchase_order_item_id"])
                billed[line_id] = billed.get(line_id, Decimal(0)) + Decimal(detail["invoiced_quantity"])
        elif len(order_lines) == 1:
            only = order_lines[0].id
            billed[only] = billed.get(only, Decimal(0)) + quantity
    return billed
