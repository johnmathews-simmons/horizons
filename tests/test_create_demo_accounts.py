"""Unit tests for the WU8.1 demo-account CLI password gating.

Only the pure password-resolution helper is covered here; the DB
integration is exercised manually via ``--help`` smoke and (in
follow-up work) an optional integration test.

The key contract: missing env vars without ``--allow-dev-defaults``
produce a non-empty ``missing`` list, which the CLI uses to abort
before any DB write — closing the "operator forgot the production
overrides" footgun from the WU8.1 security review.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    REPO_ROOT / "packages" / "horizons-api" / "scripts" / "create_demo_accounts.py"
)
_SCRIPT_MODULE_NAME = "horizons_demo_accounts_script"


def _load_script_module() -> ModuleType:
    """Import the script as a module without going through its package path.

    The script lives under ``scripts/`` and is not part of the
    importable package surface; load it via ``importlib`` and register
    it in ``sys.modules`` so the dataclass machinery (which looks the
    module up by name to resolve forward references) can find it.
    """
    cached = sys.modules.get(_SCRIPT_MODULE_NAME)
    if isinstance(cached, ModuleType):
        return cached
    spec = importlib.util.spec_from_file_location(_SCRIPT_MODULE_NAME, SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_SCRIPT_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def test_missing_env_vars_without_opt_in_lists_all_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env vars set + no opt-in → every account flagged as missing."""
    module = _load_script_module()
    monkeypatch.delenv("HORIZONS_DEMO_UK_PASSWORD", raising=False)
    monkeypatch.delenv("HORIZONS_DEMO_EU_PASSWORD", raising=False)
    monkeypatch.delenv("HORIZONS_DEMO_ADMIN_PASSWORD", raising=False)

    resolved, missing = module._resolve_passwords(  # type: ignore[attr-defined]
        module._accounts(),  # type: ignore[attr-defined]
        allow_dev_defaults=False,
    )
    assert missing == [
        "HORIZONS_DEMO_UK_PASSWORD",
        "HORIZONS_DEMO_EU_PASSWORD",
        "HORIZONS_DEMO_ADMIN_PASSWORD",
    ]
    # When `missing` is non-empty the caller aborts before any DB
    # write; `resolved` is partial and ignored.
    assert "demo-uk@example.test" not in resolved


def test_partial_env_vars_without_opt_in_flags_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator set two of three → the third is flagged."""
    module = _load_script_module()
    monkeypatch.setenv("HORIZONS_DEMO_UK_PASSWORD", "uk-real-pw")
    monkeypatch.setenv("HORIZONS_DEMO_EU_PASSWORD", "eu-real-pw")
    monkeypatch.delenv("HORIZONS_DEMO_ADMIN_PASSWORD", raising=False)

    _, missing = module._resolve_passwords(  # type: ignore[attr-defined]
        module._accounts(),  # type: ignore[attr-defined]
        allow_dev_defaults=False,
    )
    assert missing == ["HORIZONS_DEMO_ADMIN_PASSWORD"]


def test_empty_string_env_var_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``EMPTY=""`` shouldn't be accepted as a real password."""
    module = _load_script_module()
    monkeypatch.setenv("HORIZONS_DEMO_UK_PASSWORD", "")
    monkeypatch.setenv("HORIZONS_DEMO_EU_PASSWORD", "eu-real-pw")
    monkeypatch.setenv("HORIZONS_DEMO_ADMIN_PASSWORD", "admin-real-pw")

    _, missing = module._resolve_passwords(  # type: ignore[attr-defined]
        module._accounts(),  # type: ignore[attr-defined]
        allow_dev_defaults=False,
    )
    assert missing == ["HORIZONS_DEMO_UK_PASSWORD"]


def test_all_env_vars_set_resolves_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path: every env var present, no opt-in needed."""
    module = _load_script_module()
    monkeypatch.setenv("HORIZONS_DEMO_UK_PASSWORD", "uk-real-pw")
    monkeypatch.setenv("HORIZONS_DEMO_EU_PASSWORD", "eu-real-pw")
    monkeypatch.setenv("HORIZONS_DEMO_ADMIN_PASSWORD", "admin-real-pw")

    resolved, missing = module._resolve_passwords(  # type: ignore[attr-defined]
        module._accounts(),  # type: ignore[attr-defined]
        allow_dev_defaults=False,
    )
    assert missing == []
    assert resolved == {
        "demo-uk@example.test": "uk-real-pw",
        "demo-eu@example.test": "eu-real-pw",
        "admin-demo@example.test": "admin-real-pw",
    }


def test_opt_in_falls_back_to_defaults_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--allow-dev-defaults`` semantics: unset env → bake-in fallback."""
    module = _load_script_module()
    monkeypatch.delenv("HORIZONS_DEMO_UK_PASSWORD", raising=False)
    monkeypatch.delenv("HORIZONS_DEMO_EU_PASSWORD", raising=False)
    monkeypatch.delenv("HORIZONS_DEMO_ADMIN_PASSWORD", raising=False)

    resolved, missing = module._resolve_passwords(  # type: ignore[attr-defined]
        module._accounts(),  # type: ignore[attr-defined]
        allow_dev_defaults=True,
    )
    assert missing == []
    assert resolved == {
        "demo-uk@example.test": "demo-uk-pass-not-secret",
        "demo-eu@example.test": "demo-eu-pass-not-secret",
        "admin-demo@example.test": "admin-demo-pass-not-secret",
    }


def test_opt_in_still_prefers_env_var_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--allow-dev-defaults`` never *overrides* a present env var."""
    module = _load_script_module()
    monkeypatch.setenv("HORIZONS_DEMO_ADMIN_PASSWORD", "admin-real-pw")
    monkeypatch.delenv("HORIZONS_DEMO_UK_PASSWORD", raising=False)
    monkeypatch.delenv("HORIZONS_DEMO_EU_PASSWORD", raising=False)

    resolved, _ = module._resolve_passwords(  # type: ignore[attr-defined]
        module._accounts(),  # type: ignore[attr-defined]
        allow_dev_defaults=True,
    )
    # Admin env-var wins; UK/EU fall back to the bake-in defaults.
    assert resolved["admin-demo@example.test"] == "admin-real-pw"
    assert resolved["demo-uk@example.test"] == "demo-uk-pass-not-secret"
    assert resolved["demo-eu@example.test"] == "demo-eu-pass-not-secret"
