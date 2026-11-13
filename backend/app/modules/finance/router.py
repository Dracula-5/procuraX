"""Exchange rates, ledger account settings and the ERP journal export.

The journal export turns matched invoices and paid invoices into balanced journal entries (CSV or
JSON) for a person to import into the ERP. Each document is exported once: the batch locks the
organisation row (one export at a time per tenant) and the included rows (FOR UPDATE), and marks them
with the batch id in the same transaction. Nothing is posted to an ERP from here.
"""

import base64
import hashlib
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import undefer

from app.api.deps import DB, CurrentUser, requires, requires_any
from app.core.config import get_settings
from app.core.db import utcnow
from app.core.errors import BusinessRuleViolation, ConflictError, DomainError, NotFoundError
from app.core.principal import Principal
from app.domain import journal
from app.domain.catalog import SpendCategory
from app.domain.fx import ECB, MANUAL, SUPPORTED_CURRENCIES, cross_rates
from app.domain.rbac import Permission
from app.integrations import ecb
from app.modules.audit import service as audit
from app.modules.contracts.service import local_today
from app.modules.finance.models import ExchangeRate, GLAccountMapping, JournalExport
from app.modules.identity.models import Organization
from app.modules.organization.models import CostCenter
from app.modules.procurement.models import PurchaseRequest
from app.modules.procurement.records import Invoice, Payment, PurchaseOrder
from app.modules.procurement.schemas import Currency
from app.modules.vendors.models import Vendor

router = APIRouter(tags=["finance"])
RateManager = Annotated[Principal, Depends(requires_any(Permission.BUDGET_MANAGE, Permission.ORG_MANAGE))]
LedgerReader = Annotated[Principal, Depends(requires_any(Permission.ORG_MANAGE, Permission.PAYMENT_APPROVE))]
LedgerAdmin = Annotated[Principal, Depends(requires(Permission.ORG_MANAGE))]
Finance = Annotated[Principal, Depends(requires(Permission.PAYMENT_APPROVE))]

# Bounded batches keep the stored file small; run the export again for the rest.
MAX_ENTRIES = 2000
EXPORTABLE_MATCH = ("matched", "exception_approved")


class FeatureDisabledError(DomainError):
    status_code = 409
    code = "feature_disabled"


class ProviderUnavailableError(DomainError):
    status_code = 503
    code = "provider_unavailable"


async def _org(session: DB, principal: Principal, *, lock: bool = False) -> Organization:
    query = select(Organization).where(Organization.id == principal.org_id)
    org = await session.scalar(query.with_for_update() if lock else query)
    assert org is not None
    return org


# --- Exchange rates ------------------------------------------------------------------------------


class RateIn(BaseModel):
    currency: Currency
    rate: Decimal = Field(gt=0, max_digits=18, decimal_places=8)
    valid_on: date


class RateOut(BaseModel):
    id: uuid.UUID
    currency: str
    rate: Decimal
    valid_on: date
    source: str
    created_at: datetime


class RatesOut(BaseModel):
    base_currency: str
    supported_currencies: list[str]
    ecb_enabled: bool
    latest: list[RateOut]  # the rate a submission today would use, per currency
    history: list[RateOut]  # last 60 rows, newest first


def _rate_out(r: ExchangeRate) -> RateOut:
    return RateOut(
        id=r.id,
        currency=r.currency,
        rate=r.rate,
        valid_on=r.valid_on,
        source=r.source,
        created_at=r.created_at,
    )


@router.get("/fx/rates", response_model=RatesOut)
async def list_rates(session: DB, principal: CurrentUser, currency: str | None = None) -> RatesOut:
    org = await _org(session, principal)
    today = local_today(org.timezone)
    query = select(ExchangeRate).where(ExchangeRate.org_id == org.id)
    if currency:
        query = query.where(ExchangeRate.currency == currency.upper())
    history = list(
        await session.scalars(query.order_by(ExchangeRate.valid_on.desc(), ExchangeRate.currency).limit(60))
    )
    latest: dict[str, ExchangeRate] = {}
    for row in await session.scalars(
        select(ExchangeRate)
        .where(ExchangeRate.org_id == org.id, ExchangeRate.valid_on <= today)
        .order_by(ExchangeRate.currency, ExchangeRate.valid_on.desc())
    ):
        best = latest.get(row.currency)
        if best is None or (row.valid_on, row.source == MANUAL) > (best.valid_on, best.source == MANUAL):
            latest[row.currency] = row
    return RatesOut(
        base_currency=org.base_currency,
        supported_currencies=[c for c in SUPPORTED_CURRENCIES if c != org.base_currency],
        ecb_enabled=get_settings().fx_ecb_enabled,
        latest=[_rate_out(latest[c]) for c in sorted(latest)],
        history=[_rate_out(r) for r in history],
    )


