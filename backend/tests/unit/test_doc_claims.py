"""The docs' role, permission, rule and ADR counts are checked against the code."""

from pathlib import Path

from app.domain.policy.rules import RULES
from app.domain.rbac import Permission, Role
from app.scripts.check_doc_claims import actual_counts, check, find_mismatches

COUNTS = {"roles": 10, "permissions": 23, "rules": 21, "ADRs": 11}


def test_repository_docs_match_the_code() -> None:
    assert check() == []


def test_counts_come_from_the_sources_of_truth() -> None:
    design = "**ADR-001 One.** text\n**ADR-002 Two.** text\n**ADR-002 Two.** repeated\n"
    counts = actual_counts(design)
    assert counts == {"roles": len(Role), "permissions": len(Permission), "rules": len(RULES), "ADRs": 2}


def test_wrong_counts_are_reported_with_location() -> None:
    text = "intro\nan engine with 22 rules and 10 roles\nplus 24 permissions and 9 ADRs\n"
    found = [(m.line, m.claimed, m.noun, m.actual) for m in find_mismatches(text, "README.md", COUNTS)]
    assert found == [(2, 22, "rules", 21), (3, 24, "permissions", 23), (3, 9, "ADRs", 11)]


def test_qualified_claims_are_checked() -> None:
    found = find_mismatches("a deterministic engine: 20 policy rules", "x.md", COUNTS)
    assert [(m.claimed, m.noun) for m in found] == [(20, "rules")]


def test_historical_lines_and_unrelated_phrases_are_ignored() -> None:
    text = (
        "P1 shipped 19 rules at the time.\n"
        "3-way match rules and 4 weeks of rule review\n"
        "the OCR clean-up rules\n"
    )
    assert find_mismatches(text, "docs/roadmap.md", COUNTS) == []


def test_generated_docs_are_not_scanned(tmp_path: Path) -> None:
    (tmp_path / "docs" / "generated").mkdir(parents=True)
    (tmp_path / "docs" / "system_design.md").write_text("**ADR-001 Only.**\n", encoding="utf-8")
    (tmp_path / "docs" / "generated" / "rbac_matrix.md").write_text("99 roles\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(f"{len(Role)} roles and 1 ADRs\n", encoding="utf-8")
    assert check(tmp_path) == []
    (tmp_path / "docs" / "product_spec.md").write_text("7 roles\n", encoding="utf-8")
    assert [(m.path, m.claimed) for m in check(tmp_path)] == [("docs/product_spec.md", 7)]
