"""Human-in-the-loop evidence: baseline recommendation vs. human award vs. supplier outcome.

Every award stores the recommendation that was on screen (see award_context on VendorQuote).
This endpoint joins those records to what happened afterwards (delivery timeliness, damaged
quantity, first-invoice match) so recommendation quality can be judged on recorded outcomes
rather than asserted. Counts are descriptive; no significance test is applied.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on

from app.api.deps import DB, requires
from app.core.principal import Principal
from app.domain.rbac import Permission
from app.domain.sourcing import METHOD as SOURCING_METHOD
from app.modules.procurement.models import PurchaseRequest
from app.modules.procurement.records import GoodsReceipt, Invoice, PurchaseOrder, VendorQuote
from app.modules.vendors.models import Vendor

router = APIRouter(prefix="/decision-support", tags=["decision-support"])


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _empty_bucket() -> dict:
    return {
        "awards": 0,
        "delivered": 0,
        "on_time": 0,
        "late": 0,
        "pending": 0,
        "invoiced": 0,
        "first_invoice_matched": 0,
        "received_quantity": Decimal(0),
        "damaged_quantity": Decimal(0),
    }


@router.get("/sourcing")
async def sourcing_decisions(
    session: DB, principal: Annotated[Principal, Depends(requires(Permission.ANALYTICS_READ))]
) -> dict:
    org_id = principal.org_id
    rows = (
        await session.execute(
            select(VendorQuote, PurchaseRequest.number, Vendor.name, PurchaseOrder)
            .join(PurchaseRequest, PurchaseRequest.id == VendorQuote.purchase_request_id)
            .join(Vendor, Vendor.id == VendorQuote.vendor_id)
            .outerjoin(PurchaseOrder, PurchaseOrder.awarded_quote_id == VendorQuote.id)
            .where(VendorQuote.org_id == org_id, VendorQuote.is_awarded.is_(True))
            .order_by(VendorQuote.awarded_at.desc())
        )
    ).all()
    order_ids = [order.id for _, _, _, order in rows if order is not None]
    receipts: dict[uuid.UUID, tuple[date | None, Decimal, Decimal]] = {}
    first_invoice: dict[uuid.UUID, str] = {}
    if order_ids:
        for order_id, last_receipt, received_qty, damaged_qty in await session.execute(
            select(
                GoodsReceipt.purchase_order_id,
                func.max(GoodsReceipt.received_on),
                func.sum(GoodsReceipt.quantity),
                func.sum(GoodsReceipt.damaged_quantity),
            )
            .where(GoodsReceipt.org_id == org_id, GoodsReceipt.purchase_order_id.in_(order_ids))
            .group_by(GoodsReceipt.purchase_order_id)
        ):
            receipts[order_id] = (last_receipt, Decimal(received_qty or 0), Decimal(damaged_qty or 0))
        for order_id, match_status in await session.execute(
            select(Invoice.purchase_order_id, Invoice.match_status)
            .where(Invoice.org_id == org_id, Invoice.purchase_order_id.in_(order_ids))
            .order_by(Invoice.purchase_order_id, Invoice.created_at)
            .ext(distinct_on(Invoice.purchase_order_id))
        ):
            first_invoice[order_id] = match_status

    recommended_ids = {
        uuid.UUID(quote.award_context["recommended_quote_id"])
        for quote, _, _, _ in rows
        if quote.award_context and quote.award_context.get("recommended_quote_id")
    }
    recommended_vendor: dict[uuid.UUID, str] = {}
    if recommended_ids:
        recommended_vendor = {
            quote_id: name
            for quote_id, name in await session.execute(
                select(VendorQuote.id, Vendor.name)
                .join(Vendor, Vendor.id == VendorQuote.vendor_id)
                .where(VendorQuote.org_id == org_id, VendorQuote.id.in_(recommended_ids))
            )
        }

    today = date.today()
    buckets = {"followed": _empty_bucket(), "overridden": _empty_bucket()}
    unrecorded = 0
    overrides: list[dict] = []
    for quote, request_number, vendor_name, order in rows:
        context = quote.award_context
        if not context:
            unrecorded += 1  # Awarded before recommendation context was recorded.
            continue
        bucket = buckets["followed" if context.get("followed_recommendation") else "overridden"]
        bucket["awards"] += 1
        outcome = "no_purchase_order"
        if order is not None:
            due = order.created_at.date() + timedelta(days=quote.delivery_days)
            last_received, quantity, damaged = receipts.get(order.id, (None, Decimal(0), Decimal(0)))
            bucket["received_quantity"] += quantity
            bucket["damaged_quantity"] += damaged
            if order.status in {"received", "closed"} and last_received is not None:
                bucket["delivered"] += 1
                outcome = "on_time" if last_received <= due else "late"
                bucket[outcome] += 1
            elif order.status != "cancelled" and today > due:
                outcome = "late"
                bucket["late"] += 1
            else:
                outcome = "pending"
                bucket["pending"] += 1
            if order.id in first_invoice:
                bucket["invoiced"] += 1
                bucket["first_invoice_matched"] += int(first_invoice[order.id] == "matched")
        if not context.get("followed_recommendation") and len(overrides) < 50:
            recommended_id = uuid.UUID(context["recommended_quote_id"])
            overrides.append(
                {
                    "request_number": request_number,
                    "awarded_vendor": vendor_name,
                    "recommended_vendor": recommended_vendor.get(recommended_id),
                    "awarded_score": context.get("awarded_score"),
                    "recommended_score": context.get("recommended_score"),
                    "override_reason": context.get("override_reason"),
                    "awarded_at": quote.awarded_at,
                    "delivery_outcome": outcome,
                }
            )

    recorded = buckets["followed"]["awards"] + buckets["overridden"]["awards"]
    return {
        "method": SOURCING_METHOD,
        "data_scope": (
            "All awards recorded in this tenant. Awards in the fictional demo tenant are demonstration "
            "usage, not pilot evidence."
        ),
        "awards_with_recommendation": recorded,
        "awards_without_recorded_recommendation": unrecorded,
        "followed": buckets["followed"]["awards"],
        "overridden": buckets["overridden"]["awards"],
        "agreement_rate": _rate(buckets["followed"]["awards"], recorded),
        "outcomes": {
            name: {
                "awards": b["awards"],
                "delivered": b["delivered"],
                "on_time": b["on_time"],
                "late": b["late"],
                "pending": b["pending"],
                "on_time_rate": _rate(b["on_time"], b["on_time"] + b["late"]),
                "invoiced": b["invoiced"],
                "first_pass_invoice_match_rate": _rate(b["first_invoice_matched"], b["invoiced"]),
                "damaged_quantity_rate": (
                    round(float(b["damaged_quantity"] / b["received_quantity"]), 4)
                    if b["received_quantity"]
                    else None
                ),
            }
            for name, b in buckets.items()
        },
        "overrides": overrides,
        "interpretation": (
            "Descriptive counts only. On-time means the last receipt of a fully received order was on or "
            "before order date + quoted delivery days; open orders past that date count as late. No "
            "significance test is applied, so small samples must not be generalised."
        ),
    }
