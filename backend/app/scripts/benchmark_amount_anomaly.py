"""Run a synthetic-only robust amount outlier baseline smoke benchmark."""

from __future__ import annotations

import json
from math import exp
from random import Random

from app.ml.spend_anomaly import SpendObservation, detect_amount_outliers


def main() -> None:
    rng = Random(29)  # noqa: S311 - deterministic synthetic benchmark fixture
    rows: list[SpendObservation] = []
    labels: dict[str, bool] = {}
    for category_index, category in enumerate(("software", "travel", "hardware", "facilities", "logistics")):
        center = 8.0 + category_index * 0.5
        for index in range(100):
            identifier = f"{category}-{index}"
            rows.append(SpendObservation(identifier, category, exp(rng.normalvariate(center, 0.35))))
            labels[identifier] = False
        for index in range(5):
            identifier = f"{category}-injected-{index}"
            amount = exp(rng.normalvariate(center, 0.35)) * 12
            rows.append(SpendObservation(identifier, category, amount))
            labels[identifier] = True
    flags = {signal.transaction_id: signal for signal in detect_amount_outliers(rows)}
    tp = sum(labels[row] for row in flags)
    fp = sum(not labels[row] for row in flags)
    fn = sum(label and row not in flags for row, label in labels.items())
    tn = sum(not label and row not in flags for row, label in labels.items())
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    result = {
        "dataset": "synthetic_spend_amounts_v1",
        "synthetic": True,
        "seed": 29,
        "observations": len(rows),
        "positive_labels": sum(labels.values()),
        "injection": "5 transactions per category multiplied by 12x after log-normal sampling",
        "baseline": "same-category median + robust log-MAD score >= 3.5",
        "metrics": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0,
            "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else 0,
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
        },
        "limitations": [
            "Synthetic log-normal amount distributions only; not representative procurement history.",
            "Only high-side amount anomalies are evaluated; no temporal, vendor, department or split-purchase signals.",
            "No learned candidate or production alert workflow was evaluated.",
        ],
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
