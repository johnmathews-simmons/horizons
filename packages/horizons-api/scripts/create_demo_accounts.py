#!/usr/bin/env python
"""Create (or reset) the WU8.1 demo accounts for the public showcase.

Three accounts are provisioned, all under the IETF-reserved
``@example.test`` TLD so they are trivially identifiable and cannot
collide with real client data:

* ``demo-uk@example.test`` — role=client, subscription
  (jurisdiction=UK, sector=BANKING)
* ``demo-eu@example.test`` — role=client, subscription
  (jurisdiction=EU, sector=BANKING)
* ``admin-demo@example.test`` — role=admin, no subscription

Passwords are read from environment variables. By default ALL THREE must
be set explicitly; missing variables abort the run before any DB write.
The env-var names are:

  HORIZONS_DEMO_UK_PASSWORD
  HORIZONS_DEMO_EU_PASSWORD
  HORIZONS_DEMO_ADMIN_PASSWORD

``--allow-dev-defaults`` opts into the local-dev fallback passwords
baked into the source (visible to anyone who reads the file). The opt-in
is intentional: the admin account has cross-tenant read access via the
WU1.9 audit path, and the demo is publicly reachable for 1–2 days during
the showcase. The default refusal closes the "operator forgot the
override on production" footgun. ``--allow-dev-defaults`` is for
localhost development only and is never appropriate in any environment
reachable beyond your laptop.

These accounts use direct SQL writes (mirroring ``seed_e2e.py``) rather
than the WU4.5 ``/v1/admin/subscriptions`` HTTP path: the admin endpoint
flow itself requires an admin bearer, so bootstrapping the very first
admin account chicken-and-egg's through HTTP. Direct SQL is the
documented bootstrap seam — see ``docs/runbooks/demo-accounts.md`` for
the trade-offs.

Distinct from the WU8.2 Playwright e2e fixtures: those live under
``@e2e.test`` and ``seed_e2e.py``; the demo accounts here are for the
manual showcase walk-through.

**Idempotency rotates credentials.** A re-run UPDATEs the
``password_hash`` of any existing demo row to match the freshly resolved
password — re-running with new env-var values rotates the stored hash
without needing ``--reset``. A previous version of this script
silently skipped existing rows; that preserved stale (default-bake)
hashes if the operator forgot the env vars on the first run, then set
them on the second.

``--reset`` performs a teardown of every demo account (and its
subscriptions / scopes / watchlists) before recreating. Use it when you
want to delete watchlist state or otherwise rewind to a clean slate.

Run from the repo root:

    HORIZONS_DEMO_UK_PASSWORD=... \\
    HORIZONS_DEMO_EU_PASSWORD=... \\
    HORIZONS_DEMO_ADMIN_PASSWORD=... \\
    HORIZONS_DB_URL=postgresql+psycopg://postgres:postgres@localhost:5432/horizons \\
        uv run packages/horizons-api/scripts/create_demo_accounts.py [--reset]

Both ``+psycopg`` and ``+asyncpg`` forms of ``HORIZONS_DB_URL`` are
accepted; the script rewrites to psycopg internally so the same env var
that uvicorn reads works here unchanged.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import sqlalchemy
from horizons_core.core.auth import hash_password
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy import Connection


# --- Account inventory ------------------------------------------------------


DEMO_EMAIL_LIKE = "%@example.test"

UK_EMAIL = "demo-uk@example.test"
EU_EMAIL = "demo-eu@example.test"
ADMIN_EMAIL = "admin-demo@example.test"

# Dev-only fallback passwords. Used only when --allow-dev-defaults is
# passed. They are visible to anyone who reads the source and are NEVER
# acceptable in any environment reachable beyond localhost. The noqa
# pins B105 (hard-coded password) at the literal sites; the opt-in flag
# is the substantive guard.
_DEFAULT_UK_PASSWORD = "demo-uk-pass-not-secret"  # noqa: S105
_DEFAULT_EU_PASSWORD = "demo-eu-pass-not-secret"  # noqa: S105
_DEFAULT_ADMIN_PASSWORD = "admin-demo-pass-not-secret"  # noqa: S105


@dataclass(frozen=True)
class DemoAccount:
    """One row in the demo-account inventory."""

    email: str
    role: str
    password_env: str
    password_default: str
    # ``scope`` is non-None for clients. Admin has no subscription.
    scope: tuple[str, str] | None


def _accounts() -> list[DemoAccount]:
    return [
        DemoAccount(
            email=UK_EMAIL,
            role="client",
            password_env="HORIZONS_DEMO_UK_PASSWORD",
            password_default=_DEFAULT_UK_PASSWORD,
            scope=("UK", "BANKING"),
        ),
        DemoAccount(
            email=EU_EMAIL,
            role="client",
            password_env="HORIZONS_DEMO_EU_PASSWORD",
            password_default=_DEFAULT_EU_PASSWORD,
            scope=("EU", "BANKING"),
        ),
        DemoAccount(
            email=ADMIN_EMAIL,
            role="admin",
            password_env="HORIZONS_DEMO_ADMIN_PASSWORD",
            password_default=_DEFAULT_ADMIN_PASSWORD,
            scope=None,
        ),
    ]


# --- DSN helper -------------------------------------------------------------


def _normalise_db_url(raw: str) -> str:
    """Convert any ``HORIZONS_DB_URL`` driver hint to sync ``+psycopg``.

    Mirrors the helper in ``seed_e2e.py`` so all bootstrap scripts share
    one DSN parse rule.
    """
    if "+asyncpg" in raw:
        return raw.replace("+asyncpg", "+psycopg")
    if raw.startswith("postgresql://") or raw.startswith("postgres://"):
        scheme, rest = raw.split("://", 1)
        return f"{scheme.replace('postgres', 'postgresql')}+psycopg://{rest}"
    return raw


# --- Password resolution ----------------------------------------------------


def _resolve_passwords(
    accounts: list[DemoAccount],
    *,
    allow_dev_defaults: bool,
) -> tuple[dict[str, str], list[str]]:
    """Resolve every account's password, or surface the missing env vars.

    Returns ``(resolved, missing_env_vars)``. When ``missing_env_vars`` is
    non-empty the caller must abort: ``resolved`` is partial and the
    operator has not explicitly opted into the dev-default fallback.

    With ``allow_dev_defaults=True``, missing env vars are silently
    replaced by ``password_default``; the returned ``missing_env_vars``
    is always empty.
    """
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for account in accounts:
        env_value = os.environ.get(account.password_env)
        if env_value is not None and env_value != "":
            resolved[account.email] = env_value
            continue
        if allow_dev_defaults:
            resolved[account.email] = account.password_default
            continue
        missing.append(account.password_env)
    return resolved, missing


# --- Teardown --------------------------------------------------------------


def _teardown(conn: Connection) -> None:
    """Remove every demo-account row plus its dependants. Safe when empty.

    Watchlists, subscription_scopes, subscriptions, and users are all
    purgeable; refresh_tokens cascade via FK ON DELETE.

    The append-only triggers on ``subscriptions`` / ``subscription_scopes``
    reject UPDATEs by default. Plain DELETEs are unaffected by the
    UPDATE triggers, so no replication-role bypass is required here
    (unlike ``seed_e2e.py``, which deletes from append-only
    ``change_events``).
    """
    params = {"p": DEMO_EMAIL_LIKE}

    conn.execute(
        sqlalchemy.text(
            "DELETE FROM watchlists WHERE user_id IN ("
            "  SELECT id FROM users WHERE email LIKE :p"
            ")"
        ),
        params,
    )
    conn.execute(
        sqlalchemy.text(
            "DELETE FROM subscription_scopes WHERE subscription_id IN ("
            "  SELECT s.id FROM subscriptions s "
            "  JOIN users u ON u.id = s.user_id "
            "  WHERE u.email LIKE :p"
            ")"
        ),
        params,
    )
    conn.execute(
        sqlalchemy.text(
            "DELETE FROM subscriptions WHERE user_id IN ("
            "  SELECT id FROM users WHERE email LIKE :p"
            ")"
        ),
        params,
    )
    conn.execute(
        sqlalchemy.text("DELETE FROM users WHERE email LIKE :p"),
        params,
    )


# --- Create-or-rotate -----------------------------------------------------


def _account_exists(conn: Connection, email: str) -> bool:
    return (
        conn.execute(
            sqlalchemy.text("SELECT 1 FROM users WHERE email = :e"),
            {"e": email},
        ).first()
        is not None
    )


def _create_user(conn: Connection, account: DemoAccount, plaintext: str) -> uuid.UUID:
    return conn.execute(
        sqlalchemy.text(
            "INSERT INTO users (email, password_hash, role) "
            "VALUES (:e, :ph, CAST(:r AS user_role)) RETURNING id"
        ),
        {"e": account.email, "ph": hash_password(plaintext), "r": account.role},
    ).scalar_one()


def _rotate_password(conn: Connection, email: str, plaintext: str) -> None:
    """UPDATE the stored password hash for an existing user.

    Always-rotate on re-run is the chosen idempotency contract: it
    catches the "ran once with defaults, set env vars on second run"
    case that a silent skip would leave broken.
    """
    conn.execute(
        sqlalchemy.text("UPDATE users SET password_hash = :ph WHERE email = :e"),
        {"e": email, "ph": hash_password(plaintext)},
    )


def _subscribe(conn: Connection, user_id: uuid.UUID, scope: tuple[str, str]) -> None:
    jurisdiction, sector = scope
    valid_from = datetime.now(UTC) - timedelta(days=30)
    sub_id = conn.execute(
        sqlalchemy.text(
            "INSERT INTO subscriptions (user_id, valid_from, valid_to) "
            "VALUES (:u, :f, NULL) RETURNING id"
        ),
        {"u": user_id, "f": valid_from},
    ).scalar_one()
    conn.execute(
        sqlalchemy.text(
            "INSERT INTO subscription_scopes "
            "(subscription_id, jurisdiction, sector, valid_to) "
            "VALUES (:s, :j, :sec, NULL)"
        ),
        {"s": sub_id, "j": jurisdiction, "sec": sector},
    )


def _create_or_rotate(
    conn: Connection, account: DemoAccount, plaintext: str
) -> str:
    """Return one of ``"created"`` / ``"rotated"``."""
    if _account_exists(conn, account.email):
        _rotate_password(conn, account.email, plaintext)
        return "rotated"
    user_id = _create_user(conn, account, plaintext)
    if account.scope is not None:
        _subscribe(conn, user_id, account.scope)
    return "created"


# --- CLI ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create or reset Horizons demo accounts (WU8.1).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="teardown all demo accounts (and dependants) before recreating",
    )
    parser.add_argument(
        "--allow-dev-defaults",
        action="store_true",
        help=(
            "fall back to the dev-default passwords baked into the source "
            "when an env var is unset. Localhost-only; never appropriate "
            "for any environment reachable beyond your laptop"
        ),
    )
    args = parser.parse_args(argv)

    raw = os.environ.get("HORIZONS_DB_URL")
    if not raw:
        print("HORIZONS_DB_URL is required", file=sys.stderr)
        return 1
    url = _normalise_db_url(raw)

    accounts = _accounts()
    resolved, missing = _resolve_passwords(
        accounts, allow_dev_defaults=args.allow_dev_defaults
    )
    if missing:
        print(
            "refusing to provision: the following password env vars are not set: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        print(
            "set them, OR pass --allow-dev-defaults if this is localhost-only dev.",
            file=sys.stderr,
        )
        return 1

    engine = create_engine(url, future=True)
    try:
        with engine.begin() as conn:
            if args.reset:
                _teardown(conn)
            outcomes: dict[str, str] = {}
            for account in accounts:
                outcomes[account.email] = _create_or_rotate(
                    conn, account, resolved[account.email]
                )
        print("demo accounts:")
        for email, outcome in outcomes.items():
            print(f"  {email}: {outcome}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
