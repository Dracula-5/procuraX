"""Explainable robust log-amount outlier baseline for offline spend review."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class SpendObservation:
    transaction_id: str
    category: str
    amount: float


@dataclass(frozen=True)
class AnomalySignal:
    transaction_id: str
    category: str
    amount: float
    score: float
    severity: str
    explanation: str
    recommended_action: str


def detect_amount_outliers(
    observations: Iterable[SpendObservation], *, threshold: float = 3.5, min_group_size: int = 8
) -> list[AnomalySignal]:
    """Flag high-side log-amount outliers against same-category median and MAD."""
    groups: dict[str, list[SpendObservation]] = defaultdict(list)
    for row in observations:
        if row.amount > 0:
            groups[row.category].append(row)
    signals: list[AnomalySignal] = []
    for category, rows in groups.items():
        if len(rows) < min_group_size:
            continue
        logs = [math.log(row.amount) for row in rows]
        center = median(logs)
        mad = median([abs(value - center) for value in logs])
        if mad == 0:
            continue
        baseline = math.exp(center)
        for row, log_amount in zip(rows, logs, strict=True):
            score = 0.6745 * (log_amount - center) / mad
            if score >= threshold:
                multiple = row.amount / baseline
                signals.append(
                    AnomalySignal(
                        transaction_id=row.transaction_id,
                        category=category,
                        amount=row.amount,
                        score=round(score, 3),
                        severity="high" if score >= threshold * 1.75 else "medium",
                        explanation=(
                            f"Amount is {multiple:.1f}x the {category} historical median "
                            f"({baseline:.2f}); robust log-MAD score {score:.2f} exceeds {threshold:.2f}."
                        ),
                        recommended_action=(
                            "Review supporting documents and sourcing context; do not auto-block payment."
                        ),
                    )
                )
    return sorted(signals, key=lambda row: (-row.score, row.transaction_id))