async def _upsert_rate(
    session: DB,
    org_id: uuid.UUID,
    currency: str,
    rate: Decimal,
    valid_on: date,
    source: str,
    user_id: uuid.UUID,
) -> None:
    stmt = insert(ExchangeRate).values(
        id=uuid.uuid4(),
        org_id=org_id,
        currency=currency,
        rate=rate,
        valid_on=valid_on,
        source=source,
        created_by_id=user_id,
        created_at=utcnow(),
    )
    await session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_exchange_rates_org_id_currency_valid_on_source",
            set_={
                "rate": stmt.excluded.rate,
                "created_by_id": user_id,
                "created_at": stmt.excluded.created_at,
            },
        )
    )


@router.put("/fx/rates", response_model=RateOut)
async def set_rate(data: RateIn, session: DB, principal: RateManager) -> RateOut:
    """Record (or correct) the manual rate for a day: 1 unit of `currency` = `rate` units of base."""
    org = await _org(session, principal)
    if data.currency == org.base_currency:
        raise BusinessRuleViolation(
            "The base currency needs no exchange rate", details={"reason": "fx_base_currency"}
        )
    if data.valid_on > local_today(org.timezone):
        raise BusinessRuleViolation(
            "A rate cannot be dated in the future", details={"reason": "fx_future_date"}
        )
    previous = await session.scalar(
        select(ExchangeRate.rate).where(
            ExchangeRate.org_id == org.id,
            ExchangeRate.currency == data.currency,
            ExchangeRate.valid_on == data.valid_on,
            ExchangeRate.source == MANUAL,
        )
    )
    await _upsert_rate(session, org.id, data.currency, data.rate, data.valid_on, MANUAL, principal.user_id)
    row = await session.scalar(
        select(ExchangeRate)
        .where(
            ExchangeRate.org_id == org.id,
            ExchangeRate.currency == data.currency,
            ExchangeRate.valid_on == data.valid_on,
            ExchangeRate.source == MANUAL,
        )
        .execution_options(populate_existing=True)
    )
    assert row is not None
    await audit.record(
        session,
        org_id=org.id,
        actor=principal,
        action="exchange_rate.set",
        entity_type="exchange_rate",
        entity_id=row.id,
        summary=f"Exchange rate {data.currency}/{org.base_currency} on {data.valid_on.isoformat()}: {data.rate}",
        changes={
            "rate": {"before": str(previous) if previous is not None else None, "after": str(data.rate)}
        },
    )
    await session.commit()
    return _rate_out(row)


class RefreshOut(BaseModel):
    source: str
    reference_date: date
    updated: list[str]


@router.post("/fx/rates/refresh", response_model=RefreshOut)
async def refresh_rates(session: DB, principal: RateManager) -> RefreshOut:
    """Fetch today's ECB reference rates (PROCURAX_FX_ECB_ENABLED) for every supported currency."""
    settings = get_settings()
    if not settings.fx_ecb_enabled:
        raise FeatureDisabledError(
            "ECB reference rates are switched off; enter rates manually",
            details={"reason": "fx_provider_disabled"},
        )
    org = await _org(session, principal)
    try:
        on, eur_rates = await ecb.fetch_daily(settings)
        table = cross_rates(eur_rates, org.base_currency)
    except (ecb.RatesUnavailableError, ValueError) as exc:
        raise ProviderUnavailableError(str(exc), details={"reason": "fx_provider_unavailable"}) from exc
    updated = sorted(c for c in table if c in SUPPORTED_CURRENCIES)
    for currency in updated:
        await _upsert_rate(session, org.id, currency, table[currency], on, ECB, principal.user_id)
    await audit.record(
        session,
        org_id=org.id,
        actor=principal,
        action="exchange_rate.refreshed",
        entity_type="organization",
        entity_id=org.id,
        summary=f"ECB reference rates of {on.isoformat()} recorded for {', '.join(updated) or 'no currency'}",
        changes={"reference_date": on, "rates": {c: str(table[c]) for c in updated}},
    )
    await session.commit()
    return RefreshOut(source=ECB, reference_date=on, updated=updated)


