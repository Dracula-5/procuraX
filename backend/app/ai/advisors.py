"""Advisory flags on submitted purchase requests: ML signals a reviewer may want to look at.

They never change the outcome. The policy engine has already decided (and its decision is stored) before
these run; the flags are recorded in `ai_predictions` and shown next to the request as "for review".
A failing advisor is logged and skipped: it can never block or alter a submission.

* amount_outlier: robust log-amount z-score of this request against the tenant's other requests in the
  same category over the last year (`app.ml.spend_anomaly`, the offline baseline in the benchmark report).
* possible_duplicate: the same requester raised a request with a very similar title (word-set Jaccard
  ≥ 0.6) and vendor in the last 30 days that is still open or approved.
"""

import logging
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gateway
from app.ai.text import tokenize
from app.core.db import utcnow
from app.ml.spend_anomaly import SpendObservation, detect_amount_outliers
from app.modules.procurement.models import PurchaseRequest

logger = logging.getLogger("procurax.ai")
MODEL = "procurax-advisors"
MODEL_VERSION = "1 (robust-z 3.5; jaccard 0.6)"
OPEN_STATUSES = ("pending_approval", "approved", "policy_blocked")


def _jaccard(a: str, b: str) -> float:
    left, right = set(tokenize(a)), set(tokenize(b))
    return len(left & right) / len(left | right) if left and right else 0.0


async def _flags(session: AsyncSession, pr: PurchaseRequest) -> list[dict]:
    flags: list[dict] = []
    since = utcnow() - timedelta(days=365)
    history = (
        await session.execute(
            select(PurchaseRequest.id, PurchaseRequest.base_amount).where(
                PurchaseRequest.org_id == pr.org_id,
                PurchaseRequest.category == pr.category,
                PurchaseRequest.id != pr.id,
                PurchaseRequest.base_amount.is_not(None),
                PurchaseRequest.submitted_at >= since,
            )
        )
    ).all()
    if pr.base_amount is not None and len(history) >= 7:  # the detector needs 8 per category
        observations = [SpendObservation(str(i), pr.category, float(a or 0)) for i, a in history]
        observations.append(SpendObservation(str(pr.id), pr.category, float(pr.base_amount)))
        for signal in detect_amount_outliers(observations):
            if signal.transaction_id == str(pr.id):
                flags.append(
                    {
                        "code": "amount_outlier",
                        "severity": signal.severity,
                        "score": round(signal.score, 2),
                        "explanation": signal.explanation,
                        "compared_with": len(history),
                    }
                )
    recent = await session.execute(
        select(PurchaseRequest.id, PurchaseRequest.number, PurchaseRequest.title).where(
            PurchaseRequest.org_id == pr.org_id,
            PurchaseRequest.requester_id == pr.requester_id,
            PurchaseRequest.id != pr.id,
            PurchaseRequest.status.in_(OPEN_STATUSES),
            PurchaseRequest.preferred_vendor_id.is_not_distinct_from(pr.preferred_vendor_id),
            PurchaseRequest.created_at >= utcnow() - timedelta(days=30),
        )
    )
    for other_id, number, title in recent:
        similarity = _jaccard(pr.title, title)
        if similarity >= 0.6:
            flags.append(
                {
                    "code": "possible_duplicate",
                    "severity": "info",
                    "score": round(similarity, 2),
                    "related_request": number,
                    "related_request_id": str(other_id),
                }
            )
    return flags


async def advise_on_submission(session: AsyncSession, pr: PurchaseRequest, actor_user_id: object) -> None:
    """Record advisory flags for a just-submitted request. Never raises; never touches the request."""
    start = time.perf_counter()
    try:
        async with session.begin_nested():
            flags = await _flags(session, pr)
            await gateway.record(
                session,
                org_id=pr.org_id,
                kind="advisory_flags",
                model=MODEL,
                model_version=MODEL_VERSION,
                input_ref=f"purchase_request:{pr.id}",
                input_data={"amount": str(pr.base_amount), "category": pr.category, "title": pr.title},
                output={"flags": flags, "submission": pr.submission_count},
                latency_ms=(time.perf_counter() - start) * 1000,
                actor_user_id=actor_user_id,  # type: ignore[arg-type]
            )
    except Exception:  # an advisor must never block a submission
        logger.exception("advisory_flags_failed", extra={"purchase_request_id": str(pr.id)})
