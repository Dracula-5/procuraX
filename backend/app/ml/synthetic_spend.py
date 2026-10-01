"""Explicitly synthetic category-labeled text for pipeline smoke evaluation only."""

from __future__ import annotations

from random import Random

CATEGORY_TERMS = {
    "it_services": ("integration", "support", "cybersecurity", "cloud", "implementation", "consulting"),
    "travel": ("airfare", "hotel", "taxi", "lodging", "flight", "perdiem"),
    "facilities": ("cleaning", "maintenance", "utilities", "building", "security", "landscaping"),
    "marketing": ("campaign", "advertising", "media", "promotion", "branding", "agency"),
    "professional_services": ("advisory", "legal", "audit", "tax", "expertise", "consulting"),
    "hardware": ("laptop", "server", "monitor", "printer", "component", "device"),
    "software": ("license", "subscription", "saas", "application", "renewal", "software"),
    "logistics": ("freight", "shipping", "warehouse", "courier", "transport", "delivery"),
    "office_supplies": ("stationery", "paper", "pen", "toner", "supplies", "desk"),
    "manufacturing": ("rawmaterial", "production", "tooling", "assembly", "parts", "factory"),
}
CONTEXT_TERMS = ("annual", "purchase", "supplier", "contract", "business", "service", "order", "cost")


def generate_dataset(examples_per_class: int = 100, seed: int = 23) -> list[tuple[str, str]]:
    if examples_per_class < 2:
        raise ValueError("Need at least two examples per class")
    rng = Random(seed)  # noqa: S311 - deterministic synthetic dataset only, not cryptography
    rows: list[tuple[str, str]] = []
    for category, terms in CATEGORY_TERMS.items():
        for index in range(examples_per_class):
            cues = rng.sample(terms, k=2)
            context = rng.sample(CONTEXT_TERMS, k=3)
            rows.append(
                (f"{cues[0]} {cues[1]} {context[0]} {context[1]} {context[2]} reference {index}", category)
            )
    rng.shuffle(rows)
    return rows
