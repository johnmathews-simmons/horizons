"""Seed `documents` + `document_poll_schedule` from the curated set.

Run from the repo root, in the workspace venv:

    uv run scripts/seed_curated_set.py [--dry-run]

Reads:
  - data/curated_set.yaml       — curation policy
  - data/samples/fixtures.json  — upstream fixture inventory

Writes (idempotent):
  - documents                   — one row per matched, in-scope fixture
  - document_poll_schedule      — one row per matched document

DSN comes from $HORIZONS_DB_URL (same env var Alembic reads).
See docs/seeding.md for the YAML schema and idempotency contract.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from horizons_ingestion.seed import parse_curated_set, run_seed

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_YAML = REPO_ROOT / "data" / "curated_set.yaml"
DEFAULT_FIXTURES = REPO_ROOT / "data" / "samples" / "fixtures.json"


def _print_warning(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--curated", type=Path, default=DEFAULT_YAML,
        help=f"path to curated_set.yaml (default: {DEFAULT_YAML.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--fixtures", type=Path, default=DEFAULT_FIXTURES,
        help=f"path to fixtures.json (default: {DEFAULT_FIXTURES.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the plan; insert nothing",
    )
    args = parser.parse_args(argv)

    if not args.curated.exists():
        print(f"error: curated set YAML not found at {args.curated}", file=sys.stderr)
        return 1
    if not args.fixtures.exists():
        print(f"error: fixtures inventory not found at {args.fixtures}", file=sys.stderr)
        return 1

    dsn = os.environ.get("HORIZONS_DB_URL")
    if not dsn and not args.dry_run:
        print("error: HORIZONS_DB_URL is not set (required unless --dry-run)", file=sys.stderr)
        return 1

    try:
        curated = parse_curated_set(args.curated.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"error: {args.curated.name} is invalid: {exc}", file=sys.stderr)
        return 1

    fixtures_doc = json.loads(args.fixtures.read_text(encoding="utf-8"))
    fixtures = fixtures_doc.get("fixtures", [])
    if not isinstance(fixtures, list):
        print(f"error: {args.fixtures.name} has no 'fixtures' list", file=sys.stderr)
        return 1

    result = run_seed(
        dsn=dsn or "",
        curated=curated,
        fixtures=fixtures,
        now=datetime.now(UTC),
        warn=_print_warning,
        dry_run=args.dry_run,
    )

    label = "would insert" if args.dry_run else "inserted"
    print(f"{label}: {result.documents_inserted} document(s)")
    print(f"{label}: {result.schedules_inserted} schedule row(s)")
    if result.documents_skipped_conflict:
        print(f"skipped (already present): {result.documents_skipped_conflict} document(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
