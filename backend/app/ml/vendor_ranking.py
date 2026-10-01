"""Dependency-free pairwise learning-to-rank experiment for sourcing candidates."""

from __future__ import annotations

import math
from dataclasses import dataclass

FEATURES = ("price", "delivery", "quality", "contract", "on_time", "acceptance", "low_risk")
BASELINE_WEIGHTS = (0.40, 0.20, 0.25, 0.15, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class VendorCandidate:
    features: tuple[float, ...]
    relevance: int
    utility: float


class PairwiseRanker:
    """Linear pairwise logistic ranker; evaluation labels must come from historical outcomes."""

    def __init__(self, *, epochs: int = 35, learning_rate: float = 0.08, l2: float = 0.001) -> None:
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.l2 = l2
        self.weights = [0.0] * len(FEATURES)

    def fit(self, groups: list[list[VendorCandidate]]) -> PairwiseRanker:
        pairs = [
            (left.features, right.features, 1.0 if left.utility > right.utility else -1.0)
            for group in groups
            for index, left in enumerate(group)
            for right in group[index + 1 :]
            if left.utility != right.utility
        ]
        if not pairs:
            raise ValueError("Learning-to-rank requires preference pairs with different outcomes")
        for _ in range(self.epochs):
            for left, right, preference in pairs:
                difference = [a - b for a, b in zip(left, right, strict=True)]
                margin = sum(weight * value for weight, value in zip(self.weights, difference, strict=True))
                gradient = preference / (1 + math.exp(max(-30.0, min(30.0, preference * margin))))
                self.weights = [
                    weight * (1 - self.learning_rate * self.l2) + self.learning_rate * gradient * delta
                    for weight, delta in zip(self.weights, difference, strict=True)
                ]
        return self

    def score(self, candidate: VendorCandidate) -> float:
        return sum(weight * value for weight, value in zip(self.weights, candidate.features, strict=True))


def baseline_score(candidate: VendorCandidate) -> float:
    return sum(weight * value for weight, value in zip(BASELINE_WEIGHTS, candidate.features, strict=True))


def ranking_metrics(
    groups: list[list[VendorCandidate]], scores: list[list[float]], *, k: int = 3
) -> dict[str, float]:
    ndcgs: list[float] = []
    precisions: list[float] = []
    recalls: list[float] = []
    for group, group_scores in zip(groups, scores, strict=True):
        ranked = sorted(range(len(group)), key=lambda index: group_scores[index], reverse=True)
        top = ranked[:k]
        dcg = sum(
            (2 ** group[index].relevance - 1) / math.log2(position + 2) for position, index in enumerate(top)
        )
        ideal = sorted((candidate.relevance for candidate in group), reverse=True)[:k]
        idcg = sum((2**relevance - 1) / math.log2(position + 2) for position, relevance in enumerate(ideal))
        relevant_count = sum(candidate.relevance > 0 for candidate in group)
        hit_count = sum(group[index].relevance > 0 for index in top)
        ndcgs.append(dcg / idcg if idcg else 0.0)
        precisions.append(hit_count / k)
        recalls.append(hit_count / relevant_count if relevant_count else 0.0)
    return {
        f"ndcg@{k}": round(sum(ndcgs) / len(ndcgs), 4),
        f"precision@{k}": round(sum(precisions) / len(precisions), 4),
        f"recall@{k}": round(sum(recalls) / len(recalls), 4),
    }
