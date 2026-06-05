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

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "packages" / "horizons-api" / "scripts" / "create_demo_accounts.py"
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

    resolved, missing, from_dev_default = module._resolve_passwords(  # type: ignore[attr-defined]
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
    assert from_dev_default == set()


def test_partial_env_vars_without_opt_in_flags_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator set two of three → the third is flagged."""
    module = _load_script_module()
    monkeypatch.setenv("HORIZONS_DEMO_UK_PASSWORD", "uk-real-pw")
    monkeypatch.setenv("HORIZONS_DEMO_EU_PASSWORD", "eu-real-pw")
    monkeypatch.delenv("HORIZONS_DEMO_ADMIN_PASSWORD", raising=False)

    _, missing, _ = module._resolve_passwords(  # type: ignore[attr-defined]
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

    _, missing, _ = module._resolve_passwords(  # type: ignore[attr-defined]
        module._accounts(),  # type: ignore[attr-defined]
        allow_dev_defaults=False,
    )
    assert missing == ["HORIZONS_DEMO_UK_PASSWORD"]


@pytest.mark.parametrize("whitespace", [" ", "  ", "\t", "\n", " \t\n "])
def test_whitespace_only_env_var_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
    whitespace: str,
) -> None:
    """Whitespace-only values are not real passwords; they must be flagged.

    The original guard (``is not None and != ""``) accepted ``" "``
    silently, leaving an unusable password hash in the DB and producing
    "wrong credentials" failures at login that look like an unrelated
    bug. Reject whitespace-only values up front so the CLI's "missing"
    error message points the operator at the offending env var.
    """
    module = _load_script_module()
    monkeypatch.setenv("HORIZONS_DEMO_UK_PASSWORD", whitespace)
    monkeypatch.setenv("HORIZONS_DEMO_EU_PASSWORD", "eu-real-pw")
    monkeypatch.setenv("HORIZONS_DEMO_ADMIN_PASSWORD", "admin-real-pw")

    resolved, missing, _ = module._resolve_passwords(  # type: ignore[attr-defined]
        module._accounts(),  # type: ignore[attr-defined]
        allow_dev_defaults=False,
    )
    assert missing == ["HORIZONS_DEMO_UK_PASSWORD"]
    assert "demo-uk@example.test" not in resolved


def test_env_var_with_surrounding_whitespace_is_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real passwords with edge whitespace pass, stripped of the edges.

    Common operator typo: copy/paste introduces a trailing space. We
    preserve internal whitespace (a password can legitimately contain
    a space) and only trim the edges so a "hunter2 " typo becomes
    "hunter2" rather than rejecting silently.
    """
    module = _load_script_module()
    monkeypatch.setenv("HORIZONS_DEMO_UK_PASSWORD", "  hunter2  ")
    monkeypatch.setenv("HORIZONS_DEMO_EU_PASSWORD", "\teu real pw\n")
    monkeypatch.setenv("HORIZONS_DEMO_ADMIN_PASSWORD", "admin-real-pw")

    resolved, missing, _ = module._resolve_passwords(  # type: ignore[attr-defined]
        module._accounts(),  # type: ignore[attr-defined]
        allow_dev_defaults=False,
    )
    assert missing == []
    assert resolved["demo-uk@example.test"] == "hunter2"
    # Internal whitespace is preserved.
    assert resolved["demo-eu@example.test"] == "eu real pw"
    assert resolved["admin-demo@example.test"] == "admin-real-pw"


def test_all_env_vars_set_resolves_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path: every env var present, no opt-in needed."""
    module = _load_script_module()
    monkeypatch.setenv("HORIZONS_DEMO_UK_PASSWORD", "uk-real-pw")
    monkeypatch.setenv("HORIZONS_DEMO_EU_PASSWORD", "eu-real-pw")
    monkeypatch.setenv("HORIZONS_DEMO_ADMIN_PASSWORD", "admin-real-pw")

    resolved, missing, from_dev_default = module._resolve_passwords(  # type: ignore[attr-defined]
        module._accounts(),  # type: ignore[attr-defined]
        allow_dev_defaults=False,
    )
    assert missing == []
    assert from_dev_default == set()
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

    resolved, missing, from_dev_default = module._resolve_passwords(  # type: ignore[attr-defined]
        module._accounts(),  # type: ignore[attr-defined]
        allow_dev_defaults=True,
    )
    assert missing == []
    assert from_dev_default == {
        "demo-uk@example.test",
        "demo-eu@example.test",
        "admin-demo@example.test",
    }
    assert resolved == {
        "demo-uk@example.test": "demo-uk-pass-not-secret",
        "demo-eu@example.test": "demo-eu-pass-not-secret",
        "admin-demo@example.test": "admin-demo-pass-not-secret",
    }


def test_downgrade_guard_blocks_dev_default_over_real_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row holding a real credential cannot be rotated to a dev default.

    Models the attack: operator ran with real env vars on day 1, then
    re-ran with --allow-dev-defaults (env vars unset). The guard must
    flag every account whose stored hash does not match the dev
    default and block the run before any UPDATE.
    """
    module = _load_script_module()
    accounts = module._accounts()  # type: ignore[attr-defined]
    hash_password = module.hash_password  # type: ignore[attr-defined]

    # Resolution: all three accounts fall back to dev defaults.
    resolved = {a.email: a.password_default for a in accounts}
    from_dev_default = {a.email for a in accounts}

    # All three rows currently hold REAL (env-var-sourced) credentials.
    real_hashes = {
        accounts[0].email: hash_password("uk-real-pw"),
        accounts[1].email: hash_password("eu-real-pw"),
        accounts[2].email: hash_password("admin-real-pw"),
    }

    def fake_hash(_conn: object, email: str) -> str | None:
        return real_hashes.get(email)

    monkeypatch.setattr(module, "_existing_password_hash", fake_hash)

    blocked = module._downgrade_candidates(  # type: ignore[attr-defined]
        conn=object(),  # unused — _existing_password_hash is stubbed
        accounts=accounts,
        resolved=resolved,
        from_dev_default=from_dev_default,
    )
    # All three would be downgraded; all three must be flagged.
    assert sorted(blocked) == sorted(real_hashes.keys())


def test_downgrade_guard_allows_dev_default_over_matching_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row already holding the dev-default hash is a safe no-op rotate.

    The guard MUST NOT flag accounts whose stored hash already verifies
    against the dev default — otherwise idempotent re-runs of
    --allow-dev-defaults would abort spuriously.
    """
    module = _load_script_module()
    accounts = module._accounts()  # type: ignore[attr-defined]
    hash_password = module.hash_password  # type: ignore[attr-defined]

    resolved = {a.email: a.password_default for a in accounts}
    from_dev_default = {a.email for a in accounts}

    # Each row already holds the dev-default hash. Argon2 is salted, so
    # hash_password(default) produces a different ciphertext every call;
    # verify_password against the same plaintext still returns True.
    matching_hashes = {a.email: hash_password(a.password_default) for a in accounts}

    def fake_hash(_conn: object, email: str) -> str | None:
        return matching_hashes.get(email)

    monkeypatch.setattr(module, "_existing_password_hash", fake_hash)

    blocked = module._downgrade_candidates(  # type: ignore[attr-defined]
        conn=object(),
        accounts=accounts,
        resolved=resolved,
        from_dev_default=from_dev_default,
    )
    assert blocked == []


def test_downgrade_guard_ignores_env_var_sourced_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accounts whose password came from the env var are never blocked.

    The guard's invariant is "do not downgrade a real credential to a
    dev default". If the resolved password came from the env var, the
    rotate isn't a downgrade — even if the stored hash diverges, the
    operator explicitly set a new password.
    """
    module = _load_script_module()
    accounts = module._accounts()  # type: ignore[attr-defined]
    hash_password = module.hash_password  # type: ignore[attr-defined]

    # No account is in from_dev_default — every password came from an
    # env var. The guard should short-circuit before _existing_password_hash
    # is even consulted, but stub it pessimistically anyway.
    resolved = {a.email: "real-pw-" + a.email for a in accounts}
    from_dev_default: set[str] = set()
    real_hashes = {a.email: hash_password("something-totally-different") for a in accounts}

    def fake_hash(_conn: object, email: str) -> str | None:
        return real_hashes.get(email)

    monkeypatch.setattr(module, "_existing_password_hash", fake_hash)

    blocked = module._downgrade_candidates(  # type: ignore[attr-defined]
        conn=object(),
        accounts=accounts,
        resolved=resolved,
        from_dev_default=from_dev_default,
    )
    assert blocked == []


def test_downgrade_guard_skips_absent_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh provision (no existing row) is a CREATE, not a downgrade."""
    module = _load_script_module()
    accounts = module._accounts()  # type: ignore[attr-defined]

    resolved = {a.email: a.password_default for a in accounts}
    from_dev_default = {a.email for a in accounts}

    # No row exists for any account yet.
    def fake_hash(_conn: object, _email: str) -> str | None:
        return None

    monkeypatch.setattr(module, "_existing_password_hash", fake_hash)

    blocked = module._downgrade_candidates(  # type: ignore[attr-defined]
        conn=object(),
        accounts=accounts,
        resolved=resolved,
        from_dev_default=from_dev_default,
    )
    assert blocked == []


def test_opt_in_still_prefers_env_var_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--allow-dev-defaults`` never *overrides* a present env var."""
    module = _load_script_module()
    monkeypatch.setenv("HORIZONS_DEMO_ADMIN_PASSWORD", "admin-real-pw")
    monkeypatch.delenv("HORIZONS_DEMO_UK_PASSWORD", raising=False)
    monkeypatch.delenv("HORIZONS_DEMO_EU_PASSWORD", raising=False)

    resolved, _, from_dev_default = module._resolve_passwords(  # type: ignore[attr-defined]
        module._accounts(),  # type: ignore[attr-defined]
        allow_dev_defaults=True,
    )
    # Admin env-var wins; UK/EU fall back to the bake-in defaults.
    assert resolved["admin-demo@example.test"] == "admin-real-pw"
    assert resolved["demo-uk@example.test"] == "demo-uk-pass-not-secret"
    assert resolved["demo-eu@example.test"] == "demo-eu-pass-not-secret"
    # The from_dev_default set drives the no-downgrade rotate guard.
    # Admin is excluded because the env-var sourced its password.
    assert from_dev_default == {
        "demo-uk@example.test",
        "demo-eu@example.test",
    }
