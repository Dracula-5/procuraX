"""ERP journal export: balanced double-entry lines for matched invoices and paid invoices. Pure, no I/O.

Two entry types:

* Invoice (accounts payable recognised): Dr expense (subtotal), Dr input consumption tax (tax),
  Cr accounts payable (total). Only invoices that matched, or whose mismatch a person approved.
* Payment (bank transfer file produced): Dr accounts payable, Cr bank.

Accounts come from the tenant's ledger settings (control accounts and a default expense account) and
GL account mappings, where a row for a category and/or cost centre narrows the expense account (most
specific wins). Every entry balances or `build_journal` raises. Amounts stay in the document currency;
the currency column tells the ERP which ledger to post to. Nothing here posts anywhere: a person
imports the file into the ERP.
"""

import csv
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.errors import BusinessRuleViolation

FORMATS = ("csv", "json")
ACCOUNT_FIELDS = ("payable_account", "input_tax_account", "bank_account", "default_expense_account")
CSV_COLUMNS = (
    "entry_id",
    "entry_type",
    "date",
    "account",
    "debit",
    "credit",
    "currency",
    "description",
    "document",
    "vendor",
    "registration_number",
    "cost_center",
)


@dataclass(frozen=True)
class LedgerSettings:
    payable_account: str
    input_tax_account: str
    bank_account: str
    default_expense_account: str


def ledger_settings_from(raw: dict | None) -> LedgerSettings | None:
    """The stored settings, or None while any control account is missing."""
    if not raw or any(not str(raw.get(f) or "").strip() for f in ACCOUNT_FIELDS):
        return None
    return LedgerSettings(**{f: str(raw[f]).strip() for f in ACCOUNT_FIELDS})


@dataclass(frozen=True)
class AccountMapping:
    category: str | None
    cost_center_id: str | None
    expense_account: str


def expense_account(
    mappings: Sequence[AccountMapping],
    settings: LedgerSettings,
    category: str | None,
    cost_center_id: str | None,
) -> str:
    """Category + cost centre beats cost centre alone, which beats category alone, then the default."""
    matches = [
        m
        for m in mappings
        if (m.category is None or m.category == category)
        and (m.cost_center_id is None or m.cost_center_id == cost_center_id)
    ]
    if not matches:
        return settings.default_expense_account
    best = max(matches, key=lambda m: (m.cost_center_id is not None, m.category is not None))
    return best.expense_account


@dataclass(frozen=True)
class InvoiceDoc:
    invoice_number: str
    invoice_date: date
    vendor_name: str
    registration_number: str | None
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    currency: str
    category: str | None
    cost_center_id: str | None
    cost_center_code: str | None


@dataclass(frozen=True)
class PaymentDoc:
    invoice_number: str
    paid_on: date
    vendor_name: str
    amount: Decimal
    currency: str
    transfer_reference: str | None


@dataclass(frozen=True)
class JournalLine:
    entry_id: str
    entry_type: str  # "invoice" or "payment"
    entry_date: date
    account: str
    debit: Decimal
    credit: Decimal
    currency: str
    description: str
    document: str
    vendor: str
    registration_number: str
    cost_center: str


@dataclass(frozen=True)
class _Entry:
    """The fields every line of one entry shares."""

    entry_id: str
    entry_type: str
    entry_date: date
    currency: str
    document: str
    vendor: str
    registration_number: str = ""
    cost_center: str = ""

    def line(self, account: str, debit: Decimal, credit: Decimal, description: str) -> JournalLine:
        return JournalLine(
            entry_id=self.entry_id,
            entry_type=self.entry_type,
            entry_date=self.entry_date,
            account=account,
            debit=debit,
            credit=credit,
            currency=self.currency,
            description=description,
            document=self.document,
            vendor=self.vendor,
            registration_number=self.registration_number,
            cost_center=self.cost_center,
        )


ZERO = Decimal("0")


