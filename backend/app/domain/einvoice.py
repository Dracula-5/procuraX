"""Peppol BIS Billing / JP PINT (UBL 2.1 Invoice) import: parse, then map onto purchase-order lines.

Pure functions; no I/O. Parsing uses defusedxml with DTDs forbidden, so external entities and entity
expansion ("billion laughs") are rejected. The result feeds the same three-way match as a manually
entered invoice. Only *receiving* documents is in scope; sending over the Peppol network needs a
certified access point (see docs/einvoicing.md).

Mapping used (UBL 2.1 paths, `cac:`/`cbc:` = Common Aggregate/Basic Components):
  invoice number      cbc:ID
  issue date          cbc:IssueDate
  currency            cbc:DocumentCurrencyCode
  purchase order      cac:OrderReference/cbc:ID           (= ProcuraX PO number)
  supplier reg. no.   cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID (T + 13 digits)
  line → PO line      cac:InvoiceLine/cac:OrderLineReference/cbc:LineID (= PO line number)
  quantity            cac:InvoiceLine/cbc:InvoicedQuantity
  unit price          cac:Price/cbc:PriceAmount ÷ cac:Price/cbc:BaseQuantity (default 1)
  tax rate            cac:Item/cac:ClassifiedTaxCategory/cbc:Percent (10 → 0.10)
  tax amount          cac:TaxTotal/cbc:TaxAmount in the document currency
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

MAX_XML_BYTES = 5 * 1024 * 1024
NS = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
INVOICE_TAG = f"{{{NS['inv']}}}Invoice"
JP_PINT = "urn:peppol:pint:billing-1@jp-1"


class EInvoiceError(ValueError):
    """The document cannot be imported. `reason` is a stable code for the UI."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class EInvoiceLine:
    line_id: str
    order_line_ref: str | None
    description: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal | None


@dataclass(frozen=True)
class EInvoice:
    customization_id: str | None
    invoice_number: str
    issue_date: date
    currency: str
    order_reference: str | None
    supplier_name: str | None
    supplier_registration: str | None
    tax_amount: Decimal
    lines: tuple[EInvoiceLine, ...]

    @property
    def is_jp_pint(self) -> bool:
        return bool(self.customization_id and self.customization_id.startswith(JP_PINT))


def _text(node: Element, path: str) -> str | None:
    found = node.find(path, NS)
    return found.text.strip() if found is not None and found.text and found.text.strip() else None


def _decimal(value: str | None, field: str) -> Decimal:
    if value is None:
        raise EInvoiceError("einvoice_missing_field", f"The e-invoice has no {field}")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise EInvoiceError("einvoice_invalid_value", f"{field} is not a number: {value!r}") from exc


def parse_ubl_invoice(xml: bytes) -> EInvoice:
    if not xml or len(xml) > MAX_XML_BYTES:
        raise EInvoiceError("einvoice_size", "E-invoice XML must be between 1 byte and 5 MiB")
    try:
        root = fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise EInvoiceError(
            "einvoice_unsafe_xml", "DTDs and entities are not accepted in e-invoices"
        ) from exc
    except ParseError as exc:
        raise EInvoiceError("einvoice_not_xml", "The file is not well-formed XML") from exc
    if root.tag != INVOICE_TAG:
        raise EInvoiceError("einvoice_not_ubl_invoice", "Only UBL 2.1 Invoice documents are supported")

    number = _text(root, "cbc:ID")
    issued = _text(root, "cbc:IssueDate")
    currency = _text(root, "cbc:DocumentCurrencyCode")
    if not number or not issued or not currency:
        raise EInvoiceError("einvoice_missing_field", "Invoice number, issue date and currency are required")
    try:
        issue_date = date.fromisoformat(issued)
    except ValueError as exc:
        raise EInvoiceError("einvoice_invalid_value", f"Issue date is not a date: {issued!r}") from exc

    supplier = root.find("cac:AccountingSupplierParty/cac:Party", NS)
    supplier_name = None
    registration = None
    if supplier is not None:
        supplier_name = _text(supplier, "cac:PartyLegalEntity/cbc:RegistrationName") or _text(
            supplier, "cac:PartyName/cbc:Name"
        )
        registration = _text(supplier, "cac:PartyTaxScheme/cbc:CompanyID")

    tax_amount = Decimal(0)
    for total in root.findall("cac:TaxTotal", NS):
        amount = total.find("cbc:TaxAmount", NS)
        if amount is not None and amount.get("currencyID", currency) == currency:
            tax_amount = _decimal(amount.text, "tax amount")
            break

    lines: list[EInvoiceLine] = []
    for node in root.findall("cac:InvoiceLine", NS):
        price = _decimal(_text(node, "cac:Price/cbc:PriceAmount"), "unit price")
        base_quantity = _text(node, "cac:Price/cbc:BaseQuantity")
        if base_quantity is not None:
            base = _decimal(base_quantity, "base quantity")
            if base <= 0:
                raise EInvoiceError("einvoice_invalid_value", "Base quantity must be positive")
            price = price / base
        percent = _text(node, "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent")
        lines.append(
            EInvoiceLine(
                line_id=_text(node, "cbc:ID") or str(len(lines) + 1),
                order_line_ref=_text(node, "cac:OrderLineReference/cbc:LineID"),
                description=_text(node, "cac:Item/cbc:Name") or "",
                quantity=_decimal(_text(node, "cbc:InvoicedQuantity"), "invoiced quantity"),
                unit_price=price,
                tax_rate=(_decimal(percent, "tax percent") / 100) if percent is not None else None,
            )
        )
    if not lines:
        raise EInvoiceError("einvoice_no_lines", "The e-invoice has no invoice lines")

    return EInvoice(
        customization_id=_text(root, "cbc:CustomizationID"),
        invoice_number=number,
        issue_date=issue_date,
        currency=currency,
        order_reference=_text(root, "cac:OrderReference/cbc:ID"),
        supplier_name=supplier_name,
        supplier_registration=registration,
        tax_amount=tax_amount,
        lines=tuple(lines),
    )


def map_to_order_lines(invoice: EInvoice, order_line_numbers: dict[int, str]) -> list[dict]:
    """Invoice lines as ProcuraX invoice-line input, keyed to PO line ids.

    `order_line_numbers` maps a PO line number to its id. Lines are matched by OrderLineReference/LineID;
    when no line carries a reference and the counts agree, they are matched in order.
    """
    refs = [line.order_line_ref for line in invoice.lines]
    if all(ref is None for ref in refs):
        if len(invoice.lines) != len(order_line_numbers):
            raise EInvoiceError(
                "einvoice_line_reference",
                "Lines carry no order-line reference and their number differs from the purchase order",
            )
        targets = [order_line_numbers[n] for n in sorted(order_line_numbers)]
    else:
        targets = []
        for line in invoice.lines:
            try:
                targets.append(order_line_numbers[int(line.order_line_ref or "")])
            except (ValueError, KeyError) as exc:
                raise EInvoiceError(
                    "einvoice_line_reference",
                    f"Line {line.line_id} references order line {line.order_line_ref!r}, which is not on this order",
                ) from exc
    return [
        {
            "purchase_order_item_id": target,
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "tax_rate": line.tax_rate,
        }
        for line, target in zip(invoice.lines, targets, strict=True)
    ]
