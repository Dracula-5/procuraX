"""Measure lexical retrieval plumbing on an intentionally simple synthetic corpus."""

from __future__ import annotations

import json
import statistics
import time

from app.modules.procurement.assistant import _rank_texts


def main() -> None:
    topics = [
        "travel booking",
        "software licensing",
        "hardware purchases",
        "vendor onboarding",
        "invoice approval",
        "budget exceptions",
        "contract review",
        "emergency purchases",
        "purchase order changes",
        "goods receipt",
        "duplicate invoices",
        "payment approval",
        "office supplies",
        "consulting services",
        "security review",
        "data retention",
        "international travel",
        "fleet maintenance",
        "marketing agency",
        "cloud hosting",
        "quote comparison",
        "department budgets",
        "tax documentation",
        "supplier sanctions",
        "purchase cancellation",
        "approval delegation",
        "capital equipment",
        "expense policy",
        "renewal review",
        "receiving evidence",
        "invoice mismatch",
        "sustainable sourcing",
    ]
    documents = [
        (
            f"doc-{index}",
            f"{topic} procurement policy",
            f"This source defines the {topic} approval requirements and evidence.",
        )
        for index, topic in enumerate(topics)
    ]
    reciprocal_ranks: list[float] = []
    recalls: list[float] = []
    latencies: list[float] = []
    for index, topic in enumerate(topics):
        began = time.perf_counter_ns()
        hits = _rank_texts(f"what are the rules for {topic}", documents, limit=5)
        latencies.append((time.perf_counter_ns() - began) / 1_000_000)
        target_id = f"doc-{index}"
        ranks = [rank for rank, hit in enumerate(hits, start=1) if hit.id == target_id]
        reciprocal_ranks.append(1 / ranks[0] if ranks else 0.0)
        recalls.append(1.0 if ranks else 0.0)
    result = {
        "dataset": "synthetic_procurement_knowledge_v1",
        "synthetic": True,
        "queries": len(topics),
        "documents": len(documents),
        "k": 5,
        "metrics": {
            "recall@5": round(statistics.fmean(recalls), 4),
            "mrr@5": round(statistics.fmean(reciprocal_ranks), 4),
            "citation_correct_at_1": round(sum(value == 1.0 for value in reciprocal_ranks) / len(topics), 4),
        },
        "latency_ms": {
            "p50": round(statistics.median(latencies), 4),
            "p95": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 4),
        },
        "limitations": [
            "Each query repeats the target document's exact topic words.",
            "This is a retrieval implementation smoke check, not a realistic corpus evaluation.",
            "No answer-generation, semantic retrieval, faithfulness or user relevance is tested.",
        ],
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