# --- Ledger settings ------------------------------------------------------------------------------

AccountCode = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[0-9A-Za-z][0-9A-Za-z._\-/ ]*$")]


class LedgerSettingsIn(BaseModel):
    payable_account: AccountCode
    input_tax_account: AccountCode
    bank_account: AccountCode
    default_expense_account: AccountCode


class MappingIn(BaseModel):
    category: SpendCategory | None = None
    cost_center_id: uuid.UUID | None = None
    expense_account: AccountCode


class MappingOut(BaseModel):
    id: uuid.UUID
    category: str | None
    cost_center_id: uuid.UUID | None
    cost_center_code: str | None
    expense_account: str


class LedgerOut(BaseModel):
    settings: LedgerSettingsIn | None
    mappings: list[MappingOut]


async def _ledger(session: DB, org: Organization) -> LedgerOut:
    stored = journal.ledger_settings_from(org.ledger_settings)
    rows = await session.execute(
        select(GLAccountMapping, CostCenter.code)
        .outerjoin(CostCenter, CostCenter.id == GLAccountMapping.cost_center_id)
        .where(GLAccountMapping.org_id == org.id)
        .order_by(GLAccountMapping.category.nulls_first(), CostCenter.code.nulls_first())
    )
    return LedgerOut(
        settings=LedgerSettingsIn(**stored.__dict__) if stored else None,
        mappings=[
            MappingOut(
                id=m.id,
                category=m.category,
                cost_center_id=m.cost_center_id,
                cost_center_code=code,
                expense_account=m.expense_account,
            )
            for m, code in rows
        ],
    )


@router.get("/ledger/settings", response_model=LedgerOut)
async def get_ledger(session: DB, principal: LedgerReader) -> LedgerOut:
    return await _ledger(session, await _org(session, principal))


@router.put("/ledger/settings", response_model=LedgerOut)
async def set_ledger(data: LedgerSettingsIn, session: DB, principal: LedgerAdmin) -> LedgerOut:
    org = await _org(session, principal, lock=True)
    before = org.ledger_settings
    org.ledger_settings = data.model_dump()
    await audit.record(
        session,
        org_id=org.id,
        actor=principal,
        action="ledger.settings_changed",
        entity_type="organization",
        entity_id=org.id,
        summary="Ledger control accounts updated",
        changes={"before": before, "after": org.ledger_settings},
    )
    await session.commit()
    return await _ledger(session, org)


@router.put("/ledger/mappings", response_model=LedgerOut)
async def set_mappings(data: list[MappingIn], session: DB, principal: LedgerAdmin) -> LedgerOut:
    """Replace the expense-account mappings (each row narrows by category, cost centre or both)."""
    if len(data) > 500:
        raise BusinessRuleViolation("At most 500 mappings", details={"reason": "ledger_too_many_mappings"})
    keys = set()
    for m in data:
        if m.category is None and m.cost_center_id is None:
            raise BusinessRuleViolation(
                "A mapping needs a category, a cost centre or both (the default is in the settings)",
                details={"reason": "ledger_mapping_scope"},
            )
        key = (m.category, m.cost_center_id)
        if key in keys:
            raise BusinessRuleViolation(
                "Two mappings have the same category and cost centre",
                details={"reason": "ledger_mapping_duplicate"},
            )
        keys.add(key)
    org = await _org(session, principal, lock=True)
    centre_ids = {m.cost_center_id for m in data if m.cost_center_id}
    if centre_ids:
        found = set(
            await session.scalars(
                select(CostCenter.id).where(CostCenter.org_id == org.id, CostCenter.id.in_(centre_ids))
            )
        )
        if found != centre_ids:
            raise NotFoundError("Cost centre not found")
    existing = list(await session.scalars(select(GLAccountMapping).where(GLAccountMapping.org_id == org.id)))
    for row in existing:
        await session.delete(row)
    await session.flush()
    for m in data:
        session.add(
            GLAccountMapping(
                org_id=org.id,
                category=m.category.value if m.category else None,
                cost_center_id=m.cost_center_id,
                expense_account=m.expense_account,
            )
        )
    await audit.record(
        session,
        org_id=org.id,
        actor=principal,
        action="ledger.mappings_changed",
        entity_type="organization",
        entity_id=org.id,
        summary=f"Expense account mappings replaced ({len(existing)} → {len(data)})",
        changes={
            "mappings": [
                {
                    "category": m.category.value if m.category else None,
                    "cost_center_id": str(m.cost_center_id) if m.cost_center_id else None,
                    "expense_account": m.expense_account,
                }
                for m in data
            ]
        },
    )
    await session.commit()
    return await _ledger(session, org)


