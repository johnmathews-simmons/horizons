"""Architectural: ``packages/horizons-api/`` reads the corpus only through repos.

The third defence-in-depth layer (after grants + RLS) is the
repository surface in ``horizons_core.repos``. Routes that talk to
``Document``, ``DocumentVersion``, ``Clause``, or ``ChangeEvent``
directly bypass that layer: they construct ad-hoc queries that the
RLS-aware ``current_scope()`` predicate still narrows, but they
escape the typed-DTO discipline that prevents shape leakage and
the keyset-cursor / scope-aware methods the API contract depends
on.

This test parses every ``.py`` file under ``packages/horizons-api/src``
and fails on any import that names one of the corpus ORM models.
The API package may import freely from ``horizons_core.repos.*`` and
``horizons_core.core.*`` — those are the sanctioned surfaces.

Mirrors the AST shape of ``tests/test_raw_sql_isolation.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_TREE: Path = REPO_ROOT / "packages/horizons-api/src"

BANNED_NAMES: frozenset[str] = frozenset({"Document", "DocumentVersion", "Clause", "ChangeEvent"})

# Any import sourced from a module under ``horizons_core.db.models`` is
# considered direct corpus access. ``horizons_core.repos`` is fine, as is
# ``horizons_core.core.*``.
BANNED_MODULE_PREFIXES: tuple[str, ...] = ("horizons_core.db.models",)


def _iter_files() -> list[Path]:
    return sorted(API_TREE.rglob("*.py"))


def _flag_import_from(node: ast.ImportFrom) -> list[str]:
    """Return the imported names that violate the rule, if any."""
    module = node.module or ""
    if not module.startswith(BANNED_MODULE_PREFIXES):
        # Even from non-banned modules, a re-export of a banned name
        # would count — but we don't currently re-export ORM classes
        # from any non-db.models module, so the prefix check is the
        # whole story. If that changes, add the re-export module here.
        return []
    return [alias.name for alias in node.names if alias.name in BANNED_NAMES]


def _flag_import(node: ast.Import) -> list[str]:
    """``import horizons_core.db.models.documents`` would also violate."""
    return [
        alias.name
        for alias in node.names
        if any(alias.name.startswith(prefix) for prefix in BANNED_MODULE_PREFIXES)
    ]


def test_api_package_does_not_import_corpus_orm_models() -> None:
    violations: list[str] = []
    for path in _iter_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for name in _flag_import_from(node):
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: "
                        f"imports {name!r} from {node.module!r}"
                    )
            elif isinstance(node, ast.Import):
                for name in _flag_import(node):
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: imports {name!r}"
                    )
    assert not violations, (
        "packages/horizons-api/ must read the corpus only through "
        "horizons_core.repos.*, not via direct ORM imports. Found:\n  " + "\n  ".join(violations)
    )
