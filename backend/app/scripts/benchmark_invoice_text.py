"""Evaluate labeled-line invoice extraction on deterministic synthetic text fixtures."""

from __future__ import annotations

import json
import time
from random import Random

from app.ml.invoice_text import extract_invoice_text


def main() -> None:
    rng = Random(31)  # noqa: S311 - deterministic synthetic fixture generation
    samples: list[tuple[str, dict[str, str]]] = []
    for index in range(100):
        quantity = rng.randint(1, 80)
        unit_price = rng.randrange(1000, 500_000, 100)
        subtotal = quantity * unit_price
        tax = subtotal // 10
        expected = {
            "vendor": f"Supplier {index:03d} K.K.",
            "invoice_number": f"INV-2026-{index:06d}",
            "invoice_date": f"2026-{index % 12 + 1:02d}-15",
            "purchase_order_number": f"PO-2026-{index:06d}",
            "currency": "JPY",
            "quantity": str(quantity),
            "subtotal": str(subtotal),
            "tax": str(tax),
            "total": str(subtotal + tax),
        }
        text = "\n".join(
            (
                f"Vendor: {expected['vendor']}",
                f"Invoice Number: {expected['invoice_number']}",
                f"Invoice Date: {expected['invoice_date']}",
                f"PO Number: {expected['purchase_order_number']}",
                "Currency: JPY",
                f"Quantity: {quantity}",
                f"Subtotal: ¥{subtotal:,}",
                f"Tax: ¥{tax:,}",
                f"Total: ¥{subtotal + tax:,}",
            )
        )
        samples.append((text, expected))

    start = time.perf_counter_ns()
    predictions = [extract_invoice_text(text) for text, _ in samples]
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
    fields = list(samples[0][1])
    correct = {
        field: sum(row[field] == target[field] for row, (_, target) in zip(predictions, samples, strict=True))
        for field in fields
    }
    field_accuracy = {field: round(correct[field] / len(samples), 4) for field in fields}
    result = {
        "dataset": "synthetic_labeled_invoice_text_v1",
        "synthetic": True,
        "seed": 31,
        "samples": len(samples),
        "fields": fields,
        "baseline": "exact labeled-line extraction with decimal normalization; no OCR or layout model",
        "field_exact_match_accuracy": field_accuracy,
        "average_field_accuracy": round(sum(correct.values()) / (len(samples) * len(fields)), 4),
        "end_to_end_invoice_exact_match": round(
            sum(
                all(row[field] == target[field] for field in fields)
                for row, (_, target) in zip(predictions, samples, strict=True)
            )
            / len(samples),
            4,
        ),
        "average_latency_ms_per_invoice": round(elapsed_ms / len(samples), 4),
        "limitations": [
            "Generated text uses the same explicit labels and line structure expected by the parser.",
            "No scanned image, PDF, OCR, handwriting, table layout, locale variation or adversarial document "
            "was tested.",
            "This is a parser smoke check only and is not invoice automation or field accuracy on real documents.",
        ],
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
