"""Purchase order, receipt, invoice and live spend endpoints."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Body, Depends, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, func, or_, select

from app.api.deps import DB, CurrentUser, requires
from app.core.db import utcnow
from app.core.errors import BusinessRuleViolation, ConflictError, NotFoundError
from app.core.principal import Principal
from app.core.sequences import next_document_number
from app.domain.catalog import SpendCategory
from app.domain.matching import (
    PAYABLE_INVOICE_STATES,
    VOID_INVOICE_STATES,
    InvalidInvoice,
    InvoiceLine,
    OrderLine,
    billed_quantities,
    match_invoice,
)
from app.domain.rbac import Permission
from app.domain.sourcing import METHOD as SOURCING_METHOD
from app.domain.sourcing import WEIGHTS as SOURCING_WEIGHTS
from app.domain.sourcing import QuoteCandidate, RankedQuote, rank_quotes
from app.domain.workflow.purchase_request import PRStatus
from app.modules.audit import service as audit
from app.modules.organization.models import Department
from app.modules.procurement.models import PurchaseRequest
from app.modules.procurement.records import (
    GoodsReceipt,
    Invoice,
    Payment,
    PurchaseOrder,
    PurchaseOrderItem,
    VendorQuote,
)
from app.modules.vendors.models import Vendor

router = APIRouter(tags=["purchase-to-pay"])
Procurement = Annotated[Principal, Depends(requires(Permission.VENDOR_MANAGE))]


class ReceiptIn(BaseModel):
    received_on: date
    purchase_order_item_id: uuid.UUID | None = None
    quantity: Decimal = Field(gt=0)
    damaged_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    comments: str = Field(default="", max_length=2000)
    evidence_reference: str | None = Field(default=None, max_length=500)


class InvoiceLineIn(BaseModel):
    purchase_order_item_id: uuid.UUID
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    # Consumption tax rate stated for the line: 0, 0.08 (reduced) or 0.10 (standard).
    tax_rate: Decimal | None = None


class InvoiceIn(BaseModel):
    invoice_number: str = Field(min_length=1, max_length=100)
    invoice_date: date
    registration_number: str | None = Field(default=None, pattern=r"^T[0-9]{13}$")
    quantity: Decimal | None = Field(default=None, gt=0)
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax: Decimal = Field(default=Decimal("0"), ge=0)
    items: list[InvoiceLineIn] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_line_or_aggregate(self) -> "InvoiceIn":
        if self.items is None and (self.quantity is None or self.subtotal is None):
            raise ValueError("Provide invoice line items or aggregate quantity and subtotal")
        return self


class QuoteLineIn(BaseModel):
    line_no: int = Field(ge=1)
    unit_price: Decimal = Field(ge=0)


class QuoteIn(BaseModel):
    vendor_id: uuid.UUID
    amount: Decimal = Field(ge=0)
    delivery_days: int = Field(ge=0, le=3650)
    quality_score: Decimal = Field(default=Decimal("3"), ge=0, le=5)
    contract_compliant: bool = False
    quote_valid_until: date | None = None
    notes: str = Field(default="", max_length=2000)
    # Supplier unit price per request line. Optional; without it the PO apportions the total.
    line_prices: list[QuoteLineIn] | None = Field(default=None, min_length=1)


class AwardIn(BaseModel):
    # Required when the awarded quote is not the top-ranked eligible quote.
    override_reason: str | None = Field(default=None, max_length=2000)


class POTransitionIn(BaseModel):
    status: str
    reason: str | None = Field(default=None, max_length=2000)


class PaymentDecisionIn(BaseModel):
    decision: str
    comment: str = Field(min_length=3, max_length=2000)


class InvoiceExceptionDecisionIn(BaseModel):
    decision: str
    comment: str = Field(min_length=5, max_length=2000)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


@router.post("/purchase-requests/{request_id}/quotes", status_code=status.HTTP_201_CREATED)
async def add_quote(request_id: uuid.UUID, data: QuoteIn, session: DB, principal: Procurement) -> dict:
    request = await session.scalar(
        select(PurchaseRequest).where(
            PurchaseRequest.id == request_id, PurchaseRequest.org_id == principal.org_id
        )
    )
    if request is None:
        raise NotFoundError("Purchase request not found")
    if request.status not in {PRStatus.PENDING_APPROVAL.value, PRStatus.APPROVED.value}:
        raise BusinessRuleViolation("Quotes can only be recorded for submitted requests")
    vendor = await session.scalar(
        select(Vendor).where(
            Vendor.id == data.vendor_id,
            Vendor.org_id == principal.org_id,
            Vendor.deleted_at.is_(None),
            Vendor.status == "approved",
        )
    )
    if vendor is None:
        raise NotFoundError("Approved vendor not found")
    line_prices: list[dict] = []
    if data.line_prices is not None:
        request_lines = {item.line_no: item for item in request.items}
        supplied = [line.line_no for line in data.line_prices]
        if len(set(supplied)) != len(supplied) or set(supplied) != set(request_lines):
            raise BusinessRuleViolation("Quote line prices must cover each request line exactly once")
        priced_total = _money(
            sum(
                (request_lines[line.line_no].quantity * line.unit_price for line in data.line_prices),
                Decimal(0),
            )
        )
        if priced_total != _money(data.amount):
            raise BusinessRuleViolation(
                f"Quote amount must equal the sum of quantity × line unit price ({priced_total})"
            )
        line_prices = [
            {"line_no": line.line_no, "unit_price": str(_money(line.unit_price))}
            for line in sorted(data.line_prices, key=lambda line: line.line_no)
        ]
    quote = VendorQuote(
        org_id=principal.org_id,
        purchase_request_id=request.id,
        vendor_id=vendor.id,
        amount=data.amount,
        currency=request.currency,
        delivery_days=data.delivery_days,
        quality_score=data.quality_score,
        contract_compliant=data.contract_compliant,
        quote_valid_until=data.quote_valid_until,
        notes=data.notes,
        line_prices=line_prices,
        received_at=utcnow(),
    )
    session.add(quote)
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="vendor_quote.recorded",
        entity_type="purchase_request",
        entity_id=request.id,
        summary=f"Recorded quote from {vendor.name} for {request.number}",
        changes={
            "amount": data.amount,
            "delivery_days": data.delivery_days,
            "line_prices": line_prices or None,
        },
    )
    await session.commit()
    return {
        "id": quote.id,
        "vendor_id": vendor.id,
        "vendor_name": vendor.name,
        "amount": quote.amount,
        "currency": quote.currency,
        "delivery_days": quote.delivery_days,
        "line_prices": quote.line_prices,
    }


async def _ranked_quotes(
    session, org_id: uuid.UUID, request: PurchaseRequest
) -> tuple[list[tuple[VendorQuote, str]], list[RankedQuote]]:
    rows = (
        await session.execute(
            select(VendorQuote, Vendor.name, Vendor.status, Vendor.deleted_at)
            .join(Vendor, Vendor.id == VendorQuote.vendor_id)
            .where(VendorQuote.org_id == org_id, VendorQuote.purchase_request_id == request.id)
            .order_by(VendorQuote.created_at)
        )
    ).all()
    ranking = rank_quotes(
        [
            QuoteCandidate(
                id=quote.id,
                vendor_id=quote.vendor_id,
                amount=quote.amount,
                delivery_days=quote.delivery_days,
                quality_score=quote.quality_score,
                contract_compliant=quote.contract_compliant,
                valid_until=quote.quote_valid_until,
                vendor_approved=vendor_status == "approved" and deleted_at is None,
            )
            for quote, _, vendor_status, deleted_at in rows
        ],
        approved_total=request.estimated_total,
        today=date.today(),
    )
    return [(quote, name) for quote, name, _, _ in rows], ranking


@router.get("/purchase-requests/{request_id}/quotes")
async def compare_quotes(
    request_id: uuid.UUID,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.PR_READ_ALL))],
) -> dict:
    request = await session.scalar(
        select(PurchaseRequest).where(
            PurchaseRequest.id == request_id, PurchaseRequest.org_id == principal.org_id
        )
    )
    if request is None:
        raise NotFoundError("Purchase request not found")
    rows, ranking = await _ranked_quotes(session, principal.org_id, request)
    quotes = {quote.id: (quote, name) for quote, name in rows}
    enriched = []
    for ranked in ranking:
        quote, vendor_name = quotes[ranked.id]
        enriched.append(
            {
                "id": quote.id,
                "vendor_id": quote.vendor_id,
                "vendor_name": vendor_name,
                "amount": quote.amount,
                "currency": quote.currency,
                "delivery_days": quote.delivery_days,
                "quality_score": quote.quality_score,
                "contract_compliant": quote.contract_compliant,
                "quote_valid_until": quote.quote_valid_until,
                "line_prices": quote.line_prices,
                "is_awarded": quote.is_awarded,
                "award_context": quote.award_context,
                "eligible": ranked.eligible,
                "rank": ranked.rank,
                "score": ranked.score,
                "reasons": ranked.reasons,
            }
        )
    recommended = next((r.id for r in ranking if r.rank == 1), None)
    return {
        "method": "transparent weighted rule baseline",
        "method_version": SOURCING_METHOD,
        "weights": {key: str(value) for key, value in SOURCING_WEIGHTS.items()},
        "recommended_quote_id": recommended,
        "quotes": enriched,
    }


@router.post("/purchase-requests/{request_id}/quotes/{quote_id}/award")
async def award_quote(
    request_id: uuid.UUID,
    quote_id: uuid.UUID,
    session: DB,
    principal: Procurement,
    data: Annotated[AwardIn | None, Body()] = None,
) -> dict:
    request = await session.scalar(
        select(PurchaseRequest)
        .where(PurchaseRequest.id == request_id, PurchaseRequest.org_id == principal.org_id)
        .with_for_update()
    )
    if request is None:
        raise NotFoundError("Purchase request not found")
    if request.status != PRStatus.APPROVED.value:
        raise BusinessRuleViolation("Quotes can only be awarded after request approval")
    if await session.scalar(select(PurchaseOrder.id).where(PurchaseOrder.purchase_request_id == request.id)):
        raise ConflictError("A purchase order already exists for this request")
    quote = await session.scalar(
        select(VendorQuote)
        .where(
            VendorQuote.id == quote_id,
            VendorQuote.org_id == principal.org_id,
            VendorQuote.purchase_request_id == request.id,
        )
        .with_for_update()
    )
    if quote is None:
        raise NotFoundError("Quote not found for this request")
    if quote.is_awarded:
        raise ConflictError("This quote has already been awarded")
    already_awarded = await session.scalar(
        select(VendorQuote.id).where(
            VendorQuote.org_id == principal.org_id,
            VendorQuote.purchase_request_id == request.id,
            VendorQuote.is_awarded.is_(True),
        )
    )
    if already_awarded:
        raise ConflictError("A quote has already been awarded for this request")
    if quote.quote_valid_until and quote.quote_valid_until < date.today():
        raise BusinessRuleViolation("This quote has expired")
    if quote.amount > request.estimated_total:
        raise BusinessRuleViolation("Quote exceeds the approved request value; resubmit for approval")
    vendor = await session.scalar(
        select(Vendor).where(
            Vendor.id == quote.vendor_id,
            Vendor.org_id == principal.org_id,
            Vendor.deleted_at.is_(None),
            Vendor.status == "approved",
        )
    )
    if vendor is None:
        raise BusinessRuleViolation("The quote vendor is no longer approved")

    # Human-in-the-loop record: what the baseline recommended at this moment vs. the decision.
    _, ranking = await _ranked_quotes(session, principal.org_id, request)
    recommended = next(r for r in ranking if r.rank == 1)
    awarded = next(r for r in ranking if r.id == quote.id)
    followed = recommended.id == quote.id
    override_reason = (data.override_reason or "").strip() if data else ""
    if not followed and len(override_reason) < 10:
        raise BusinessRuleViolation(
            "Awarding a quote other than the top-ranked eligible quote requires an override reason "
            "(at least 10 characters)",
            details={"recommended_quote_id": str(recommended.id), "recommended_score": recommended.score},
        )
    award_context = {
        "method": SOURCING_METHOD,
        "recommended_quote_id": str(recommended.id),
        "recommended_score": recommended.score,
        "awarded_score": awarded.score,
        "awarded_rank": awarded.rank,
        "eligible_quotes": sum(1 for r in ranking if r.eligible),
        "followed_recommendation": followed,
        "override_reason": override_reason or None,
    }
    quote.is_awarded = True
    quote.awarded_by_id = principal.user_id
    quote.awarded_at = utcnow()
    quote.award_context = award_context
    request.preferred_vendor_id = vendor.id
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="vendor_quote.awarded",
        entity_type="purchase_request",
        entity_id=request.id,
        summary=(
            f"Awarded {request.number} to {vendor.name}"
            + ("" if followed else " (override of baseline recommendation)")
        ),
        changes={
            "quote_id": str(quote.id),
            "vendor_id": str(vendor.id),
            "amount": quote.amount,
            "award_context": award_context,
        },
    )
    await session.commit()
    return {
        "request_id": request.id,
        "quote_id": quote.id,
        "vendor_id": vendor.id,
        "vendor_name": vendor.name,
        "amount": quote.amount,
        "currency": quote.currency,
        "awarded_at": quote.awarded_at,
        "award_context": award_context,
    }


async def _order(
    session, org_id: uuid.UUID, order_id: uuid.UUID, *, for_update: bool = False
) -> PurchaseOrder:
    query = select(PurchaseOrder).where(PurchaseOrder.id == order_id, PurchaseOrder.org_id == org_id)
    if for_update:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise NotFoundError("Purchase order not found")
    return row


@router.get("/purchase-orders")
async def list_orders(
    session: DB, principal: Annotated[Principal, Depends(requires(Permission.PR_READ_ALL))]
) -> list[dict]:
    rows = await session.scalars(
        select(PurchaseOrder)
        .where(PurchaseOrder.org_id == principal.org_id)
        .order_by(PurchaseOrder.created_at.desc())
        .limit(200)
    )
    return [
        {
            "id": r.id,
            "number": r.number,
            "request_id": r.purchase_request_id,
            "vendor_id": r.vendor_id,
            "status": r.status,
            "currency": r.currency,
            "total": r.total,
            "awarded_quote_id": r.awarded_quote_id,
            "terms": r.terms,
            "items": [
                {
                    "id": item.id,
                    "line_no": item.line_no,
                    "description": item.description,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "line_total": item.line_total,
                }
                for item in r.items
            ],
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.post("/purchase-requests/{request_id}/purchase-order", status_code=status.HTTP_201_CREATED)
async def create_order(request_id: uuid.UUID, session: DB, principal: Procurement) -> dict:
    request = await session.scalar(
        select(PurchaseRequest)
        .where(PurchaseRequest.id == request_id, PurchaseRequest.org_id == principal.org_id)
        .with_for_update()
    )
    if request is None:
        raise NotFoundError("Purchase request not found")
    if request.status != PRStatus.APPROVED.value:
        raise BusinessRuleViolation("Only an approved purchase request can become a purchase order")
    if request.preferred_vendor_id is None:
        raise BusinessRuleViolation("Select an approved vendor before creating the purchase order")
    vendor = await session.scalar(
        select(Vendor).where(
            Vendor.id == request.preferred_vendor_id,
            Vendor.org_id == principal.org_id,
            Vendor.deleted_at.is_(None),
            Vendor.status == "approved",
        )
    )
    if vendor is None:
        raise BusinessRuleViolation("The selected vendor is not approved")
    if await session.scalar(select(PurchaseOrder.id).where(PurchaseOrder.purchase_request_id == request.id)):
        raise ConflictError("A purchase order already exists for this request")
    awarded_quote = await session.scalar(
        select(VendorQuote).where(
            VendorQuote.org_id == principal.org_id,
            VendorQuote.purchase_request_id == request.id,
            VendorQuote.is_awarded.is_(True),
        )
    )
    if awarded_quote and awarded_quote.vendor_id != vendor.id:
        raise BusinessRuleViolation("The selected vendor does not match the awarded quote")
    number = await next_document_number(session, principal.org_id, "PO", date.today().year)
    po_items: list[PurchaseOrderItem] = []
    quoted_prices = {
        int(line["line_no"]): Decimal(line["unit_price"])
        for line in (awarded_quote.line_prices if awarded_quote else [])
    }
    if awarded_quote and quoted_prices:
        # The supplier priced each line: the PO carries those exact unit prices.
        pricing_basis = "quote_line_prices"
        for item in request.items:
            unit_price = quoted_prices[item.line_no]
            po_items.append(
                PurchaseOrderItem(
                    org_id=principal.org_id,
                    line_no=item.line_no,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=unit_price,
                    line_total=_money(item.quantity * unit_price),
                )
            )
    elif awarded_quote:
        # Only a quote total exists: apportion it in proportion to the request line values.
        pricing_basis = "quote_total_apportioned"
        base_total = sum((item.line_total for item in request.items), Decimal(0))
        remaining = awarded_quote.amount
        for index, item in enumerate(request.items):
            if index == len(request.items) - 1:
                allocated = remaining
            elif base_total > 0:
                allocated = _money(awarded_quote.amount * item.line_total / base_total)
                remaining -= allocated
            else:
                allocated = Decimal("0.00")
            effective_unit_price = (allocated / item.quantity).quantize(Decimal("0.01"))
            po_items.append(
                PurchaseOrderItem(
                    org_id=principal.org_id,
                    line_no=item.line_no,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=effective_unit_price,
                    line_total=allocated,
                )
            )
    else:
        pricing_basis = "request_estimate"
        po_items = [
            PurchaseOrderItem(
                org_id=principal.org_id,
                line_no=item.line_no,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                line_total=item.line_total,
            )
            for item in request.items
        ]
    order = PurchaseOrder(
        org_id=principal.org_id,
        number=number,
        purchase_request_id=request.id,
        vendor_id=vendor.id,
        currency=request.currency,
        total=sum((item.line_total for item in po_items), Decimal(0)),
        awarded_quote_id=awarded_quote.id if awarded_quote else None,
        status="approved",
        terms=f"Pricing basis: {pricing_basis}",
        items=po_items,
    )
    session.add(order)
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="purchase_order.created",
        entity_type="purchase_order",
        entity_id=order.id,
        summary=f"Created {number} from {request.number}",
        changes={
            "awarded_quote_id": str(awarded_quote.id) if awarded_quote else None,
            "pricing_basis": pricing_basis,
            "total": order.total,
        },
    )
    await session.commit()
    return {
        "id": order.id,
        "number": order.number,
        "status": order.status,
        "total": order.total,
        "currency": order.currency,
        "vendor_id": order.vendor_id,
        "pricing_basis": pricing_basis,
    }


@router.post("/purchase-orders/{order_id}/receipts", status_code=status.HTTP_201_CREATED)
async def receive_goods(order_id: uuid.UUID, data: ReceiptIn, session: DB, principal: Procurement) -> dict:
    order = await _order(session, principal.org_id, order_id, for_update=True)
    if order.status not in {"acknowledged", "partially_received"}:
        raise BusinessRuleViolation("Goods can only be received against an acknowledged open purchase order")
    if data.damaged_quantity > data.quantity:
        raise BusinessRuleViolation("Damaged quantity cannot exceed quantity received")
    item_id = data.purchase_order_item_id
    if item_id is None and len(order.items) == 1:
        item_id = order.items[0].id
    if item_id is None:
        raise BusinessRuleViolation("Select the purchase order line being received")
    po_item = next((item for item in order.items if item.id == item_id), None)
    if po_item is None:
        raise NotFoundError("Purchase order line not found")
    receipt_filter = [
        GoodsReceipt.org_id == principal.org_id,
        GoodsReceipt.purchase_order_id == order.id,
        or_(GoodsReceipt.purchase_order_item_id == po_item.id, GoodsReceipt.purchase_order_item_id.is_(None))
        if len(order.items) == 1
        else GoodsReceipt.purchase_order_item_id == po_item.id,
    ]
    received = await session.scalar(
        select(func.coalesce(func.sum(GoodsReceipt.quantity - GoodsReceipt.damaged_quantity), 0)).where(
            *receipt_filter
        )
    )
    accepted = Decimal(received or 0)
    if accepted + data.quantity - data.damaged_quantity > po_item.quantity:
        raise BusinessRuleViolation("Receipt exceeds the remaining quantity on this order line")
    receipt = GoodsReceipt(
        org_id=principal.org_id,
        purchase_order_id=order.id,
        purchase_order_item_id=po_item.id,
        received_by_id=principal.user_id,
        received_on=data.received_on,
        quantity=data.quantity,
        damaged_quantity=data.damaged_quantity,
        comments=data.comments,
        evidence_reference=data.evidence_reference,
    )
    session.add(receipt)
    await session.flush()
    accepted_by_line = await _accepted_by_line(session, principal.org_id, order)
    fully_received = all(accepted_by_line.get(item.id, Decimal(0)) == item.quantity for item in order.items)
    status_value = "received" if fully_received else "partially_received"
    order.status = status_value
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="goods_receipt.created",
        entity_type="purchase_order",
        entity_id=order.id,
        summary=f"Recorded receipt against {order.number}",
        changes={
            "purchase_order_item_id": str(po_item.id),
            "quantity": data.quantity,
            "damaged_quantity": data.damaged_quantity,
        },
    )
    await session.commit()
    return {
        "id": receipt.id,
        "purchase_order_id": order.id,
        "accepted_total": accepted + data.quantity - data.damaged_quantity,
        "ordered_total": po_item.quantity,
        "purchase_order_item_id": po_item.id,
        "purchase_order_status": status_value,
    }


async def _accepted_by_line(session, org_id: uuid.UUID, order: PurchaseOrder) -> dict[uuid.UUID, Decimal]:
    """Accepted (received minus damaged) quantity per PO line.

    Receipts recorded before line-level receipts have no line; on a single-line order they are
    attributed to that line.
    """
    rows = await session.execute(
        select(
            GoodsReceipt.purchase_order_item_id,
            func.sum(GoodsReceipt.quantity - GoodsReceipt.damaged_quantity),
        )
        .where(GoodsReceipt.org_id == org_id, GoodsReceipt.purchase_order_id == order.id)
        .group_by(GoodsReceipt.purchase_order_item_id)
    )
    accepted: dict[uuid.UUID, Decimal] = {}
    for line_id, amount in rows:
        if line_id is None:
            if len(order.items) != 1:
                continue
            line_id = order.items[0].id
        accepted[line_id] = accepted.get(line_id, Decimal(0)) + Decimal(amount or 0)
    return accepted


async def _billed_by_line(session, org_id: uuid.UUID, order: PurchaseOrder) -> dict[uuid.UUID, Decimal]:
    prior = await session.execute(
        select(Invoice.match_status, Invoice.line_match_details, Invoice.quantity).where(
            Invoice.org_id == org_id, Invoice.purchase_order_id == order.id
        )
    )
    return billed_quantities(
        [(status_, details or [], quantity) for status_, details, quantity in prior], _order_lines(order)
    )


def _order_lines(order: PurchaseOrder) -> list[OrderLine]:
    return [
        OrderLine(id=item.id, line_no=item.line_no, quantity=item.quantity, unit_price=item.unit_price)
        for item in order.items
    ]


@router.post("/purchase-orders/{order_id}/invoices", status_code=status.HTTP_201_CREATED)
async def submit_invoice(order_id: uuid.UUID, data: InvoiceIn, session: DB, principal: Procurement) -> dict:
    # The order row lock serialises invoices on one order, so two concurrent invoices cannot
    # both bill the same accepted quantity.
    order = await _order(session, principal.org_id, order_id, for_update=True)
    if order.status not in {"partially_received", "received"}:
        raise BusinessRuleViolation("Invoices require at least one recorded goods receipt")
    duplicate = await session.scalar(
        select(Invoice.id).where(
            Invoice.org_id == principal.org_id,
            Invoice.vendor_id == order.vendor_id,
            func.lower(Invoice.invoice_number) == data.invoice_number.strip().lower(),
        )
    )
    if duplicate:
        raise ConflictError("Duplicate invoice: this vendor and invoice number already exist")
    accepted_by_line = await _accepted_by_line(session, principal.org_id, order)
    billed_by_line = await _billed_by_line(session, principal.org_id, order)

    if data.items is None:
        if len(order.items) != 1:
            raise BusinessRuleViolation("Multi-line orders require invoice line items for matching")
        assert data.quantity is not None and data.subtotal is not None
        invoice_lines = [
            InvoiceLine(
                purchase_order_item_id=order.items[0].id,
                quantity=data.quantity,
                unit_price=(data.subtotal / data.quantity).quantize(Decimal("0.01")),
            )
        ]
    else:
        invoice_lines = [
            InvoiceLine(
                purchase_order_item_id=line.purchase_order_item_id,
                quantity=line.quantity,
                unit_price=line.unit_price,
                tax_rate=line.tax_rate,
            )
            for line in data.items
        ]
        line_subtotal = _money(sum((line.quantity * line.unit_price for line in data.items), Decimal(0)))
        if data.subtotal is not None and _money(data.subtotal) != line_subtotal:
            raise BusinessRuleViolation("Invoice subtotal must equal the sum of its line amounts")
        if data.quantity is not None and data.quantity != sum(
            (line.quantity for line in data.items), Decimal(0)
        ):
            raise BusinessRuleViolation("Invoice quantity must equal the sum of its line quantities")

    vendor_registration = await session.scalar(
        select(Vendor.invoice_registration_number).where(
            Vendor.id == order.vendor_id, Vendor.org_id == principal.org_id
        )
    )
    try:
        result = match_invoice(
            order_lines=_order_lines(order),
            accepted_by_line=accepted_by_line,
            billed_by_line=billed_by_line,
            invoice_lines=invoice_lines,
            declared_tax=data.tax,
            currency=order.currency,
            stated_registration=data.registration_number,
            vendor_registration=vendor_registration,
        )
    except InvalidInvoice as exc:
        raise BusinessRuleViolation(str(exc)) from exc
    subtotal = _money(data.subtotal) if data.items is None and data.subtotal is not None else result.subtotal
    total = _money(subtotal + data.tax)
    details = {
        "po_total": str(order.total),
        "invoice_total": str(total),
        "received_quantity": str(sum(accepted_by_line.values(), Decimal(0))),
        "previously_invoiced_quantity": str(sum(billed_by_line.values(), Decimal(0))),
        "invoice_quantity": str(result.quantity),
        "line_level_match": True,
        "cumulative_quantity_check": True,
        "tax_compared": result.tax["compared"],
        "tax": result.tax,
        "qualified_invoice": result.qualified_invoice,
        "exceptions": result.exceptions,
        "reason": "; ".join(result.exceptions)
        or "Prices, quantities not yet invoiced and any stated tax agree with the order and accepted receipts",
    }
    invoice = Invoice(
        org_id=principal.org_id,
        purchase_order_id=order.id,
        vendor_id=order.vendor_id,
        invoice_number=data.invoice_number.strip(),
        invoice_date=data.invoice_date,
        registration_number=data.registration_number,
        quantity=result.quantity,
        subtotal=subtotal,
        tax=data.tax,
        total=total,
        currency=order.currency,
        match_status=result.status,
        match_details=details,
        line_match_details=result.line_details,
        submitted_by_id=principal.user_id,
    )
    session.add(invoice)
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="invoice.submitted",
        entity_type="invoice",
        entity_id=invoice.id,
        summary=f"Invoice {invoice.invoice_number} submitted ({result.status})",
        changes={
            "match_status": result.status,
            "total": total,
            "exceptions": result.exceptions,
            "line_match_details": result.line_details,
        },
    )
    await session.commit()
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "total": total,
        "currency": invoice.currency,
        "match_status": result.status,
        "match_details": details,
        "line_match_details": result.line_details,
    }


@router.post("/purchase-orders/{order_id}/transition")
async def transition_order(
    order_id: uuid.UUID, data: POTransitionIn, session: DB, principal: Procurement
) -> dict:
    order = await _order(session, principal.org_id, order_id, for_update=True)
    current, target = order.status, data.status
    valid = {"approved": {"sent"}, "sent": {"acknowledged"}, "received": {"closed"}}
    if target == "cancelled":
        if current in {"closed", "cancelled"} or not data.reason or len(data.reason.strip()) < 3:
            raise BusinessRuleViolation(
                "An open purchase order needs a cancellation reason of at least 3 characters"
            )
        receipt_count = await session.scalar(
            select(func.count(GoodsReceipt.id)).where(GoodsReceipt.purchase_order_id == order.id)
        )
        if receipt_count:
            raise BusinessRuleViolation("A purchase order with recorded receipts cannot be cancelled")
    elif target == "closed":
        if target not in valid.get(current, set()):
            raise BusinessRuleViolation(f"Purchase order cannot move from {current} to {target}")
        states = [
            match
            for match in await session.scalars(
                select(Invoice.match_status).where(
                    Invoice.org_id == principal.org_id, Invoice.purchase_order_id == order.id
                )
            )
            if match not in VOID_INVOICE_STATES
        ]
        if not states or any(match not in PAYABLE_INVOICE_STATES for match in states):
            raise BusinessRuleViolation(
                "Close an order only when its invoices are matched or their exceptions approved"
            )
        accepted = await _accepted_by_line(session, principal.org_id, order)
        billed = await _billed_by_line(session, principal.org_id, order)
        unbilled = [
            item.line_no
            for item in order.items
            if billed.get(item.id, Decimal(0)) < accepted.get(item.id, Decimal(0))
        ]
        if unbilled:
            raise BusinessRuleViolation(
                "Every accepted quantity must be invoiced before the order is closed",
                details={"unbilled_lines": unbilled},
            )
    elif target not in valid.get(current, set()):
        raise BusinessRuleViolation(f"Purchase order cannot move from {current} to {target}")
    order.status = target
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="purchase_order.status_changed",
        entity_type="purchase_order",
        entity_id=order.id,
        summary=f"{order.number}: {current} -> {target}",
        changes={"from": current, "to": target, "reason": data.reason},
    )
    await session.commit()
    return {"id": order.id, "number": order.number, "status": order.status}


@router.get("/invoices")
async def list_invoices(
    session: DB, principal: Annotated[Principal, Depends(requires(Permission.PR_READ_ALL))]
) -> list[dict]:
    rows = await session.scalars(
        select(Invoice)
        .where(Invoice.org_id == principal.org_id)
        .order_by(Invoice.created_at.desc())
        .limit(200)
    )
    return [
        {
            "id": r.id,
            "invoice_number": r.invoice_number,
            "purchase_order_id": r.purchase_order_id,
            "total": r.total,
            "currency": r.currency,
            "match_status": r.match_status,
            "match_details": r.match_details,
            "line_match_details": r.line_match_details,
            "reviewed_by_id": r.reviewed_by_id,
            "reviewed_at": r.reviewed_at,
            "review_comment": r.review_comment,
            "invoice_date": r.invoice_date,
            "registration_number": r.registration_number,
        }
        for r in rows
    ]


@router.post("/invoices/{invoice_id}/exception-decision")
async def decide_invoice_exception(
    invoice_id: uuid.UUID,
    data: InvoiceExceptionDecisionIn,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.PAYMENT_APPROVE))],
) -> dict:
    invoice = await session.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.org_id == principal.org_id).with_for_update()
    )
    if invoice is None:
        raise NotFoundError("Invoice not found")
    if invoice.match_status == "duplicate":
        raise BusinessRuleViolation("Duplicate invoices cannot be approved through exception review")
    if invoice.match_status not in {"partially_matched", "mismatch", "requires_review"}:
        raise BusinessRuleViolation("Only invoices awaiting exception review can be decided")
    if invoice.submitted_by_id == principal.user_id:
        raise BusinessRuleViolation("The invoice submitter cannot decide its exception")
    if data.decision not in {"approve", "reject"}:
        raise BusinessRuleViolation("Decision must be approve or reject")
    previous_status = invoice.match_status
    invoice.match_status = "exception_approved" if data.decision == "approve" else "exception_rejected"
    invoice.reviewed_by_id = principal.user_id
    invoice.reviewed_at = utcnow()
    invoice.review_comment = data.comment
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action=f"invoice.exception.{invoice.match_status.removeprefix('exception_')}",
        entity_type="invoice",
        entity_id=invoice.id,
        summary=f"Invoice {invoice.invoice_number} exception {invoice.match_status.removeprefix('exception_')}",
        changes={"from": previous_status, "to": invoice.match_status, "comment": data.comment},
    )
    await session.commit()
    return {
        "id": invoice.id,
        "match_status": invoice.match_status,
        "reviewed_by_id": invoice.reviewed_by_id,
        "reviewed_at": invoice.reviewed_at,
        "review_comment": invoice.review_comment,
    }


@router.post("/invoices/{invoice_id}/payment-requests", status_code=status.HTTP_201_CREATED)
async def request_payment(invoice_id: uuid.UUID, session: DB, principal: CurrentUser) -> dict:
    principal.require(Permission.PR_READ_ALL)
    invoice = await session.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.org_id == principal.org_id).with_for_update()
    )
    if invoice is None:
        raise NotFoundError("Invoice not found")
    if invoice.match_status not in PAYABLE_INVOICE_STATES:
        raise BusinessRuleViolation(
            "Only a matched or finance-approved exception invoice can be submitted for payment approval"
        )
    if await session.scalar(
        select(Payment.id).where(Payment.org_id == principal.org_id, Payment.invoice_id == invoice.id)
    ):
        raise ConflictError("A payment request already exists for this invoice")
    payment = Payment(
        org_id=principal.org_id,
        invoice_id=invoice.id,
        amount=invoice.total,
        currency=invoice.currency,
        status="pending_approval",
        submitted_by_id=principal.user_id,
    )
    session.add(payment)
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="payment.requested",
        entity_type="invoice",
        entity_id=invoice.id,
        summary=f"Payment approval requested for invoice {invoice.invoice_number}",
        changes={"amount": invoice.total, "currency": invoice.currency},
    )
    await session.commit()
    return {
        "id": payment.id,
        "invoice_id": payment.invoice_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": payment.status,
    }


@router.get("/payments")
async def list_payments(
    session: DB, principal: Annotated[Principal, Depends(requires(Permission.PR_READ_ALL))]
) -> list[dict]:
    rows = await session.scalars(
        select(Payment)
        .where(Payment.org_id == principal.org_id)
        .order_by(Payment.created_at.desc())
        .limit(200)
    )
    return [
        {
            "id": p.id,
            "invoice_id": p.invoice_id,
            "amount": p.amount,
            "currency": p.currency,
            "status": p.status,
            "submitted_by_id": p.submitted_by_id,
            "decided_by_id": p.decided_by_id,
            "comment": p.comment,
            "created_at": p.created_at,
            "exported_at": p.exported_at,
            "export_reference": p.export_reference,
        }
        for p in rows
    ]


@router.post("/payments/{payment_id}/decision")
async def decide_payment(
    payment_id: uuid.UUID,
    data: PaymentDecisionIn,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.PAYMENT_APPROVE))],
) -> dict:
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id, Payment.org_id == principal.org_id).with_for_update()
    )
    if payment is None:
        raise NotFoundError("Payment request not found")
    if payment.status != "pending_approval":
        raise BusinessRuleViolation("This payment request has already been decided")
    if payment.submitted_by_id == principal.user_id:
        raise BusinessRuleViolation("The payment requester cannot approve their own payment")
    if data.decision not in {"approve", "reject"}:
        raise BusinessRuleViolation("Decision must be approve or reject")
    payment.status = "approved" if data.decision == "approve" else "rejected"
    payment.decided_by_id = principal.user_id
    payment.decided_at = utcnow()
    payment.comment = data.comment
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action=f"payment.{payment.status}",
        entity_type="payment",
        entity_id=payment.id,
        summary=f"Payment {payment.status} for {payment.amount} {payment.currency}",
        changes={"status": payment.status, "comment": payment.comment},
    )
    await session.commit()
    return {
        "id": payment.id,
        "status": payment.status,
        "decided_by_id": payment.decided_by_id,
        "decided_at": payment.decided_at,
        "comment": payment.comment,
    }


@router.get("/spend-analytics")
async def spend_analytics(
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.ANALYTICS_READ))],
    from_date: date | None = None,
    to_date: date | None = None,
    department_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    category: SpendCategory | None = None,
) -> dict:
    if from_date and to_date and from_date > to_date:
        raise BusinessRuleViolation("from_date must be on or before to_date")
    approved = PurchaseRequest.status == PRStatus.APPROVED.value
    request_filters = [PurchaseRequest.org_id == principal.org_id]
    if from_date:
        request_filters.append(
            PurchaseRequest.decided_at >= datetime.combine(from_date, time.min, tzinfo=UTC)
        )
    if to_date:
        request_filters.append(
            PurchaseRequest.decided_at < datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=UTC)
        )
    if department_id:
        request_filters.append(PurchaseRequest.department_id == department_id)
    if vendor_id:
        request_filters.append(PurchaseRequest.preferred_vendor_id == vendor_id)
    if category:
        request_filters.append(PurchaseRequest.category == category.value)
    spend_filters = [*request_filters, approved]
    rows = await session.execute(
        select(
            PurchaseRequest.category,
            func.count(PurchaseRequest.id),
            func.coalesce(func.sum(PurchaseRequest.estimated_total), 0),
        )
        .where(*spend_filters)
        .group_by(PurchaseRequest.category)
        .order_by(PurchaseRequest.category)
    )
    categories = [
        {"category": category, "approved_requests": int(count), "approved_value": str(total)}
        for category, count, total in rows
    ]
    departments = await session.execute(
        select(
            Department.id,
            func.coalesce(Department.name, "Unassigned"),
            func.count(PurchaseRequest.id),
            func.coalesce(func.sum(PurchaseRequest.estimated_total), 0),
        )
        .select_from(PurchaseRequest)
        .outerjoin(
            Department,
            and_(Department.id == PurchaseRequest.department_id, Department.org_id == principal.org_id),
        )
        .where(*spend_filters)
        .group_by(Department.id, Department.name)
        .order_by(func.sum(PurchaseRequest.estimated_total).desc())
    )
    vendors = await session.execute(
        select(
            Vendor.id,
            func.coalesce(Vendor.name, "Unspecified vendor"),
            func.count(PurchaseRequest.id),
            func.coalesce(func.sum(PurchaseRequest.estimated_total), 0),
        )
        .select_from(PurchaseRequest)
        .outerjoin(
            Vendor,
            and_(Vendor.id == PurchaseRequest.preferred_vendor_id, Vendor.org_id == principal.org_id),
        )
        .where(*spend_filters)
        .group_by(Vendor.id, Vendor.name)
        .order_by(func.sum(PurchaseRequest.estimated_total).desc())
    )
    invoices = await session.execute(
        select(Invoice.match_status, func.count(Invoice.id), func.coalesce(func.sum(Invoice.total), 0))
        .join(PurchaseOrder, PurchaseOrder.id == Invoice.purchase_order_id)
        .join(PurchaseRequest, PurchaseRequest.id == PurchaseOrder.purchase_request_id)
        .where(Invoice.org_id == principal.org_id, PurchaseOrder.org_id == principal.org_id, *request_filters)
        .group_by(Invoice.match_status)
        .order_by(Invoice.match_status)
    )
    orders = await session.execute(
        select(
            PurchaseOrder.status,
            func.count(PurchaseOrder.id),
            func.coalesce(func.sum(PurchaseOrder.total), 0),
        )
        .join(PurchaseRequest, PurchaseRequest.id == PurchaseOrder.purchase_request_id)
        .where(PurchaseOrder.org_id == principal.org_id, *request_filters)
        .group_by(PurchaseOrder.status)
        .order_by(PurchaseOrder.status)
    )
    payments = await session.execute(
        select(Payment.status, func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0))
        .join(Invoice, Invoice.id == Payment.invoice_id)
        .join(PurchaseOrder, PurchaseOrder.id == Invoice.purchase_order_id)
        .join(PurchaseRequest, PurchaseRequest.id == PurchaseOrder.purchase_request_id)
        .where(
            Payment.org_id == principal.org_id,
            Invoice.org_id == principal.org_id,
            PurchaseOrder.org_id == principal.org_id,
            *request_filters,
        )
        .group_by(Payment.status)
        .order_by(Payment.status)
    )
    elapsed_hours = func.extract("epoch", PurchaseRequest.decided_at - PurchaseRequest.submitted_at) / 3600
    cycle = await session.execute(
        select(
            func.count(PurchaseRequest.id),
            func.avg(elapsed_hours),
            func.max(elapsed_hours),
            func.percentile_cont(0.5).within_group(elapsed_hours),
            func.percentile_cont(0.9).within_group(elapsed_hours),
        ).where(
            *request_filters,
            PurchaseRequest.submitted_at.is_not(None),
            PurchaseRequest.decided_at.is_not(None),
            PurchaseRequest.status.in_([PRStatus.APPROVED.value, PRStatus.REJECTED.value]),
        )
    )
    cycle_count, mean_hours, max_hours, median_hours, p90_hours = cycle.one()
    month_bucket = func.date_trunc("month", PurchaseRequest.decided_at)
    monthly = await session.execute(
        select(
            month_bucket,
            func.count(PurchaseRequest.id),
            func.coalesce(func.sum(PurchaseRequest.estimated_total), 0),
        )
        .where(
            *spend_filters,
            *([] if from_date else [PurchaseRequest.decided_at >= utcnow() - timedelta(days=365)]),
        )
        .group_by(month_bucket)
        .order_by(month_bucket)
    )
    return {
        "currency": "tenant base currency / per-record currency",
        "approved_spend_by_category": categories,
        "approved_spend_by_department": [
            {
                "department_id": department_id,
                "department_name": name,
                "approved_requests": int(count),
                "approved_value": str(total),
            }
            for department_id, name, count, total in departments
        ],
        "approved_spend_by_vendor": [
            {
                "vendor_id": vendor_id,
                "vendor_name": name,
                "approved_requests": int(count),
                "approved_value": str(total),
            }
            for vendor_id, name, count, total in vendors
        ],
        "invoice_match_summary": [{"status": s, "count": int(n), "total": str(v)} for s, n, v in invoices],
        "purchase_order_summary": [{"status": s, "count": int(n), "total": str(v)} for s, n, v in orders],
        "payment_summary": [{"status": s, "count": int(n), "total": str(v)} for s, n, v in payments],
        "request_decision_cycle_hours": {
            "completed_requests": int(cycle_count or 0),
            "mean": round(float(mean_hours), 2) if mean_hours is not None else None,
            "median": round(float(median_hours), 2) if median_hours is not None else None,
            "p90": round(float(p90_hours), 2) if p90_hours is not None else None,
            "max": round(float(max_hours), 2) if max_hours is not None else None,
        },
        "approved_spend_monthly_last_12_months": [
            {"month": month.isoformat(), "approved_requests": int(count), "approved_value": str(total)}
            for month, count, total in monthly
        ],
    }
