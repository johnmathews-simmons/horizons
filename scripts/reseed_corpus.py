"""One-shot wipe + re-seed of the corpus for the WU8.1 demo.

Runs INSIDE the worker container, dispatched from a laptop via
``az containerapp exec`` (see ``scripts/reseed_aca.sh``). The full
sequence:

  1. Pre-flight — verify the four required env vars are set, check
     observed row counts look like staging (refuses if user count
     exceeds ``--max-existing-users``; cheap guard against pointing at
     a real production DB by accident).
  2. Wipe — DELETE the corpus + demo-account dependants in a single
     transaction, FK-safe order.
  3. Re-seed — call ``scripts/seed_curated_set.py --stage-synthetic-v2``
     against the curated YAML + synthetic-v2 markdown baked into the
     image.
  4. Re-provision demo accounts — call
     ``scripts/create_demo_accounts.py --reset`` to rotate the three
     ``@demo.example.com`` users with the env-var passwords.
  5. Post-flight — print row counts and a one-line smoke summary.

Required env vars (inherited from the operator's shell via
``az containerapp exec``):

  HORIZONS_DB_URL                — Postgres DSN (psycopg or asyncpg form)
  HORIZONS_DEMO_UK_PASSWORD
  HORIZONS_DEMO_EU_PASSWORD
  HORIZONS_DEMO_ADMIN_PASSWORD

Flags:
  --yes                          required to perform the destructive
                                 wipe; otherwise the script prints what
                                 it WOULD do and exits 0.
  --max-existing-users INT       refuses if ``users`` row count exceeds
                                 this. Default 50.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_SCRIPT = REPO_ROOT / "scripts" / "seed_curated_set.py"
DEMO_ACCOUNTS_SCRIPT = REPO_ROOT / "scripts" / "create_demo_accounts.py"

REQUIRED_ENV = (
    "HORIZONS_DB_URL",
    "HORIZONS_DEMO_UK_PASSWORD",
    "HORIZONS_DEMO_EU_PASSWORD",
    "HORIZONS_DEMO_ADMIN_PASSWORD",
)

# DELETE order: children of documents first, then documents, then the
# subscription/user graph for the demo accounts. ``demo_accounts`` is
# wiped+rebuilt by create_demo_accounts.py --reset so we don't touch
# users here; the demo script handles its own teardown.
_WIPE_STATEMENTS = (
    "DELETE FROM change_events",
    "DELETE FROM clauses",
    "DELETE FROM document_versions",
    "DELETE FROM document_poll_schedule",
    # watchlists FK clauses(uid); explicit DELETE before clauses are gone.
    # Repeat after seed completes is a no-op so we can do it here.
    # Already covered above; clauses goes first then documents.
    "DELETE FROM documents",
)

_COUNT_TABLES = (
    "users",
    "documents",
    "document_versions",
    "clauses",
    "change_events",
)


def _normalise_db_url(raw: str) -> str:
    """Convert any DSN driver hint to sync ``+psycopg``."""
    if "+asyncpg" in raw:
        return raw.replace("+asyncpg", "+psycopg")
    if raw.startswith("postgresql://") or raw.startswith("postgres://"):
        scheme, rest = raw.split("://", 1)
        return f"{scheme.replace('postgres', 'postgresql')}+psycopg://{rest}"
    return raw


def _row_counts(conn: sqlalchemy.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in _COUNT_TABLES:
        value: int = conn.execute(
            text(f"SELECT count(*) FROM {table}")  # noqa: S608 — table name is a literal from a fixed allowlist
        ).scalar_one()
        out[table] = value
    return out


def _print_counts(label: str, counts: dict[str, int]) -> None:
    print(f"  [{label}]")
    for table, n in counts.items():
        print(f"    {table:24s} {n}")


def _host_from_dsn(dsn: str) -> str:
    """Best-effort host extraction for the confirmation print line."""
    try:
        rest = dsn.split("://", 1)[1]
        after_creds = rest.split("@", 1)[-1]
        return after_creds.split("/", 1)[0].split("?", 1)[0]
    except Exception:  # pragma: no cover — best-effort only
        return "<unparseable>"


def _check_env() -> list[str]:
    return [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]


def _run_subscript(path: Path, args: list[str]) -> int:
    """Run a sibling script with the inherited environment.

    The image's ``/opt/venv/bin/python`` is the only python on PATH, so
    invoking ``python`` directly is unambiguous.
    """
    cmd = ["python", str(path), *args]
    print(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm the destructive wipe; without this flag the script is a dry-run",
    )
    parser.add_argument(
        "--max-existing-users",
        type=int,
        default=50,
        help=(
            "refuse to run if users row count exceeds this. Cheap guard "
            "against pointing at a real production DB by accident (default: 50)"
        ),
    )
    args = parser.parse_args(argv)

    # --- 1. Pre-flight ------------------------------------------------------
    missing = _check_env()
    if missing:
        print(
            "refusing to run: the following env vars are not set: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 1

    if not SEED_SCRIPT.exists():
        print(f"refusing to run: {SEED_SCRIPT} is missing from the image", file=sys.stderr)
        return 1
    if not DEMO_ACCOUNTS_SCRIPT.exists():
        print(
            f"refusing to run: {DEMO_ACCOUNTS_SCRIPT} is missing from the image",
            file=sys.stderr,
        )
        return 1

    raw_dsn = os.environ["HORIZONS_DB_URL"]
    dsn = _normalise_db_url(raw_dsn)
    host = _host_from_dsn(dsn)
    print(f"target host: {host}")
    print(f"mode:        {'WIPE + RESEED' if args.yes else 'dry-run (pass --yes to execute)'}")

    engine = create_engine(dsn, future=True)
    try:
        with engine.connect() as conn:
            before = _row_counts(conn)
        _print_counts("before", before)

        if before["users"] > args.max_existing_users:
            print(
                f"refusing to run: users count {before['users']} > "
                f"--max-existing-users {args.max_existing_users}. "
                "This does not look like a staging DB. If you are sure, "
                "re-run with a higher --max-existing-users.",
                file=sys.stderr,
            )
            return 2

        if not args.yes:
            print(
                "\ndry-run complete; no rows touched. Re-run with --yes to "
                "execute the wipe + reseed."
            )
            return 0

        # --- 2. Wipe (transactional) ----------------------------------------
        print("\nwiping corpus tables (transactional)…")
        with engine.begin() as conn:
            for stmt in _WIPE_STATEMENTS:
                result = conn.execute(text(stmt))
                print(f"  {stmt:50s} -> {result.rowcount} row(s)")

        # --- 3. Re-seed ------------------------------------------------------
        print("\nseeding curated set + synthetic v2…")
        rc = _run_subscript(SEED_SCRIPT, ["--stage-synthetic-v2"])
        if rc != 0:
            print(f"seed_curated_set.py exited {rc}; aborting", file=sys.stderr)
            return rc

        # --- 4. Demo accounts (reset) ---------------------------------------
        print("\nre-provisioning demo accounts (reset)…")
        rc = _run_subscript(DEMO_ACCOUNTS_SCRIPT, ["--reset"])
        if rc != 0:
            print(f"create_demo_accounts.py exited {rc}; aborting", file=sys.stderr)
            return rc

        # --- 5. Post-flight --------------------------------------------------
        with engine.connect() as conn:
            after = _row_counts(conn)
        print("")
        _print_counts("after", after)

        ok = (
            after["documents"] > 0
            and after["document_versions"] > 0
            and after["clauses"] > 0
            and after["change_events"] > 0
            and after["users"] >= 3
        )
        if not ok:
            print(
                "\nWARNING: post-reseed counts look thin. Expected non-zero corpus "
                "tables and >=3 demo users; investigate the seed output above.",
                file=sys.stderr,
            )
            return 3

        print("\nOK — wipe + reseed completed.")
        return 0

    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
