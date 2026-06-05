"""Integration tests for the WU3.5 curated-set seed.

Drives :func:`horizons_ingestion.seed.run_seed` against a testcontainers
Postgres 18 with the full Alembic tree. Asserts:

- A fresh DB + YAML + fixture list produces one row per matched fixture in
  ``documents`` and a matching row in ``document_poll_schedule``.
- ``next_poll_at`` values fall within ``[now, now + cadence)`` per cadence bucket.
- A second run with the same inputs inserts zero new rows (idempotency).
- An override referencing a fixture id absent from the inventory is reported
  on the warn callback and produces no row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from horizons_ingestion.seed import CuratedSet, DocOverride, run_seed
from sqlalchemy import text

if TYPE_CHECKING:
    from .conftest import MigratedDb


pytestmark = pytest.mark.integration


# --- Fixtures shared by the test cases ---------------------------------------


def _curated_set() -> CuratedSet:
    return CuratedSet(
        jurisdictions=frozenset({"IE", "GB", "BE"}),
        sectors=("financial-services", "employment"),
        default_cadence_hours=24,
        overrides={
            "8064194": DocOverride(cadence_hours=1),
            "19194112": DocOverride(sector="employment"),
            "28914588": DocOverride(sector="employment", title="Foat v DWP override"),
        },
    )


def _fixtures() -> list[dict[str, Any]]:
    return [
        {"iso": "IE", "document_id": "8064194", "title": "CRO Social Media Policy"},
        {"iso": "BE", "document_id": "19194112", "title": "BE labour"},
        {"iso": "GB", "document_id": "28914588", "title": "GB DWP"},
        {"iso": "FR", "document_id": "99999999", "title": "should be filtered"},
    ]


def _fetch_seeded_rows(sync_engine: Any) -> list[dict[str, Any]]:
    with sync_engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT d.lawstronaut_document_id, d.jurisdiction, d.sector, "
                    "       d.title, "
                    "       extract(epoch from s.cadence_interval) AS cadence_s, "
                    "       s.next_poll_at, s.failure_count, s.last_polled_at "
                    "  FROM documents d "
                    "  JOIN document_poll_schedule s ON s.document_id = d.id "
                    " ORDER BY d.lawstronaut_document_id"
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


# --- Test cases --------------------------------------------------------------


def test_seed_writes_one_row_per_matched_fixture(migrated_db: MigratedDb) -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    result = run_seed(
        dsn=migrated_db.sync_engine.url.render_as_string(hide_password=False),
        curated=_curated_set(),
        fixtures=_fixtures(),
        now=now,
    )

    assert result.documents_inserted == 3
    assert result.schedules_inserted == 3
    assert result.documents_skipped_conflict == 0

    rows = _fetch_seeded_rows(migrated_db.sync_engine)
    by_id = {r["lawstronaut_document_id"]: r for r in rows}

    assert set(by_id) == {"8064194", "19194112", "28914588"}

    # Default sector + cadence
    assert by_id["8064194"]["jurisdiction"] == "IE"
    assert by_id["8064194"]["sector"] == "financial-services"
    assert by_id["8064194"]["title"] == "CRO Social Media Policy"
    assert float(by_id["8064194"]["cadence_s"]) == 3600.0  # 1h override
    assert by_id["8064194"]["failure_count"] == 0
    assert by_id["8064194"]["last_polled_at"] is None

    # Sector override
    assert by_id["19194112"]["jurisdiction"] == "BE"
    assert by_id["19194112"]["sector"] == "employment"
    assert float(by_id["19194112"]["cadence_s"]) == 86400.0  # 24h default

    # Sector + title override
    assert by_id["28914588"]["jurisdiction"] == "GB"
    assert by_id["28914588"]["sector"] == "employment"
    assert by_id["28914588"]["title"] == "Foat v DWP override"


def test_seed_next_poll_at_within_cadence_window(migrated_db: MigratedDb) -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    run_seed(
        dsn=migrated_db.sync_engine.url.render_as_string(hide_password=False),
        curated=_curated_set(),
        fixtures=_fixtures(),
        now=now,
    )

    by_id = {r["lawstronaut_document_id"]: r for r in _fetch_seeded_rows(migrated_db.sync_engine)}

    # All next_poll_at values must fall in [now, now + cadence) for their bucket.
    for lid, row in by_id.items():
        cadence = timedelta(seconds=float(row["cadence_s"]))
        next_poll = row["next_poll_at"]
        assert now <= next_poll < now + cadence, (
            f"{lid}: next_poll_at {next_poll} outside [{now}, {now + cadence})"
        )


def test_seed_is_idempotent(migrated_db: MigratedDb) -> None:
    """Re-running with identical inputs inserts zero new rows."""
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    dsn = migrated_db.sync_engine.url.render_as_string(hide_password=False)
    curated = _curated_set()
    fixtures = _fixtures()

    first = run_seed(dsn=dsn, curated=curated, fixtures=fixtures, now=now)
    assert first.documents_inserted == 3

    second = run_seed(dsn=dsn, curated=curated, fixtures=fixtures, now=now)
    assert second.documents_inserted == 0
    assert second.schedules_inserted == 0
    assert second.documents_skipped_conflict == 3

    # Row count unchanged.
    rows = _fetch_seeded_rows(migrated_db.sync_engine)
    assert len(rows) == 3


def test_seed_idempotent_after_partial_state(migrated_db: MigratedDb) -> None:
    """Re-runs with the YAML extended pick up only the new entries."""
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    dsn = migrated_db.sync_engine.url.render_as_string(hide_password=False)
    curated = _curated_set()

    # First pass: only IE fixture available.
    fixtures_first = [_fixtures()[0]]
    first = run_seed(dsn=dsn, curated=curated, fixtures=fixtures_first, now=now)
    assert first.documents_inserted == 1

    # Second pass: full inventory. Only the two new docs land.
    second = run_seed(dsn=dsn, curated=curated, fixtures=_fixtures(), now=now)
    assert second.documents_inserted == 2
    assert second.documents_skipped_conflict == 1


def test_seed_warns_on_override_referencing_unknown_fixture(migrated_db: MigratedDb) -> None:
    curated = CuratedSet(
        jurisdictions=frozenset({"IE"}),
        sectors=("financial-services",),
        default_cadence_hours=24,
        overrides={"does-not-exist": DocOverride(cadence_hours=1)},
    )
    warnings: list[str] = []
    result = run_seed(
        dsn=migrated_db.sync_engine.url.render_as_string(hide_password=False),
        curated=curated,
        fixtures=_fixtures(),
        now=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
        warn=warnings.append,
    )

    # Only IE fixture matches, and no override references the existing IE id.
    assert result.documents_inserted == 1
    assert any("does-not-exist" in w for w in warnings)


def test_seed_handles_empty_curation(migrated_db: MigratedDb) -> None:
    curated = CuratedSet(
        jurisdictions=frozenset({"XX"}),  # no fixture has this ISO
        sectors=("financial-services",),
        default_cadence_hours=24,
        overrides={},
    )
    result = run_seed(
        dsn=migrated_db.sync_engine.url.render_as_string(hide_password=False),
        curated=curated,
        fixtures=_fixtures(),
        now=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
    )
    assert result.documents_inserted == 0
    assert result.schedules_inserted == 0
    assert _fetch_seeded_rows(migrated_db.sync_engine) == []
