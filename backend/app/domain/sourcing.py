"""Transparent weighted quote baseline used for sourcing recommendations.

Pure functions, no I/O. The same ranking is shown on the comparison screen and recorded at
award time, so the human decision can later be compared with what the system recommended.
Only eligible quotes (unexpired, vendor still approved, within the approved request value) are
scored; ineligible quotes are listed with the reason instead of a score.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

METHOD = "weighted_rule_baseline_v1"
WEIGHTS = {
    "price": Decimal("0.40"),
    "delivery": Decimal("0.20"),
    "quality": Decimal("0.25"),
    "contract": Decimal("0.15"),
}


@dataclass(frozen=True)
class QuoteCandidate:
    id: uuid.UUID
    vendor_id: uuid.UUID
    amount: Decimal
    delivery_days: int
    quality_score: Decimal
    contract_compliant: bool
    valid_until: date | None
    vendor_approved: bool


@dataclass(frozen=True)
class RankedQuote:
    id: uuid.UUID
    eligible: bool
    score: float | None
    rank: int | None
    reasons: list[str]


def ineligibility(candidate: QuoteCandidate, *, approved_total: Decimal, today: date) -> str | None:
    if candidate.valid_until is not None and candidate.valid_until < today:
        return "Quote has expired"
    if not candidate.vendor_approved:
        return "Vendor is no longer approved"
    if candidate.amount > approved_total:
        return "Quote exceeds the approved request value"
    return None


def rank_quotes(
    candidates: list[QuoteCandidate], *, approved_total: Decimal, today: date
) -> list[RankedQuote]:
    """Score eligible quotes on price, delivery, quality and contract; rank 1 is the recommendation."""
    blocked = {c.id: ineligibility(c, approved_total=approved_total, today=today) for c in candidates}
    eligible = [c for c in candidates if blocked[c.id] is None]
    scored: list[tuple[float, Decimal, QuoteCandidate, list[str]]] = []
    if eligible:
        lowest = min(c.amount for c in eligible)
        fastest = min(c.delivery_days for c in eligible)
        for c in eligible:
            price = Decimal(1) if c.amount == 0 else lowest / c.amount
            delivery = Decimal(1) if c.delivery_days == 0 else Decimal(fastest) / Decimal(c.delivery_days)
            quality = c.quality_score / Decimal(5)
            contract = Decimal(1) if c.contract_compliant else Decimal(0)
            score = 100 * (
                WEIGHTS["price"] * price
                + WEIGHTS["delivery"] * delivery
                + WEIGHTS["quality"] * quality
                + WEIGHTS["contract"] * contract
            )
            reasons = []
            if c.amount == lowest:
                reasons.append("Lowest eligible price")
            if c.delivery_days == fastest:
                reasons.append("Fastest eligible delivery")
            if c.contract_compliant:
                reasons.append("Contract compliant")
            reasons.append(f"Quality score {c.quality_score}/5")
            scored.append((round(float(score), 2), c.amount, c, reasons))
    # Deterministic order: score desc, then cheaper, then id for a stable tie-break.
    scored.sort(key=lambda row: (-row[0], row[1], str(row[2].id)))
    ranked = [
        RankedQuote(id=c.id, eligible=True, score=score, rank=index + 1, reasons=reasons)
        for index, (score, _, c, reasons) in enumerate(scored)
    ]
    ranked += [
        RankedQuote(id=c.id, eligible=False, score=None, rank=None, reasons=[blocked[c.id] or ""])
        for c in candidates
        if blocked[c.id] is not None
    ]
    return ranked
