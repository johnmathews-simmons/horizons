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

Passwords are read from environment variables with development defaults
so the script is runnable out-of-the-box for the local demo. Do NOT
publish the defaults; production demo deployments override them via the
container environment. The env-var names are:

  HORIZONS_DEMO_UK_PASSWORD     (default: demo-uk-pass-not-secret)
  HORIZONS_DEMO_EU_PASSWORD     (default: demo-eu-pass-not-secret)
  HORIZONS_DEMO_ADMIN_PASSWORD  (default: admin-demo-pass-not-secret)

These accounts use direct SQL writes (mirroring ``seed_e2e.py``) rather
than the WU4.5 ``/v1/admin/subscriptions`` HTTP path: the admin endpoint
flow itself requires an admin bearer, so bootstrapping the very first
admin account chicken-and-egg's through HTTP. Direct SQL is the
documented bootstrap seam — see ``docs/runbooks/demo-accounts.md`` for
the trade-offs.

Distinct from the WU8.2 Playwright e2e fixtures: those live under
``@e2e.test`` and ``seed_e2e.py``; the demo accounts here are for the
manual showcase walk-through.

Idempotent. Default behaviour is "create what does not exist; leave
existing rows untouched". ``--reset`` performs a teardown of every demo
account (and its subscriptions / scopes / watchlists) before recreating.

Run from the repo root:

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

# Development defaults. They are NOT secret; they are not used in the
# public demo deployment, where the env vars override them. Lint pragmas
# pin the noqa for B105 (hard-coded password) at the assignment sites.
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


# --- Create-or-skip -------------------------------------------------------


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


def _resolve_password(account: DemoAccount) -> str:
    return os.environ.get(account.password_env, account.password_default)


def _create_or_skip(conn: Connection, account: DemoAccount) -> str:
    """Return one of ``"created"`` / ``"skipped"``."""
    if _account_exists(conn, account.email):
        return "skipped"
    user_id = _create_user(conn, account, _resolve_password(account))
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
    args = parser.parse_args(argv)

    raw = os.environ.get("HORIZONS_DB_URL")
    if not raw:
        print("HORIZONS_DB_URL is required", file=sys.stderr)
        return 1
    url = _normalise_db_url(raw)

    engine = create_engine(url, future=True)
    try:
        with engine.begin() as conn:
            if args.reset:
                _teardown(conn)
            outcomes: dict[str, str] = {}
            for account in _accounts():
                outcomes[account.email] = _create_or_skip(conn, account)
        print("demo accounts:")
        for email, outcome in outcomes.items():
            print(f"  {email}: {outcome}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
