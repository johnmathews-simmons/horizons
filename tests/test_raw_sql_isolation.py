"""Architectural: ``sqlalchemy.text`` is permitted only in session.py.

The single sanctioned home for imperative raw SQL execution is
``horizons_core.db.session``. ``db/models/*.py`` is allow-listed
because it uses ``text("uuidv7()")`` / ``text("now()")`` as
declarative ``server_default=`` arguments — a SQL expression literal
for schema generation, not raw-SQL execution.

This test parses every ``.py`` file under each ``packages/horizons-*/src``
tree and fails on any ``text(...)`` call outside the allow list. AST
analysis is conservative: it matches both bare ``text(...)`` and
``module.text(...)`` calls. False positives (e.g. an unrelated method
called ``text``) can be added to ``ALLOWED_FILES`` if they ever arise.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_TREES: tuple[Path, ...] = (
    REPO_ROOT / "packages/horizons-core/src",
    REPO_ROOT / "packages/horizons-ingestion/src",
    REPO_ROOT / "packages/horizons-api/src",
)
ALLOWED_FILES: frozenset[Path] = frozenset(
    {REPO_ROOT / "packages/horizons-core/src/horizons_core/db/session.py"}
)
ALLOWED_DIRS: tuple[Path, ...] = (
    REPO_ROOT / "packages/horizons-core/src/horizons_core/db/models",
)


def _is_text_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "text"
    if isinstance(func, ast.Attribute):
        return func.attr == "text"
    return False


def _iter_candidate_files() -> list[Path]:
    candidates: list[Path] = []
    for tree in SRC_TREES:
        for path in tree.rglob("*.py"):
            if path in ALLOWED_FILES:
                continue
            if any(allowed in path.parents for allowed in ALLOWED_DIRS):
                continue
            candidates.append(path)
    return sorted(candidates)


def test_text_calls_only_in_session_module() -> None:
    violations: list[str] = []
    for path in _iter_candidate_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_text_call(node):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not violations, (
        "sqlalchemy.text() is only permitted inside "
        "horizons_core.db.session (and declaratively in db/models/*.py). "
        "Found:\n  " + "\n  ".join(violations)
    )