# --- Journal export --------------------------------------------------------------------------------


class JournalPendingOut(BaseModel):
    configured: bool
    invoices: int
    payments: int


class JournalExportIn(BaseModel):
    format: Literal["csv", "json"] = "csv"


class JournalExportOut(BaseModel):
    id: uuid.UUID
    reference: str
    format: str
    filename: str
    invoice_count: int
    payment_count: int
    entry_count: int
    sha256: str
    created_by_id: uuid.UUID
    created_at: datetime


class JournalExportFileOut(JournalExportOut):
    content_base64: str
    more_pending: bool


def _export_out(e: JournalExport) -> JournalExportOut:
    return JournalExportOut(
        id=e.id,
        reference=e.reference,
        format=e.format,
        filename=f"{e.reference}.{e.format}",
        invoice_count=e.invoice_count,
        payment_count=e.payment_count,
        entry_count=e.entry_count,
        sha256=e.sha256,
        created_by_id=e.created_by_id,
        created_at=e.created_at,
    )


def _pending_invoices(org_id: uuid.UUID) -> list[ColumnElement[bool]]:
    return [
        Invoice.org_id == org_id,
        Invoice.match_status.in_(EXPORTABLE_MATCH),
        Invoice.journal_export_id.is_(None),
    ]


def _pending_payments(org_id: uuid.UUID) -> list[ColumnElement[bool]]:
    # "exported" = the bank transfer file was produced; that is when the payment leaves the payables.
    return [Payment.org_id == org_id, Payment.status == "exported", Payment.journal_export_id.is_(None)]


@router.get("/journal-exports/pending", response_model=JournalPendingOut)
async def pending_journal(session: DB, principal: Finance) -> JournalPendingOut:
    org = await _org(session, principal)
    invoices = await session.scalar(
        select(func.count()).select_from(Invoice).where(*_pending_invoices(org.id))
    )
    payments = await session.scalar(
        select(func.count()).select_from(Payment).where(*_pending_payments(org.id))
    )
    return JournalPendingOut(
        configured=journal.ledger_settings_from(org.ledger_settings) is not None,
        invoices=int(invoices or 0),
        payments=int(payments or 0),
    )


@router.get("/journal-exports", response_model=list[JournalExportOut])
async def list_journal_exports(session: DB, principal: Finance) -> list[JournalExportOut]:
    rows = await session.scalars(
        select(JournalExport)
        .where(JournalExport.org_id == principal.org_id)
        .order_by(JournalExport.created_at.desc())
        .limit(100)
    )
    return [_export_out(e) for e in rows]


