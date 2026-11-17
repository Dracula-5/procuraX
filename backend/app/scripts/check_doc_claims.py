"""Fail when the docs state role, permission, rule or ADR counts that the code disagrees with.

uv run python -m app.scripts.check_doc_claims    # CI: exit 1 and list every mismatch

Sources of truth: `app/domain/rbac.py` (roles, permissions), `app/domain/policy/rules.py` (rule
catalogue) and the `**ADR-NNN` headings in docs/system_design.md. Every phrase such as "21 rules"
or "23 permissions" in README.md or docs/*.md (generated docs excluded) must equal the current
count.

Historical statements stay true for their date. Put "at the time" on the same line and the line
is skipped, e.g. "a policy engine with 21 rules at the time".
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from app.domain.policy.rules import RULES
from app.domain.rbac import Permission, Role

REPO = Path(__file__).resolve().parents[3]
# Only these qualifiers may sit between the number and the noun ("21 policy rules"); anything
# looser would match unrelated phrases such as "3-way match rules".
CLAIM = re.compile(r"\b(\d+)\s+(?:(?:policy|deterministic|versioned)\s+)?(roles|permissions|rules|ADRs)\b")
ADR_HEADING = re.compile(r"^\*\*ADR-(\d{3}) ", re.MULTILINE)
HISTORICAL_MARKER = "at the time"


@dataclass(frozen=True)
class Mismatch:
    path: str
    line: int
    claimed: int
    noun: str
    actual: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: says {self.claimed} {self.noun}, the code has {self.actual}"


def actual_counts(system_design_text: str) -> dict[str, int]:
    return {
        "roles": len(Role),
        "permissions": len(Permission),
        "rules": len(RULES),
        "ADRs": len(set(ADR_HEADING.findall(system_design_text))),
    }


def find_mismatches(text: str, path: str, counts: dict[str, int]) -> list[Mismatch]:
    mismatches = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if HISTORICAL_MARKER in line.lower():
            continue
        for match in CLAIM.finditer(line):
            claimed, noun = int(match.group(1)), match.group(2)
            if claimed != counts[noun]:
                mismatches.append(Mismatch(path, line_no, claimed, noun, counts[noun]))
    return mismatches


def doc_files(repo: Path = REPO) -> list[Path]:
    return [repo / "README.md", *sorted((repo / "docs").glob("*.md"))]


def check(repo: Path = REPO) -> list[Mismatch]:
    counts = actual_counts((repo / "docs" / "system_design.md").read_text(encoding="utf-8"))
    mismatches: list[Mismatch] = []
    for path in doc_files(repo):
        rel = path.relative_to(repo).as_posix()
        mismatches += find_mismatches(path.read_text(encoding="utf-8"), rel, counts)
    return mismatches


def main() -> int:
    mismatches = check()
    if mismatches:
        print("Docs disagree with the code:", file=sys.stderr)
        for mismatch in mismatches:
            print(f"  {mismatch}", file=sys.stderr)
        print('Fix the number, or add "at the time" to a historical statement.', file=sys.stderr)
        return 1
    counts = actual_counts((REPO / "docs" / "system_design.md").read_text(encoding="utf-8"))
    print("Doc counts match the code: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