def _invoice_lines(
    entry_id: str, doc: InvoiceDoc, settings: LedgerSettings, mappings: Sequence[AccountMapping]
) -> list[JournalLine]:
    if doc.subtotal + doc.tax != doc.total:
        raise BusinessRuleViolation(
            f"Invoice {doc.invoice_number}: subtotal plus tax does not equal the total",
            details={"reason": "journal_invoice_amounts", "document": doc.invoice_number},
        )
    entry = _Entry(
        entry_id,
        "invoice",
        doc.invoice_date,
        doc.currency,
        doc.invoice_number,
        doc.vendor_name,
        doc.registration_number or "",
        doc.cost_center_code or "",
    )
    account = expense_account(mappings, settings, doc.category, doc.cost_center_id)
    lines = [entry.line(account, doc.subtotal, ZERO, f"Invoice {doc.invoice_number} expense")]
    if doc.tax > 0:
        lines.append(
            entry.line(
                settings.input_tax_account,
                doc.tax,
                ZERO,
                f"Invoice {doc.invoice_number} input consumption tax",
            )
        )
    lines.append(
        entry.line(settings.payable_account, ZERO, doc.total, f"Invoice {doc.invoice_number} payable")
    )
    return lines


def _payment_lines(entry_id: str, doc: PaymentDoc, settings: LedgerSettings) -> list[JournalLine]:
    entry = _Entry(entry_id, "payment", doc.paid_on, doc.currency, doc.invoice_number, doc.vendor_name)
    note = f" ({doc.transfer_reference})" if doc.transfer_reference else ""
    text = f"Payment of invoice {doc.invoice_number}{note}"
    return [
        entry.line(settings.payable_account, doc.amount, ZERO, text),
        entry.line(settings.bank_account, ZERO, doc.amount, text),
    ]


def build_journal(
    reference: str,
    invoices: Sequence[InvoiceDoc],
    payments: Sequence[PaymentDoc],
    settings: LedgerSettings,
    mappings: Sequence[AccountMapping] = (),
) -> list[JournalLine]:
    """Entries in a stable order (invoices by date and number, then payments), each balanced."""
    lines: list[JournalLine] = []
    n = 0
    for inv in sorted(invoices, key=lambda d: (d.invoice_date, d.invoice_number)):
        n += 1
        lines += _invoice_lines(f"{reference}-{n:04d}", inv, settings, mappings)
    for pay in sorted(payments, key=lambda d: (d.paid_on, d.invoice_number)):
        n += 1
        lines += _payment_lines(f"{reference}-{n:04d}", pay, settings)
    for entry_id in {line.entry_id for line in lines}:
        entry = [line for line in lines if line.entry_id == entry_id]
        if sum(line.debit for line in entry) != sum(line.credit for line in entry):  # pragma: no cover
            raise AssertionError(f"Unbalanced journal entry {entry_id}")
    return lines


def entry_count(lines: Sequence[JournalLine]) -> int:
    return len({line.entry_id for line in lines})


def _cell(value: str) -> str:
    # A spreadsheet must not run a vendor name as a formula (CSV injection).
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def render_csv(lines: Sequence[JournalLine]) -> bytes:
    """UTF-8 CSV with a header row, one row per journal line, CRLF line endings."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for line in lines:
        writer.writerow(
            [
                line.entry_id,
                line.entry_type,
                line.entry_date.isoformat(),
                _cell(line.account),
                str(line.debit),
                str(line.credit),
                line.currency,
                _cell(line.description),
                _cell(line.document),
                _cell(line.vendor),
                line.registration_number,
                _cell(line.cost_center),
            ]
        )
    return buffer.getvalue().encode("utf-8")


def render_json(reference: str, lines: Sequence[JournalLine]) -> bytes:
    """{"reference", "entries": [{"entry_id", "entry_type", "date", "currency", "lines": [...]}]}."""
    entries: dict[str, dict] = {}
    for line in lines:
        entry = entries.setdefault(
            line.entry_id,
            {
                "entry_id": line.entry_id,
                "entry_type": line.entry_type,
                "date": line.entry_date.isoformat(),
                "currency": line.currency,
                "document": line.document,
                "vendor": line.vendor,
                "registration_number": line.registration_number or None,
                "cost_center": line.cost_center or None,
                "lines": [],
            },
        )
        entry["lines"].append(
            {
                "account": line.account,
                "debit": str(line.debit),
                "credit": str(line.credit),
                "description": line.description,
            }
        )
    body = {"reference": reference, "entries": list(entries.values())}
    return json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")


def render(fmt: str, reference: str, lines: Sequence[JournalLine]) -> bytes:
    if fmt == "csv":
        return render_csv(lines)
    if fmt == "json":
        return render_json(reference, lines)
    raise BusinessRuleViolation(f"Unknown journal format {fmt!r}", details={"reason": "journal_format"})
