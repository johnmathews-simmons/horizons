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

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from horizons_core.core.alignment import Clause  # noqa: TC002
from horizons_ingestion.seed import CuratedSet, DocOverride, run_seed
from sqlalchemy import text

if TYPE_CHECKING:
    from .conftest import MigratedDb


_WU86_REPO_ROOT = Path(__file__).resolve().parents[2]
_WU86_SAMPLES_DIR = _WU86_REPO_ROOT / "data" / "samples"
_WU86_FIXTURES_JSON = _WU86_SAMPLES_DIR / "fixtures.json"


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


# --- WU8.6: v1 clause staging -----------------------------------------------


def _wu86_curated() -> CuratedSet:
    """A two-doc curated set referencing real fixtures on disk."""
    return CuratedSet(
        jurisdictions=frozenset({"GB", "AU"}),
        sectors=("BANKING",),
        default_cadence_hours=24,
        overrides={
            "28914588": DocOverride(jurisdiction="UK", sector="BANKING"),
            "2145602": DocOverride(jurisdiction="UK", sector="BANKING"),
        },
    )


# The WU8.6 test scenario was authored against a 2-doc curated subset
# (one GB, one AU). The on-disk inventory has since grown — WU8.7 added
# native GB/EU captures — so loading the full fixtures.json into these
# tests pulls in extra rows the assertions weren't designed for. Pin
# the inventory to the two ids ``_wu86_curated`` actually cares about so
# the scenario stays stable when ``data/samples/fixtures.json`` grows.
_WU86_FIXTURE_IDS: frozenset[str] = frozenset({"28914588", "2145602"})


def _wu86_load_fixtures() -> list[dict[str, Any]]:
    """Load the on-disk fixtures inventory, filtered to the WU8.6 scenario set."""
    all_fixtures = json.loads(_WU86_FIXTURES_JSON.read_text(encoding="utf-8"))["fixtures"]
    return [f for f in all_fixtures if f["document_id"] in _WU86_FIXTURE_IDS]


def test_run_seed_stages_v1_for_every_curated_doc(migrated_db: MigratedDb) -> None:
    """With samples_dir set, every curated doc gets a v1 + clauses + parked schedule."""
    dsn = migrated_db.sync_engine.url.render_as_string(hide_password=False)
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    result = run_seed(
        dsn=dsn,
        curated=_wu86_curated(),
        fixtures=_wu86_load_fixtures(),
        now=now,
        samples_dir=_WU86_SAMPLES_DIR,
    )

    assert result.documents_inserted == 2
    assert result.schedules_inserted == 2
    assert result.v1_documents_staged == 2
    assert result.v1_parse_failures == 0
    assert result.v1_skipped_missing_fixture == 0
    assert result.v1_skipped_synthetic_v2 == 0
    assert result.v1_clauses_inserted > 0

    with migrated_db.sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT d.lawstronaut_document_id, dv.version_label, "
                "       count(c.id) AS n_clauses, ds.next_poll_at "
                "FROM documents d "
                "JOIN document_versions dv ON dv.document_id = d.id "
                "JOIN clauses c ON c.document_version_id = dv.id "
                "JOIN document_poll_schedule ds ON ds.document_id = d.id "
                "GROUP BY d.lawstronaut_document_id, dv.version_label, ds.next_poll_at "
                "ORDER BY d.lawstronaut_document_id"
            )
        ).all()

    assert len(rows) == 2
    by_id = {row.lawstronaut_document_id: row for row in rows}
    parked = datetime(2026, 12, 31, 0, 0, tzinfo=UTC)
    for doc_id in ("28914588", "2145602"):
        assert by_id[doc_id].version_label == "v1"
        assert by_id[doc_id].n_clauses > 0
        # The next_poll_at column carries timezone-awareness; the assertion
        # must be tz-aware to match. _STAGED_NEXT_POLL_AT is at 2026-12-31.
        actual = by_id[doc_id].next_poll_at
        if actual.tzinfo is None:
            actual = actual.replace(tzinfo=UTC)
        assert actual == parked


