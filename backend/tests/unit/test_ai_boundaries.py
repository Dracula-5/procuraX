"""AI recommends · rules enforce · humans decide: structural checks that keep models out of decisions."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
AI_MODULES = ("app.ml", "app.ai", "anthropic", "app.integrations.oidc")
# Modules that decide or move money: they may not import a model, so no AI output can steer them.
DECIDERS = [
    APP / "domain" / "policy",
    APP / "domain" / "workflow",
    APP / "domain" / "matching.py",
    APP / "domain" / "zengin.py",
    APP / "modules" / "procurement" / "payment_export.py",
    APP / "modules" / "finance" / "router.py",
]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _files(target: Path) -> list[Path]:
    return sorted(target.rglob("*.py")) if target.is_dir() else [target]


def test_policy_engine_imports_no_ai_or_ml_module() -> None:
    offenders = {
        str(path.relative_to(APP)): sorted(n for n in _imports(path) if n.startswith(AI_MODULES))
        for path in _files(APP / "domain" / "policy")
    }
    assert {k: v for k, v in offenders.items() if v} == {}


def test_deciding_and_money_moving_code_imports_no_ai_module() -> None:
    offenders = {
        str(path.relative_to(APP)): sorted(n for n in _imports(path) if n.startswith(AI_MODULES))
        for target in DECIDERS
        for path in _files(target)
    }
    assert {k: v for k, v in offenders.items() if v} == {}


def test_the_whole_domain_layer_is_free_of_ai_imports() -> None:
    offenders = [
        str(path.relative_to(APP))
        for path in _files(APP / "domain")
        if any(n.startswith(AI_MODULES) for n in _imports(path))
    ]
    assert offenders == []


def test_ai_modules_never_write_decision_columns() -> None:
    """The AI package reads requests; it never assigns status, approvals, awards or payment state."""
    forbidden = ("status", "decided_at", "decided_by_id", "awarded_quote_id", "match_status", "exported_at")
    offenders = []
    for path in _files(APP / "ai"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign | ast.AugAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr in forbidden:
                        offenders.append(f"{path.name}:{node.lineno} .{target.attr}")
    assert offenders == []
