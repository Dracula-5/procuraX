"""Compare an existing transparent sourcing heuristic with pairwise LTR on synthetic labels."""

from __future__ import annotations

import json
import statistics
import time
from random import Random

from app.ml.vendor_ranking import FEATURES, PairwiseRanker, VendorCandidate, baseline_score, ranking_metrics


def _groups(seed: int, count: int) -> list[list[VendorCandidate]]:
    rng = Random(seed)  # noqa: S311 - deterministic synthetic experiment fixture
    hidden_weights = (0.18, 0.12, 0.13, 0.10, 0.21, 0.15, 0.11)
    groups: list[list[VendorCandidate]] = []
    for _ in range(count):
        features = [tuple(rng.random() for _ in FEATURES) for _ in range(6)]
        utility = [
            sum(weight * value for weight, value in zip(hidden_weights, row, strict=True))
            + rng.uniform(-0.025, 0.025)
            for row in features
        ]
        order = sorted(range(len(features)), key=lambda index: utility[index], reverse=True)
        relevance = {index: 3 - rank for rank, index in enumerate(order[:2])}
        groups.append(
            [
                VendorCandidate(row, relevance.get(index, 0), utility[index])
                for index, row in enumerate(features)
            ]
        )
    return groups


def _evaluate(groups: list[list[VendorCandidate]], scorer) -> tuple[dict[str, float], dict[str, float]]:  # noqa: ANN001
    rankings: list[list[float]] = []
    latencies: list[float] = []
    for group in groups:
        start = time.perf_counter_ns()
        rankings.append([scorer(candidate) for candidate in group])
        latencies.append((time.perf_counter_ns() - start) / 1_000_000)
    return (
        ranking_metrics(groups, rankings, k=3),
        {
            "p50_ms": round(statistics.median(latencies), 4),
            "p95_ms": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 4),
        },
    )


def main() -> None:
    train = _groups(seed=41, count=80)
    evaluation = _groups(seed=42, count=40)
    ranker = PairwiseRanker().fit(train)
    baseline_metrics, baseline_latency = _evaluate(evaluation, baseline_score)
    ltr_metrics, ltr_latency = _evaluate(evaluation, ranker.score)
    print(
        json.dumps(
            {
                "dataset": "synthetic_vendor_preferences_v1",
                "synthetic": True,
                "seed": 41,
                "evaluation_seed": 42,
                "train_queries": len(train),
                "evaluation_queries": len(evaluation),
                "vendors_per_query": 6,
                "features": list(FEATURES),
                "relevance": "generated from a fixed hidden utility formula; top two candidates are positive",
                "baseline": {
                    "method": "existing price/delivery/quality/contract weights",
                    "metrics": baseline_metrics,
                    "latency": baseline_latency,
                },
                "pairwise_ltr": {
                    "method": "linear pairwise logistic SGD",
                    "learned_weights": [round(value, 4) for value in ranker.weights],
                    "metrics": ltr_metrics,
                    "latency": ltr_latency,
                },
                "limitations": [
                    "Synthetic preferences are generated from a known formula, not procurement outcomes.",
                    "Metrics demonstrate evaluator mechanics only and do not support vendor recommendation claims.",
                    "Latency excludes database queries, feature construction, and network overhead.",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
