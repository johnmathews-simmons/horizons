"""Integration test for the WU8.0 worker-vs-staged guard.

The headline check is the worker-vs-staged guard: ``stage_synthetic_v2``
must push ``document_poll_schedule.next_poll_at`` for every staged
document well past the demo window, so the ingestion worker's
``SELECT ... FOR UPDATE SKIP LOCKED`` claim query (which filters on
``next_poll_at <= now()``) never picks the staged document up and
inserts a real-v1 "v3" that clobbers the staged change events.

The test stages a hermetic, hand-authored markdown pair (kept tiny so
the alignment pass runs in milliseconds and the clause-uniqueness
constraint is trivially satisfied), then verifies both:

1. ``document_poll_schedule.next_poll_at`` lands well past the demo.
2. The exact worker claim query (``CLAIM_SQL``) does NOT return the
   staged document's UUID.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from horizons_ingestion.loop import CLAIM_SQL
from horizons_ingestion.seed import (
    CuratedSet,
    DocOverride,
    SyntheticV2Pair,
    run_seed,
    stage_synthetic_v2,
)
from sqlalchemy import text

if TYPE_CHECKING:
    from pathlib import Path

    import asyncpg

    from .conftest import MigratedDb


pytestmark = pytest.mark.integration


_DOC_ID = "guard-test-doc-1"

# Minimal v1: three unique-pathed clauses under distinct H2 sections.
# Each section heading gives the clause a unique path under the
# alignment parser, satisfying the ``clauses_unique_path_per_version``
# uniqueness constraint regardless of parser internals.
_V1_MARKDOWN = """\
# Demo Document

## Section One

The first clause talks about apples.

## Section Two

The second clause talks about pears.

## Section Three

The third clause talks about plums.
"""

# v2: one MODIFIED clause + one ADDED section. Exact alignment outcome
# is not the contract under test — the contract is "if any staging
# committed, the schedule row was parked", regardless of which change
# types the aligner emitted.
_V2_MARKDOWN = """\
# Demo Document

## Section One

The first clause talks about apples and oranges.

## Section Two

The second clause talks about pears.

## Section Three

The third clause talks about plums.

## Section Four

A newly added clause about cherries.
"""


def _write_pair(tmp_path: Path) -> SyntheticV2Pair:
    v1_path = tmp_path / f"xx-{_DOC_ID}-v1.md"
    v2_path = tmp_path / f"xx-{_DOC_ID}-v2.md"
    v1_path.write_text(_V1_MARKDOWN, encoding="utf-8")
    v2_path.write_text(_V2_MARKDOWN, encoding="utf-8")
    return SyntheticV2Pair(
        lawstronaut_document_id=_DOC_ID,
        v1_path=v1_path,
        v2_path=v2_path,
    )


def _seed_and_stage(migrated_db: MigratedDb, tmp_path: Path) -> None:
    """Seed one document + schedule row, then stage a synthetic v2 pair."""
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    dsn = migrated_db.sync_engine.url.render_as_string(hide_password=False)

    # Make the curated-set seed pick our test document up.
    curated = CuratedSet(
        jurisdictions=frozenset({"XX"}),
        sectors=("financial-services",),
        default_cadence_hours=24,
        overrides={_DOC_ID: DocOverride(cadence_hours=1)},
    )
    fixtures = [
        {"iso": "XX", "document_id": _DOC_ID, "title": "Demo Document"},
    ]
    run_seed(dsn=dsn, curated=curated, fixtures=fixtures, now=now)

    stage_synthetic_v2(
        dsn=dsn,
        pairs=[_write_pair(tmp_path)],
        now=now,
    )


def test_stage_synthetic_v2_parks_schedule_far_future(
    migrated_db: MigratedDb,
    tmp_path: Path,
) -> None:
    """After staging, the schedule row's next_poll_at is well past the demo.

    The staging path must rewrite ``next_poll_at`` to a sentinel far
    past 2026-06-08 so the worker's claim query (``next_poll_at <=
    now()``) never picks the row up while the staged change events
    are live.
    """
    _seed_and_stage(migrated_db, tmp_path)

    with migrated_db.sync_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT s.next_poll_at "
                "  FROM document_poll_schedule s "
                "  JOIN documents d ON d.id = s.document_id "
                " WHERE d.lawstronaut_document_id = :lid"
            ),
            {"lid": _DOC_ID},
        ).one()

    parked_at = row.next_poll_at
    # The sentinel chosen in seed.py is 2026-12-31; in any case it must
    # land well past the planned 2026-06-08 demo so the schedule row is
    # never due during the showcase.
    demo_window_end = datetime(2026, 6, 30, tzinfo=UTC)
    assert parked_at > demo_window_end, (
        f"next_poll_at={parked_at} is within the demo window; the worker "
        f"would still claim this row and overwrite the staged change events"
    )


async def test_worker_claim_sql_skips_staged_document(
    migrated_db: MigratedDb,
    pool: asyncpg.Pool,
    tmp_path: Path,
) -> None:
    """The worker's CLAIM_SQL must NOT return the staged document.

    Drives the exact claim query the ingestion worker uses
    (``SELECT ... FOR UPDATE SKIP LOCKED`` filtered on ``next_poll_at
    <= now()``). The staged row's ``next_poll_at`` is parked at
    2026-12-31, so the claim must return an empty list — regardless of
    the failure_threshold and batch_size the worker is configured
    with.
    """
    _seed_and_stage(migrated_db, tmp_path)

    async with pool.acquire() as conn:
        rows = await conn.fetch(CLAIM_SQL, 5, 10)

    with migrated_db.sync_engine.connect() as sync_conn:
        staged_doc_id = sync_conn.execute(
            text("SELECT id FROM documents WHERE lawstronaut_document_id = :lid"),
            {"lid": _DOC_ID},
        ).scalar_one()

    claimed_ids = [row["document_id"] for row in rows]
    assert staged_doc_id not in claimed_ids, (
        f"worker claimed the staged document (id={staged_doc_id}); the "
        f"WU8.0 synthetic-v2 staging failed to park its schedule row"
    )
