"""Run a reproducible synthetic-only TF-IDF/logistic regression smoke benchmark."""

from __future__ import annotations

import json
import statistics
import time
from random import Random

from app.ml.spend_classifier import TfidfLogisticClassifier
from app.ml.synthetic_spend import CATEGORY_TERMS, generate_dataset


def _metrics(actual: list[str], predicted: list[str]) -> dict[str, object]:
    classes = sorted(CATEGORY_TERMS)
    class_rows: dict[str, dict[str, float | int]] = {}
    f1s: list[float] = []
    for label in classes:
        tp = sum(a == label and p == label for a, p in zip(actual, predicted, strict=True))
        fp = sum(a != label and p == label for a, p in zip(actual, predicted, strict=True))
        fn = sum(a == label and p != label for a, p in zip(actual, predicted, strict=True))
        support = sum(a == label for a in actual)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        class_rows[label] = {
            "support": support,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
        f1s.append(f1)
    return {
        "accuracy": round(
            sum(a == p for a, p in zip(actual, predicted, strict=True)) / max(len(actual), 1), 4
        ),
        "macro_f1": round(statistics.fmean(f1s), 4),
        "per_class": class_rows,
    }


def main() -> None:
    seed = 23
    rows = generate_dataset(examples_per_class=100, seed=seed)
    train: list[tuple[str, str]] = []
    test: list[tuple[str, str]] = []
    for category in sorted(CATEGORY_TERMS):
        category_rows = [row for row in rows if row[1] == category]
        Random(seed).shuffle(category_rows)  # noqa: S311 - reproducible synthetic split
        train.extend(category_rows[:80])
        test.extend(category_rows[80:])
    model = TfidfLogisticClassifier().fit(
        [text for text, _ in train], [label for _, label in train], seed=seed
    )
    predictions: list[str] = []
    latencies: list[float] = []
    for text, _ in test:
        start = time.perf_counter_ns()
        predictions.append(model.predict(text))
        latencies.append((time.perf_counter_ns() - start) / 1_000_000)
    result = {
        "dataset": "synthetic_procurement_text_v1",
        "synthetic": True,
        "seed": seed,
        "examples_per_class": 100,
        "train_size": len(train),
        "test_size": len(test),
        "split": "stratified 80/20, generated from category-specific vocabulary templates",
        "model": "word TF-IDF + multinomial logistic regression (sparse SGD, 40 epochs)",
        "metrics": _metrics([label for _, label in test], predictions),
        "inference_latency_ms": {
            "p50": round(statistics.median(latencies), 4),
            "p95": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 4),
        },
        "limitations": [
            "Synthetic template data only; not representative of enterprise descriptions.",
            "Random split shares the same vocabulary generator; scores measure pipeline operation, "
            "not expected production accuracy.",
            "No transformer candidate or public dataset comparison was run.",
        ],
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