def test_run_seed_v1_idempotent(migrated_db: MigratedDb) -> None:
    """A second run with the same inputs is a no-op across all v1 counters."""
    dsn = migrated_db.sync_engine.url.render_as_string(hide_password=False)
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    curated = _wu86_curated()
    fixtures = _wu86_load_fixtures()

    first = run_seed(
        dsn=dsn,
        curated=curated,
        fixtures=fixtures,
        now=now,
        samples_dir=_WU86_SAMPLES_DIR,
    )
    assert first.v1_documents_staged == 2

    second = run_seed(
        dsn=dsn,
        curated=curated,
        fixtures=fixtures,
        now=now,
        samples_dir=_WU86_SAMPLES_DIR,
    )
    assert second.documents_inserted == 0
    assert second.documents_skipped_conflict == 2
    # Re-run gates v1 staging on inserted_id != None, so all v1 counters
    # report 0 — even ``v1_skipped_synthetic_v2``, which only increments
    # for freshly-inserted docs in skip_v1_for.
    assert second.v1_documents_staged == 0
    assert second.v1_clauses_inserted == 0
    assert second.v1_parse_failures == 0
    assert second.v1_skipped_missing_fixture == 0
    assert second.v1_skipped_synthetic_v2 == 0


def test_run_seed_parser_failure_does_not_abort(
    migrated_db: MigratedDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the parser raises on one doc, the rest of the seed still completes."""
    import horizons_ingestion.seed as seed_mod

    # ``parse`` is imported into ``seed_mod`` from horizons_core.core.alignment
    # but not re-exported in ``__all__`` — pragma silences pyright's
    # reportPrivateImportUsage on direct attribute access.
    original = seed_mod.parse  # pyright: ignore[reportPrivateImportUsage]
    call_count = {"n": 0}

    def _selective_raise(text_arg: str) -> Clause:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic parser failure on first doc")
        return original(text_arg)

    monkeypatch.setattr(seed_mod, "parse", _selective_raise)

    warnings: list[str] = []
    result = run_seed(
        dsn=migrated_db.sync_engine.url.render_as_string(hide_password=False),
        curated=_wu86_curated(),
        fixtures=_wu86_load_fixtures(),
        now=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        samples_dir=_WU86_SAMPLES_DIR,
        warn=warnings.append,
    )

    # Both documents land in `documents`; only one gets a v1 staged.
    assert result.documents_inserted == 2
    assert result.v1_documents_staged == 1
    assert result.v1_parse_failures == 1
    assert any("parser failed" in w for w in warnings)


def test_run_seed_skips_v1_for_synthetic_v2_paired(migrated_db: MigratedDb) -> None:
    """Docs in skip_v1_for don't get v1 staged via run_seed."""
    dsn = migrated_db.sync_engine.url.render_as_string(hide_password=False)
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    result = run_seed(
        dsn=dsn,
        curated=_wu86_curated(),
        fixtures=_wu86_load_fixtures(),
        now=now,
        samples_dir=_WU86_SAMPLES_DIR,
        skip_v1_for={"28914588"},  # GB Foat v DWP has a synthetic v2 sibling.
    )

    # 2 docs inserted, only 1 gets a v1 (AU); the GB doc is skipped.
    assert result.documents_inserted == 2
    assert result.v1_documents_staged == 1
    assert result.v1_skipped_synthetic_v2 == 1

    with migrated_db.sync_engine.connect() as conn:
        ids = (
            conn.execute(
                text(
                    "SELECT d.lawstronaut_document_id "
                    "FROM documents d JOIN document_versions dv ON dv.document_id = d.id "
                    "ORDER BY d.lawstronaut_document_id"
                )
            )
            .scalars()
            .all()
        )
    assert list(ids) == ["2145602"]  # GB 28914588 has no document_versions row.
