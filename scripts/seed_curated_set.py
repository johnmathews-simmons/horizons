"""Seed `documents` + `document_poll_schedule` from the curated set.

Run from the repo root, in the workspace venv:

    uv run scripts/seed_curated_set.py [--dry-run] [--stage-synthetic-v2]

Reads:
  - data/curated_set.yaml             — curation policy
  - data/samples/fixtures.json        — upstream fixture inventory
  - data/samples/synthetic_v2/*.md    — WU8.0 synthetic v2 markdown
                                          (only when --stage-synthetic-v2)

Writes (idempotent):
  - documents                   — one row per matched, in-scope fixture
  - document_poll_schedule      — one row per matched document
  - document_versions, clauses, change_events — one v1 + one v2 (and
                                  their alignment result) per pair when
                                  --stage-synthetic-v2 is passed

DSN comes from $HORIZONS_DB_URL (same env var Alembic reads).
See docs/runbooks/seeding.md for the YAML schema and idempotency contract.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from horizons_ingestion.seed import (
    SyntheticV2Pair,
    parse_curated_set,
    run_seed,
    stage_synthetic_v2,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_YAML = REPO_ROOT / "data" / "curated_set.yaml"
DEFAULT_FIXTURES = REPO_ROOT / "data" / "samples" / "fixtures.json"
DEFAULT_SYNTHETIC_V2_DIR = REPO_ROOT / "data" / "samples" / "synthetic_v2"
DEFAULT_SAMPLES_DIR = REPO_ROOT / "data" / "samples"

# WU8.0 filename convention: <iso>-<document_id>-v<n>.md.
_V2_FILENAME_RE = re.compile(r"^(?P<iso>[a-z]{2})-(?P<docid>\d+)-v2\.md$")


def _print_warning(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _discover_v2_pairs(
    synthetic_v2_dir: Path, samples_dir: Path
) -> list[SyntheticV2Pair]:
    """Find every ``<iso>-<docid>-v2.md`` under ``synthetic_v2_dir`` paired with v1."""
    pairs: list[SyntheticV2Pair] = []
    if not synthetic_v2_dir.is_dir():
        return pairs
    for v2_path in sorted(synthetic_v2_dir.glob("*.md")):
        match = _V2_FILENAME_RE.match(v2_path.name)
        if match is None:
            continue
        iso = match.group("iso")
        docid = match.group("docid")
        v1_path = samples_dir / f"{iso}-{docid}-v1.md"
        if not v1_path.exists():
            _print_warning(
                f"synthetic v2 {v2_path.name} has no v1 sibling at "
                f"{v1_path.relative_to(REPO_ROOT)}; skipped"
            )
            continue
        pairs.append(
            SyntheticV2Pair(
                lawstronaut_document_id=docid, v1_path=v1_path, v2_path=v2_path
            )
        )
    return pairs


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
    parser.add_argument(
        "--stage-synthetic-v2", action="store_true",
        help=(
            "after seeding documents + schedules, also stage the WU8.0 "
            "synthetic v2 fixtures (document_versions + clauses + "
            "change_events). Idempotent per document."
        ),
    )
    parser.add_argument(
        "--synthetic-v2-dir", type=Path, default=DEFAULT_SYNTHETIC_V2_DIR,
        help=(
            f"directory containing <iso>-<docid>-v2.md files (default: "
            f"{DEFAULT_SYNTHETIC_V2_DIR.relative_to(REPO_ROOT)})"
        ),
    )
    parser.add_argument(
        "--samples-dir", type=Path, default=DEFAULT_SAMPLES_DIR,
        help=(
            f"directory holding the v1 fixtures the v2 files pair against "
            f"(default: {DEFAULT_SAMPLES_DIR.relative_to(REPO_ROOT)})"
        ),
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

    now = datetime.now(UTC)
    result = run_seed(
        dsn=dsn or "",
        curated=curated,
        fixtures=fixtures,
        now=now,
        warn=_print_warning,
        dry_run=args.dry_run,
    )

    label = "would insert" if args.dry_run else "inserted"
    print(f"{label}: {result.documents_inserted} document(s)")
    print(f"{label}: {result.schedules_inserted} schedule row(s)")
    if result.documents_skipped_conflict:
        print(f"skipped (already present): {result.documents_skipped_conflict} document(s)")

    if args.stage_synthetic_v2:
        pairs = _discover_v2_pairs(args.synthetic_v2_dir, args.samples_dir)
        if not pairs:
            print(
                f"warning: no synthetic v2 pairs found under "
                f"{args.synthetic_v2_dir.relative_to(REPO_ROOT)}",
                file=sys.stderr,
            )
        else:
            staging = stage_synthetic_v2(
                dsn=dsn or "",
                pairs=pairs,
                now=now,
                warn=_print_warning,
                dry_run=args.dry_run,
            )
            v_label = "would stage" if args.dry_run else "staged"
            print(f"{v_label}: {staging.documents_staged} synthetic v2 document(s)")
            print(f"{v_label}: {staging.clauses_inserted} clause row(s)")
            print(f"{v_label}: {staging.change_events_inserted} change_event row(s)")
            if staging.documents_skipped_missing:
                print(
                    f"skipped (no documents row): "
                    f"{staging.documents_skipped_missing} pair(s)"
                )
            if staging.documents_skipped_already_staged:
                print(
                    f"skipped (versions already present): "
                    f"{staging.documents_skipped_already_staged} pair(s)"
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