@router.post("/journal-exports", status_code=status.HTTP_201_CREATED, response_model=JournalExportFileOut)
async def create_journal_export(
    data: JournalExportIn, session: DB, principal: Finance
) -> JournalExportFileOut:
    org = await _org(session, principal, lock=True)  # one export at a time per tenant
    settings = journal.ledger_settings_from(org.ledger_settings)
    if settings is None:
        raise BusinessRuleViolation(
            "Configure the ledger accounts first", details={"reason": "ledger_not_configured"}
        )
    invoice_rows = (
        await session.execute(
            select(
                Invoice,
                Vendor.name,
                PurchaseRequest.category,
                PurchaseRequest.cost_center_id,
                CostCenter.code,
            )
            .join(Vendor, Vendor.id == Invoice.vendor_id)
            .join(PurchaseOrder, PurchaseOrder.id == Invoice.purchase_order_id)
            .join(PurchaseRequest, PurchaseRequest.id == PurchaseOrder.purchase_request_id)
            .outerjoin(CostCenter, CostCenter.id == PurchaseRequest.cost_center_id)
            .where(*_pending_invoices(org.id))
            .order_by(Invoice.invoice_date, Invoice.invoice_number)
            .limit(MAX_ENTRIES)
            .with_for_update(of=Invoice)
        )
    ).all()
    room = MAX_ENTRIES - len(invoice_rows)
    payment_rows = (
        (
            await session.execute(
                select(Payment, Invoice.invoice_number, Vendor.name)
                .join(Invoice, Invoice.id == Payment.invoice_id)
                .join(Vendor, Vendor.id == Invoice.vendor_id)
                .where(*_pending_payments(org.id))
                .order_by(Payment.exported_at, Payment.id)
                .limit(room)
                .with_for_update(of=Payment)
            )
        ).all()
        if room > 0
        else []
    )
    if not invoice_rows and not payment_rows:
        raise BusinessRuleViolation(
            "Nothing new to export: no matched invoices or paid invoices since the last export",
            details={"reason": "journal_nothing_to_export"},
        )
    mappings = [
        journal.AccountMapping(
            m.category, str(m.cost_center_id) if m.cost_center_id else None, m.expense_account
        )
        for m in await session.scalars(select(GLAccountMapping).where(GLAccountMapping.org_id == org.id))
    ]
    today = local_today(org.timezone)
    sequence = (
        await session.scalar(
            select(func.count()).select_from(JournalExport).where(JournalExport.org_id == org.id)
        )
        or 0
    ) + 1
    reference = f"JE-{today:%Y%m%d}-{sequence:04d}"
    lines = journal.build_journal(
        reference,
        [
            journal.InvoiceDoc(
                invoice_number=inv.invoice_number,
                invoice_date=inv.invoice_date,
                vendor_name=vendor_name,
                registration_number=inv.registration_number,
                subtotal=inv.subtotal,
                tax=inv.tax,
                total=inv.total,
                currency=inv.currency,
                category=category,
                cost_center_id=str(cc_id) if cc_id else None,
                cost_center_code=cc_code,
            )
            for inv, vendor_name, category, cc_id, cc_code in invoice_rows
        ],
        [
            journal.PaymentDoc(
                invoice_number=invoice_number,
                paid_on=local_today(org.timezone, pay.exported_at) if pay.exported_at else today,
                vendor_name=vendor_name,
                amount=pay.amount,
                currency=pay.currency,
                transfer_reference=pay.export_reference,
            )
            for pay, invoice_number, vendor_name in payment_rows
        ],
        settings,
        mappings,
    )
    content = journal.render(data.format, reference, lines)
    digest = hashlib.sha256(content).hexdigest()
    batch = JournalExport(
        org_id=org.id,
        reference=reference,
        format=data.format,
        invoice_count=len(invoice_rows),
        payment_count=len(payment_rows),
        entry_count=journal.entry_count(lines),
        sha256=digest,
        content=content,
        created_by_id=principal.user_id,
        created_at=utcnow(),
    )
    session.add(batch)
    await session.flush()
    for inv, *_ in invoice_rows:
        inv.journal_export_id = batch.id
    for pay, *_ in payment_rows:
        pay.journal_export_id = batch.id
    await audit.record(
        session,
        org_id=org.id,
        actor=principal,
        action="journal.exported",
        entity_type="journal_export",
        entity_id=batch.id,
        summary=f"Journal export {reference}: {len(invoice_rows)} invoices, {len(payment_rows)} payments "
        f"({batch.entry_count} entries, {data.format.upper()})",
        changes={
            "invoice_ids": [str(inv.id) for inv, *_ in invoice_rows],
            "payment_ids": [str(pay.id) for pay, *_ in payment_rows],
            "sha256": digest,
        },
    )
    await session.commit()
    pending = await pending_journal(session, principal)
    return JournalExportFileOut(
        **_export_out(batch).model_dump(),
        content_base64=base64.b64encode(content).decode("ascii"),
        more_pending=bool(pending.invoices or pending.payments),
    )


@router.get("/journal-exports/{export_id}/download", response_model=None)
async def download_journal_export(export_id: uuid.UUID, session: DB, principal: Finance) -> Response:
    batch = await session.scalar(
        select(JournalExport)
        .options(undefer(JournalExport.content))
        .where(JournalExport.id == export_id, JournalExport.org_id == principal.org_id)
    )
    if batch is None:
        raise NotFoundError("Journal export not found")
    if hashlib.sha256(batch.content).hexdigest() != batch.sha256:
        raise ConflictError(
            "The stored export does not match its recorded hash", details={"reason": "document_integrity"}
        )
    media = "text/csv; charset=utf-8" if batch.format == "csv" else "application/json"
    return Response(
        batch.content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{batch.reference}.{batch.format}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
