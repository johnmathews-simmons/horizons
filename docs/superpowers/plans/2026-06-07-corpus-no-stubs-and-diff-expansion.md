# Corpus: no stubs + clause-diff expansion — Implementation Plan

*Last revised: 2026-06-07.*
*Path: docs/superpowers/plans/2026-06-07-corpus-no-stubs-and-diff-expansion.md.*

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 26 stub documents in the curated set with full v1 clauses staged from on-disk fixtures, and author 3 new English-language synthetic v2 pairs so each demo user sees realistic clause-level diffs covering all four change kinds (ADDED, REMOVED, MODIFIED, MOVED).

**Architecture:** Extend `horizons_ingestion.seed.run_seed` to also stage a v1 `document_versions` row + parsed `clauses` for every curated, in-scope fixture (idempotent, per-doc parser-failure tolerant, parks the poll schedule at 2026-12-31). The existing `stage_synthetic_v2` path continues to handle v2 + change events for paired fixtures. New synthetic v2 fixtures (`ie-27732019-v2.md`, `au-2145602-v2.md`, `eu-31366184-v2.md`, plus a MOVED edit appended into the existing IE plan) are picked up automatically by the existing `_discover_v2_pairs` glob.

**Tech Stack:** Python 3.13, `uv`, SQLAlchemy 2.x, Pydantic, `pytest` (+ `testcontainers`), Vue 3 + Vitest, Playwright; Postgres 18; deployed via Bicep into Azure Container Apps.

**Spec:** [`docs/superpowers/specs/2026-06-07-corpus-no-stubs-and-diff-expansion-design.md`](../specs/2026-06-07-corpus-no-stubs-and-diff-expansion-design.md).

---

## File map

**Modify (Python):**
- `packages/horizons-ingestion/src/horizons_ingestion/seed.py` — extract shared `_stage_v1_only` helper; extend `run_seed` to stage v1 clauses after each `documents`/`document_poll_schedule` insert; park `next_poll_at`; add per-doc parser-failure tolerance.
- `packages/horizons-ingestion/tests/test_seed_helpers.py` — additional unit tests at the library layer for v1 staging (parser-failure, idempotency). Note: the existing file is the only seed unit test file in the workspace; integration-level tests live alongside, gated by the `integration` marker.
- `scripts/seed_curated_set.py` — adjust the printed result lines to report v1 staging counts.
- `scripts/reseed_corpus.py` — confirm FK-safe wipe order already covers v1 staging. (Read-only check; may not need edits — verify in task.)

**Create (markdown fixtures):**
- `data/samples/synthetic_v2/ie-27732019-v2.md` — IE Statute Book v2 with MOVED + MODIFIED edits.
- `data/samples/synthetic_v2/au-2145602-v2.md` — Australian doc v2 with ADDED + REMOVED edits.
- `data/samples/synthetic_v2/eu-31366184-v2.md` — BEREC EU press item v2 with MODIFIED + REMOVED edits.

**Modify (markdown):**
- `data/samples/synthetic_v2/README.md` — append rows for the three new v2s with diff-intent blocks.

**Modify (e2e):**
- `packages/horizons-webapp/e2e/documents-viewer.spec.ts` — extend to assert *every* visible UK demo doc renders ≥1 clause (no "Loading clauses…" stuck state).
- `packages/horizons-webapp/e2e/changes-viewer.spec.ts` (check first; create only if not present) — assert at least one MOVED event renders with the before/after path lozenge.

**Create (journal):**
- `journal/260607-corpus-no-stubs.md` — session retrospective + lawyer-review notes.

---

## Task ordering rationale

Tasks 1–5 land the library changes with tests, behind no flag, idempotent. Task 6 verifies the reseed teardown SQL covers everything. Tasks 7–10 add the new synthetic v2 fixtures one at a time (each independently commitable — easy revert if a lawyer-review pass flags one). Task 11 updates the README. Tasks 12–13 extend e2e coverage. Task 14 is the local validation pass before pushing. Task 15 is the staging-reseed runbook.

The library changes (1–5) are independent of the new fixtures (7–10) — different files, no overlap. A reviewer can land them in either order. The plan walks them in dependency order to keep the test commit chain coherent.

---

## Task 1: Extract `_stage_v1_only` helper and write its failing test

Goal: factor out the v1-staging body inside `_stage_one_pair` so the upcoming `run_seed` path can reuse it for v1-only fixtures.

**Files:**
- Modify: `packages/horizons-ingestion/src/horizons_ingestion/seed.py`
- Test: `packages/horizons-ingestion/tests/test_seed_helpers.py`

- [ ] **Step 1: Write the failing test (unit, no DB)**

This test exercises the parsing + path-emission side of v1 staging without touching the DB. The helper signature is the contract.

Append to `packages/horizons-ingestion/tests/test_seed_helpers.py`:

```python
# --- v1 staging helper -------------------------------------------------------

from horizons_ingestion.seed import compute_v1_staging_payload


def test_compute_v1_staging_payload_parses_clauses() -> None:
    """The helper returns parsed clauses with paths the inserter can write."""
    markdown = (
        "# Part 1\n"
        "\n"
        "## Section 1\n"
        "\n"
        "Alpha clause.\n"
        "\n"
        "## Section 2\n"
        "\n"
        "Beta clause.\n"
    )
    payload = compute_v1_staging_payload(markdown)
    paths = [tuple(c.path) for c in payload.clauses]
    bodies = [c.body_text.strip() for c in payload.clauses]
    assert ("Part 1", "Section 1") in paths
    assert ("Part 1", "Section 2") in paths
    assert "Alpha clause." in bodies
    assert "Beta clause." in bodies
    assert payload.content_bytes == len(markdown.encode("utf-8"))
    assert len(payload.content_sha256) == 32  # SHA-256 digest length
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/horizons-ingestion/tests/test_seed_helpers.py::test_compute_v1_staging_payload_parses_clauses -v`
Expected: FAIL with `ImportError: cannot import name 'compute_v1_staging_payload'`.

- [ ] **Step 3: Implement the helper**

In `packages/horizons-ingestion/src/horizons_ingestion/seed.py`, add **above** `_stage_one_pair` (around the existing `_walk_emitting_leaves` block):

```python
@dataclass(frozen=True)
class V1StagingPayload:
    """Pre-computed payload for staging one v1 document version + clauses."""

    clauses: list[Clause]
    content_bytes: int
    content_sha256: bytes


def compute_v1_staging_payload(markdown_text: str) -> V1StagingPayload:
    """Parse v1 markdown and return the payload an inserter needs.

    Pure / no DB. Raises whatever ``parse(...)`` raises on malformed input;
    callers that need failure tolerance must wrap.
    """
    encoded = markdown_text.encode("utf-8")
    tree = parse(markdown_text)
    return V1StagingPayload(
        clauses=_walk_emitting_leaves(tree),
        content_bytes=len(encoded),
        content_sha256=hashlib.sha256(encoded).digest(),
    )
```

Add `"V1StagingPayload"` and `"compute_v1_staging_payload"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/horizons-ingestion/tests/test_seed_helpers.py::test_compute_v1_staging_payload_parses_clauses -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-ingestion/src/horizons_ingestion/seed.py \
        packages/horizons-ingestion/tests/test_seed_helpers.py
git commit -m "$(cat <<'EOF'
refactor(seed): extract compute_v1_staging_payload helper

Factors the v1 parse + bytes/sha256 computation out of _stage_one_pair
so the next change to run_seed can reuse it for v1-only fixtures.

EOF
)"
```

---

## Task 2: Add `_insert_v1_only` DB writer with idempotency

Goal: a low-level helper that takes a SQLAlchemy Connection + a document UUID + a `V1StagingPayload` + the source path, and inserts one v1 `document_versions` row plus its `clauses`. Idempotent: if `document_versions` already has any row for the doc, return without writing.

**Files:**
- Modify: `packages/horizons-ingestion/src/horizons_ingestion/seed.py`

This task is implementation-only — its tests come in Task 3 (the integration path that exercises the full `run_seed`-with-v1 flow against a testcontainers Postgres).

- [ ] **Step 1: Implement `_insert_v1_only`**

In `packages/horizons-ingestion/src/horizons_ingestion/seed.py`, add **after** `_PARK_SCHEDULE_SQL` and **before** `_walk_emitting_leaves`:

```python
def _insert_v1_only(
    conn: Any,
    document_id: UUID,
    payload: V1StagingPayload,
    *,
    v1_path: Path,
    now: datetime,
) -> int:
    """Insert one v1 ``document_versions`` row + its ``clauses``. Idempotent.

    Returns the number of clauses inserted, or 0 if any
    ``document_versions`` row already exists for this document.
    """
    if conn.execute(_HAS_VERSIONS_SQL, {"d": document_id}).first() is not None:
        return 0

    version_id: UUID = conn.execute(
        _INSERT_VERSION_SQL,
        {
            "d": document_id,
            "lbl": "v1",
            "vno": 1,
            "vf": now,
            "vt": None,
            "pub": None,
            "eff": None,
            "bc": _V1_BLOB_CONTAINER,
            "bk": v1_path.name,
            "sha": payload.content_sha256,
            "bytes": payload.content_bytes,
        },
    ).scalar_one()

    inserted = 0
    for ord_i, node in enumerate(payload.clauses, start=1):
        conn.execute(
            _INSERT_CLAUSE_SQL,
            {
                "dv": version_id,
                "uid": _uuid.uuid4(),
                "path": "/".join(node.path),
                "body": node.body_text,
                "ord": ord_i,
            },
        )
        inserted += 1
    return inserted
```

Note: `valid_to=None` matches what `stage_synthetic_v2` writes for the *live* version. For a v1-only doc the v1 IS live; the worker will close it out at the next real v2 fetch. (Once a v2 is added, `stage_synthetic_v2` writes `valid_to=now` on its v1 row — there's no automatic reconciliation today, but the v1-only path doesn't need one.)

- [ ] **Step 2: Run the unit-test file as a sanity check**

Run: `uv run pytest packages/horizons-ingestion/tests/test_seed_helpers.py -v`
Expected: all existing tests + the Task-1 test still PASS. (The helper is dead code at this point; covered in Task 3.)

- [ ] **Step 3: Commit**

```bash
git add packages/horizons-ingestion/src/horizons_ingestion/seed.py
git commit -m "$(cat <<'EOF'
feat(seed): add _insert_v1_only helper for v1-only fixture staging

Companion to compute_v1_staging_payload; takes a SQLAlchemy connection
and writes the one document_versions row + its clauses. Idempotent at
the document level — returns 0 if any document_versions row exists.

Wired into run_seed in the next commit.

EOF
)"
```

---

## Task 3: Wire v1 staging into `run_seed` + extend `SeedResult`

Goal: `run_seed` now stages v1 clauses for every freshly-inserted document, parks the schedule at `2026-12-31`, and reports the counts. Parser failures emit a warning and skip v1 staging for that one doc only.

**Files:**
- Modify: `packages/horizons-ingestion/src/horizons_ingestion/seed.py`
- Test: `packages/horizons-ingestion/tests/test_seed_helpers.py`

- [ ] **Step 1: Extend `SeedResult` with new counters**

In `packages/horizons-ingestion/src/horizons_ingestion/seed.py`, replace the `SeedResult` dataclass:

```python
@dataclass(frozen=True)
class SeedResult:
    documents_inserted: int
    schedules_inserted: int
    documents_skipped_conflict: int
    v1_documents_staged: int
    v1_clauses_inserted: int
    v1_parse_failures: int
    v1_skipped_missing_fixture: int
```

- [ ] **Step 2: Add `samples_dir` parameter to `run_seed` and locate v1 markdown**

Update the signature of `run_seed` (in `seed.py`):

```python
def run_seed(
    dsn: str,
    curated: CuratedSet,
    fixtures: Iterable[dict[str, Any]],
    *,
    now: datetime,
    samples_dir: Path | None = None,
    warn: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> SeedResult:
```

Make `from pathlib import Path` non-`TYPE_CHECKING` (move it to the top of the file with the other runtime imports — `samples_dir` is a real argument now).

- [ ] **Step 3: Implement the v1 staging loop inside `run_seed`**

Replace the body of `run_seed` (between the `if dry_run:` block and the `return SeedResult(...)`) with the version below. The dry-run branch must also return the new counter shape:

```python
    if dry_run:
        return SeedResult(
            documents_inserted=len(seeded),
            schedules_inserted=len(seeded),
            documents_skipped_conflict=0,
            v1_documents_staged=0,
            v1_clauses_inserted=0,
            v1_parse_failures=0,
            v1_skipped_missing_fixture=0,
        )

    engine = create_engine(dsn, future=True)
    docs_inserted = 0
    schedules_inserted = 0
    docs_skipped = 0
    v1_docs_staged = 0
    v1_clauses_total = 0
    v1_parse_failures = 0
    v1_skipped_missing = 0
    try:
        with engine.begin() as conn:
            for row in seeded:
                inserted_id: Any = conn.execute(
                    _INSERT_DOCUMENT_SQL,
                    {
                        "j": row.jurisdiction,
                        "s": row.sector,
                        "lid": row.lawstronaut_document_id,
                        "t": row.title,
                    },
                ).scalar()
                if inserted_id is None:
                    docs_skipped += 1
                    document_id: Any = conn.execute(
                        _SELECT_DOCUMENT_ID_SQL,
                        {"lid": row.lawstronaut_document_id},
                    ).scalar_one()
                else:
                    docs_inserted += 1
                    document_id = inserted_id

                schedule_id: Any = conn.execute(
                    _INSERT_SCHEDULE_SQL,
                    {"d": document_id, "c": row.cadence, "n": row.next_poll_at},
                ).scalar()
                if schedule_id is not None:
                    schedules_inserted += 1

                # v1 staging — best-effort per doc. Failures here must not
                # abort the whole seed transaction; on parse failure we
                # warn + continue with no document_versions row (the
                # legacy "stub" outcome) for that one doc only.
                if samples_dir is not None:
                    iso = _fixture_iso_for(row.lawstronaut_document_id, fixtures)
                    if iso is None:
                        v1_skipped_missing += 1
                        if warn is not None:
                            warn(
                                f"v1 staging: no fixtures.json entry for id="
                                f"{row.lawstronaut_document_id!r}; skipped"
                            )
                    else:
                        v1_path = samples_dir / f"{iso}-{row.lawstronaut_document_id}-v1.md"
                        if not v1_path.exists():
                            v1_skipped_missing += 1
                            if warn is not None:
                                warn(
                                    f"v1 staging: no v1 markdown at "
                                    f"{v1_path}; skipped"
                                )
                        else:
                            try:
                                payload = compute_v1_staging_payload(
                                    v1_path.read_text(encoding="utf-8")
                                )
                            except Exception as exc:  # noqa: BLE001 — boundary
                                v1_parse_failures += 1
                                if warn is not None:
                                    warn(
                                        f"v1 staging: parser failed on "
                                        f"{v1_path}: {exc!r}; skipped"
                                    )
                            else:
                                inserted = _insert_v1_only(
                                    conn,
                                    document_id,
                                    payload,
                                    v1_path=v1_path,
                                    now=now,
                                )
                                if inserted > 0:
                                    v1_docs_staged += 1
                                    v1_clauses_total += inserted
                                    conn.execute(
                                        _PARK_SCHEDULE_SQL,
                                        {
                                            "d": document_id,
                                            "n": _STAGED_NEXT_POLL_AT,
                                        },
                                    )
    finally:
        engine.dispose()

    return SeedResult(
        documents_inserted=docs_inserted,
        schedules_inserted=schedules_inserted,
        documents_skipped_conflict=docs_skipped,
        v1_documents_staged=v1_docs_staged,
        v1_clauses_inserted=v1_clauses_total,
        v1_parse_failures=v1_parse_failures,
        v1_skipped_missing_fixture=v1_skipped_missing,
    )
```

- [ ] **Step 4: Add `_fixture_iso_for` helper**

Place above `run_seed` (after `_INSERT_SCHEDULE_SQL`):

```python
def _fixture_iso_for(
    document_id: str, fixtures: Iterable[dict[str, Any]]
) -> str | None:
    """Look up the capture ``iso`` for a document id from the fixtures inventory.

    Streams the iterable a second time. Callers pass either a list or a
    re-iterable container; iterating an already-consumed generator here
    returns ``None`` silently. The CLI passes a list, so this is fine in
    practice.
    """
    for fixture in fixtures:
        if str(fixture.get("document_id")) == document_id:
            iso_val: Any = fixture.get("iso")
            return str(iso_val).lower() if iso_val is not None else None
    return None
```

- [ ] **Step 5: Write the failing parser-failure test**

Append to `packages/horizons-ingestion/tests/test_seed_helpers.py`:

```python
def test_compute_v1_staging_payload_propagates_parser_failure() -> None:
    """compute_v1_staging_payload does not swallow parser exceptions.

    The boundary that decides ``skip vs abort`` lives in run_seed; the
    helper itself stays pure.
    """
    import pytest as _pytest

    with _pytest.raises(Exception):  # noqa: B017 — exact class is parser-internal
        compute_v1_staging_payload("\x00not-valid-markdown\x00" * 1000)
```

Run: `uv run pytest packages/horizons-ingestion/tests/test_seed_helpers.py::test_compute_v1_staging_payload_propagates_parser_failure -v`
Expected: PASS (the parser tolerates a lot of input; if this passes immediately because the parser succeeds on the input, replace the input with something that triggers a known parser failure — e.g. by monkeypatching `parse` to raise. Use this fallback if needed):

```python
def test_compute_v1_staging_payload_propagates_parser_failure(monkeypatch) -> None:
    import horizons_ingestion.seed as seed_mod

    def _raise(_: str) -> None:
        raise RuntimeError("synthetic parser failure")

    monkeypatch.setattr(seed_mod, "parse", _raise)
    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="synthetic parser failure"):
        seed_mod.compute_v1_staging_payload("anything")
```

- [ ] **Step 6: Run the unit tests**

Run: `uv run pytest packages/horizons-ingestion/tests/test_seed_helpers.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the full Python test suite (no integration)**

Run: `uv run pytest -m "not integration"`
Expected: all PASS — confirm no caller of `run_seed` outside the test file has broken on the signature change. (The CLI shim and `reseed_corpus.py` are exercised in Task 5; if anything else calls `run_seed`, grep first: `git grep -n 'run_seed(' -- '*.py' '!**/__pycache__'`.)

- [ ] **Step 8: Commit**

```bash
git add packages/horizons-ingestion/src/horizons_ingestion/seed.py \
        packages/horizons-ingestion/tests/test_seed_helpers.py
git commit -m "$(cat <<'EOF'
feat(seed): run_seed stages v1 clauses + parks the poll schedule

When ``samples_dir`` is supplied, for every freshly-inserted document
run_seed now (a) parses ``<samples_dir>/<iso>-<id>-v1.md``, (b) writes
one v1 ``document_versions`` row + its ``clauses``, and (c) parks
``document_poll_schedule.next_poll_at`` at 2026-12-31 so the worker
won't claim and overwrite the staged content during the demo window.

Per-doc failures (no fixtures entry, no v1 markdown on disk, parser
exception) emit a warning and skip v1 staging for that one document
only — the legacy "stub" outcome for that one doc, no transaction
rollback. Counters distinguish parse failures from missing fixtures.

EOF
)"
```

---

## Task 4: Verify the new seed library path against a real Postgres (integration)

Goal: prove the v1-staging path works against the real schema + triggers (document_versions append-only trigger, clauses FK, schedule park). This is the integration test that gates the design.

**Files:**
- Test: `packages/horizons-ingestion/tests/test_seed_integration.py` (NEW)

The existing test file is unit-level; integration tests for `run_seed` live in their own module so the testcontainers dependency only fires under `-m integration`.

- [ ] **Step 1: Create the integration test file**

```python
"""Integration tests for the WU8.5+ v1-staging path in run_seed.

Spins up a Postgres 18 testcontainer, applies migrations, runs the
seed end-to-end against a real fixture set sourced from
``data/samples/``, and verifies clauses + parked schedule rows landed.

Run with ``uv run pytest -m integration packages/horizons-ingestion/tests/test_seed_integration.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from horizons_ingestion.seed import (
    SeedResult,
    parse_curated_set,
    run_seed,
)
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
FIXTURES_JSON = SAMPLES_DIR / "fixtures.json"

# A tiny in-memory curated set referencing two real fixtures on disk.
# We pick GB 28914588 (large, well-structured tribunal judgment) and
# AU 2145602 (short, common-law) so the integration test runs fast.
_CURATED_YAML = """
jurisdictions: [GB, AU]
sectors: [BANKING]
default_cadence_hours: 24
documents:
  - id: "28914588"
    jurisdiction: UK
    sector: BANKING
  - id: "2145602"
    jurisdiction: UK
    sector: BANKING
"""


def _load_fixtures() -> list[dict]:
    return json.loads(FIXTURES_JSON.read_text(encoding="utf-8"))["fixtures"]


def _staged_pg_dsn(postgres_url: str) -> str:
    """Force the sync psycopg driver for SQLAlchemy."""
    return postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture
def pg_dsn(postgres_container_url: str) -> str:
    """Resolved against the project-wide testcontainers fixture."""
    return _staged_pg_dsn(postgres_container_url)


def test_run_seed_stages_v1_for_every_curated_doc(pg_dsn: str) -> None:
    curated = parse_curated_set(_CURATED_YAML)
    fixtures = _load_fixtures()
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    result = run_seed(
        dsn=pg_dsn,
        curated=curated,
        fixtures=fixtures,
        now=now,
        samples_dir=SAMPLES_DIR,
    )

    assert isinstance(result, SeedResult)
    assert result.documents_inserted == 2
    assert result.schedules_inserted == 2
    assert result.v1_documents_staged == 2
    assert result.v1_parse_failures == 0
    assert result.v1_skipped_missing_fixture == 0
    assert result.v1_clauses_inserted > 0

    engine = create_engine(pg_dsn, future=True)
    with engine.connect() as conn:
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
    engine.dispose()

    assert len(rows) == 2
    by_id = {row.lawstronaut_document_id: row for row in rows}
    for doc_id in ("28914588", "2145602"):
        assert by_id[doc_id].version_label == "v1"
        assert by_id[doc_id].n_clauses > 0
        # Parked far past the demo window so the worker won't claim it.
        assert by_id[doc_id].next_poll_at == datetime(
            2026, 12, 31, 0, 0, tzinfo=UTC
        )


def test_run_seed_v1_idempotent(pg_dsn: str) -> None:
    curated = parse_curated_set(_CURATED_YAML)
    fixtures = _load_fixtures()
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    first = run_seed(
        dsn=pg_dsn,
        curated=curated,
        fixtures=fixtures,
        now=now,
        samples_dir=SAMPLES_DIR,
    )
    second = run_seed(
        dsn=pg_dsn,
        curated=curated,
        fixtures=fixtures,
        now=now,
        samples_dir=SAMPLES_DIR,
    )

    assert first.v1_documents_staged == 2
    assert second.documents_inserted == 0
    assert second.documents_skipped_conflict == 2
    assert second.v1_documents_staged == 0  # both already had a v1


def test_run_seed_parser_failure_does_not_abort(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the parser raises on one doc, the rest of the seed still completes."""
    import horizons_ingestion.seed as seed_mod

    original = seed_mod.parse
    call_count = {"n": 0}

    def _selective_raise(text: str):  # pragma: no cover — narrow patch
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic parser failure on first doc")
        return original(text)

    monkeypatch.setattr(seed_mod, "parse", _selective_raise)

    curated = parse_curated_set(_CURATED_YAML)
    fixtures = _load_fixtures()
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    warnings: list[str] = []

    result = run_seed(
        dsn=pg_dsn,
        curated=curated,
        fixtures=fixtures,
        now=now,
        samples_dir=SAMPLES_DIR,
        warn=warnings.append,
    )

    # Both documents land in `documents`; only one gets a v1 staged.
    assert result.documents_inserted == 2
    assert result.v1_documents_staged == 1
    assert result.v1_parse_failures == 1
    assert any("parser failed" in w for w in warnings)
```

Note: the `postgres_container_url` fixture is shared across the workspace; verify its name with `git grep -n 'postgres_container_url' packages/`. If the fixture name differs, adjust the fixture import. If no such fixture exists yet (the workspace `conftest.py` may declare it inline), borrow the pattern from any existing test marked `integration`, e.g. `packages/horizons-api/tests/test_documents_endpoints.py`.

- [ ] **Step 2: Verify the fixture name is right**

Run: `git grep -n 'pytest.fixture' packages/horizons-ingestion/tests/ packages/horizons-api/tests/ | grep -i 'postgres'`
Expected: surfaces the canonical fixture name and module. Adjust the test file's fixture parameter if needed.

- [ ] **Step 3: Run the integration tests (requires Docker)**

Run: `uv run pytest -m integration packages/horizons-ingestion/tests/test_seed_integration.py -v`
Expected: 3 PASS. If Docker isn't running, the marker auto-skips per the project's pytest config.

- [ ] **Step 4: Commit**

```bash
git add packages/horizons-ingestion/tests/test_seed_integration.py
git commit -m "$(cat <<'EOF'
test(seed): integration coverage for run_seed v1-staging

Three integration cases against testcontainers Postgres:
- v1 documents + clauses land + schedule is parked
- second pass is a no-op (idempotent)
- parser failure on one doc warns and continues, the other staged

EOF
)"
```

---

## Task 5: Update the CLI to pass `samples_dir` and print v1 counts

Goal: `scripts/seed_curated_set.py` now invokes `run_seed` with `samples_dir=…` and prints the new counters; `scripts/reseed_corpus.py` continues to invoke the CLI script unchanged.

**Files:**
- Modify: `scripts/seed_curated_set.py`

- [ ] **Step 1: Pass `samples_dir` into the call**

Edit `scripts/seed_curated_set.py`. Inside `main(...)`, replace the `run_seed(...)` invocation with:

```python
    result = run_seed(
        dsn=dsn or "",
        curated=curated,
        fixtures=fixtures,
        now=now,
        samples_dir=args.samples_dir,
        warn=_print_warning,
        dry_run=args.dry_run,
    )
```

Then, immediately after the existing `print(f"{label}: ...documents skipped...")` block, append:

```python
    v1_label = "would stage" if args.dry_run else "staged"
    print(f"{v1_label}: {result.v1_documents_staged} v1 document version(s)")
    print(f"{v1_label}: {result.v1_clauses_inserted} v1 clause row(s)")
    if result.v1_parse_failures:
        print(f"v1 staging parse failures: {result.v1_parse_failures}")
    if result.v1_skipped_missing_fixture:
        print(
            f"v1 staging skipped (no markdown/iso): "
            f"{result.v1_skipped_missing_fixture}"
        )
```

- [ ] **Step 2: Verify `--dry-run` does not write to the DB**

Run from the repo root:

```bash
HORIZONS_DB_URL='' uv run scripts/seed_curated_set.py --dry-run | head -40
```

Expected: the script prints `would insert: 31 document(s)` (or similar) plus the new `would stage: 0 v1 …` lines (dry-run path doesn't actually parse anything; that's intentional and matches the existing `stage_synthetic_v2` dry-run contract). Exit 0.

- [ ] **Step 3: Boot a local Postgres and seed against it**

Follow `docs/runbooks/local-dev.md` to start the local Postgres on port 5433. Then:

```bash
HORIZONS_DB_URL='postgresql+psycopg://horizons:horizons@localhost:5433/horizons' \
  uv run alembic upgrade head

HORIZONS_DB_URL='postgresql+psycopg://horizons:horizons@localhost:5433/horizons' \
  uv run scripts/seed_curated_set.py
```

Expected output (counts approximate):

```
inserted: 31 document(s)
inserted: 31 schedule row(s)
staged: ~26 v1 document version(s)
staged: <thousands> v1 clause row(s)
```

The exact number of v1 documents staged depends on parser tolerance per fixture; a few non-English fixtures may produce warnings (acceptable per spec — those land as stubs).

- [ ] **Step 4: Confirm parsed clauses landed for several reps**

```bash
psql 'postgresql://horizons:horizons@localhost:5433/horizons' -c "
  SELECT d.lawstronaut_document_id, dv.version_label, count(c.id) AS n
  FROM documents d
  JOIN document_versions dv ON dv.document_id = d.id
  JOIN clauses c ON c.document_version_id = dv.id
  GROUP BY 1, 2
  ORDER BY n DESC LIMIT 10;
"
```

Expected: large fixtures (CY, IT, MC, ES, IE-27732019) show many clauses; smaller fixtures show fewer. No row should have `n = 0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/seed_curated_set.py
git commit -m "$(cat <<'EOF'
feat(seed-cli): seed_curated_set.py passes samples_dir + prints v1 counts

EOF
)"
```

---

## Task 6: Verify the reseed wipe order covers v1 staging

Goal: ensure `scripts/reseed_corpus.py` deletes `change_events` → `clauses` → `document_versions` → `documents` (FK-correct), so the Job can rewipe the corpus the new seed produces.

**Files:**
- Read: `scripts/reseed_corpus.py`
- (Maybe modify if a DELETE is missing.)

- [ ] **Step 1: Inspect the current wipe statements**

Run: `grep -n "DELETE FROM" scripts/reseed_corpus.py`
Expected to find: `change_events`, `clauses`, `document_versions`, `document_poll_schedule`, `documents`.

If any of those four is missing — likely `document_versions` is — add it in FK-safe order between `clauses` and `document_poll_schedule`. Use the existing `_WIPE_STATEMENTS` tuple.

- [ ] **Step 2: If a DELETE is missing, add it**

If `document_versions` is absent, edit `scripts/reseed_corpus.py`:

```python
_WIPE_STATEMENTS = (
    "DELETE FROM change_events",
    "DELETE FROM clauses",
    "DELETE FROM document_versions",
    "DELETE FROM document_poll_schedule",
    "DELETE FROM documents",
)
```

- [ ] **Step 3: If changes were made, commit them; otherwise skip**

```bash
git add scripts/reseed_corpus.py
git commit -m "$(cat <<'EOF'
fix(reseed): wipe document_versions before documents

WU8.5+ seeding now writes a v1 document_versions row for every
curated doc. Without an explicit DELETE the FK from clauses /
change_events to document_versions blocks the wipe.

EOF
)"
```

---

## Task 7: New synthetic v2 — `ie-27732019-v2.md` (MOVED + MODIFIED)

Goal: add a v2 of the Irish Statute Book Act with one MOVED edit (sub-section relocated to a different parent, clause text unchanged) and one MODIFIED edit (a single numeric threshold amended in Oireachtas-amendment style).

**Files:**
- Read: `data/samples/ie-27732019-v1.md`
- Create: `data/samples/synthetic_v2/ie-27732019-v2.md`

- [ ] **Step 1: Read the v1 to pick a safe MOVED candidate**

Run: `wc -l data/samples/ie-27732019-v1.md && head -200 data/samples/ie-27732019-v1.md`

Identify a leaf sub-section `(a)(i)`-style clause whose body could plausibly be reordered or relocated under a sibling section without breaking statutory sense. Pick a clause whose body has no internal cross-references that would be invalidated by the move.

Also pick a single numeric threshold (a fee, a percentage, a date, a section count) that can plausibly amend in line with real Oireachtas style. Conservative: change a small monetary threshold by a small percentage, or shift a notice-period count by one.

Record the chosen edits in a comment block at the top of the v2 (so the lawyer reviewer sees the intent without diffing):

```
<!-- Edits applied vs v1:
1. MOVED — sub-section (a)(i) moved from Section X to Section Y. Body text unchanged.
2. MODIFIED — Section X, sub-section (b): "€500" → "€750" (or similar).
-->
```

- [ ] **Step 2: Create the v2 file**

`cp data/samples/ie-27732019-v1.md data/samples/synthetic_v2/ie-27732019-v2.md` then apply the two edits with `Edit` (the file is large; use the `Edit` tool with precise `old_string`/`new_string` blocks).

Critical constraints:
- **Do not reformat.** No whitespace changes outside the edited region.
- **Do not touch headings other than the MOVED clause's parent reference.**
- **The MOVED clause body text must be byte-identical** before and after (modulo the parent-anchor change the parser will encode in the path). Otherwise the aligner emits MODIFIED, not MOVED.
- **The MODIFIED edit changes exactly one token** (the numeric value). Surrounding sentence stays as written.

- [ ] **Step 3: Dry-run the aligner against the new pair**

```bash
HORIZONS_DB_URL='' uv run scripts/seed_curated_set.py --dry-run --stage-synthetic-v2 2>&1 | grep -E "ie-27732019|MOVED|MODIFIED|warning"
```

Expected: the dry-run reports staging would happen for `ie-27732019` with at least 2 change events. If the count is wrong, inspect the diff manually and re-edit.

For a closer look at the events the aligner produces, run a one-off Python snippet from the repo root:

```bash
uv run python - <<'PY'
from pathlib import Path
from horizons_core.core.alignment import align, parse

v1 = parse(Path("data/samples/ie-27732019-v1.md").read_text())
v2 = parse(Path("data/samples/synthetic_v2/ie-27732019-v2.md").read_text())
events = align(v1, v2)
for e in events:
    print(f"{e.change_type:9s} before={e.before_path} after={e.after_path}")
PY
```

Expected output: exactly 2 events — one `MOVED`, one `MODIFIED`. If extra events appear (typically spurious `MODIFIED` from re-flowed whitespace), tighten the edit until the count is clean.

- [ ] **Step 4: Self-review for lawyer-defensibility**

Open the v1 and v2 in a side-by-side diff:

```bash
diff -u data/samples/ie-27732019-v1.md data/samples/synthetic_v2/ie-27732019-v2.md | head -200
```

Confirm:
- (a) the MOVED clause text is byte-identical;
- (b) the MODIFIED edit is exactly one token in size;
- (c) no inadvertent edits leaked outside the intended deltas;
- (d) the surrounding statutory voice is unchanged.

If any of these fail, revise.

- [ ] **Step 5: Commit**

```bash
git add data/samples/synthetic_v2/ie-27732019-v2.md
git commit -m "$(cat <<'EOF'
feat(samples): IE 27732019 synthetic v2 (MOVED + MODIFIED)

UK demo-visible Irish Statute Book Act v2 with one sub-section
relocated (parser path changes, clause text unchanged → MOVED) and
one numeric threshold amended in Oireachtas-amendment style →
MODIFIED. The MOVED edit carries the fourth change kind across the
whole demo corpus.

EOF
)"
```

---

## Task 8: New synthetic v2 — `au-2145602-v2.md` (ADDED + REMOVED)

Goal: add a v2 of the Australian doc with one new short clause appended at a plausible location and one clearly deprecated paragraph removed.

**Files:**
- Read: `data/samples/au-2145602-v1.md`
- Create: `data/samples/synthetic_v2/au-2145602-v2.md`

- [ ] **Step 1: Read the v1**

```bash
cat data/samples/au-2145602-v1.md
```

The doc is small (5.1k), so this is a single-screen read. Identify (a) a section under which a new clause could plausibly land (continuation paragraph, supplementary note), and (b) an existing paragraph that reads as time-bounded, redundant, or superseded — something a real amendment would remove without controversy.

- [ ] **Step 2: Create the v2 file**

```bash
cp data/samples/au-2145602-v1.md data/samples/synthetic_v2/au-2145602-v2.md
```

Apply two edits via the `Edit` tool:
- Remove the chosen paragraph (REMOVED).
- Insert one new short clause at the end of an existing section (ADDED). The new clause must read in the same legal/common-law voice as the surrounding text. Match sentence length, register, paragraph indent style.

Lawyer-defensibility checks (same rules as Task 7): single-unit edits, no reformatting beyond the inserted/deleted region, tone-consistent voice.

Add the same `<!-- Edits applied vs v1: ... -->` block at top.

- [ ] **Step 3: Dry-run the aligner**

```bash
uv run python - <<'PY'
from pathlib import Path
from horizons_core.core.alignment import align, parse

v1 = parse(Path("data/samples/au-2145602-v1.md").read_text())
v2 = parse(Path("data/samples/synthetic_v2/au-2145602-v2.md").read_text())
events = align(v1, v2)
for e in events:
    print(f"{e.change_type:9s} before={e.before_path} after={e.after_path}")
PY
```

Expected: exactly 2 events — one `ADDED`, one `REMOVED`.

- [ ] **Step 4: Self-review**

```bash
diff -u data/samples/au-2145602-v1.md data/samples/synthetic_v2/au-2145602-v2.md
```

Confirm the four lawyer-defensibility rules from Task 7 Step 4.

- [ ] **Step 5: Commit**

```bash
git add data/samples/synthetic_v2/au-2145602-v2.md
git commit -m "$(cat <<'EOF'
feat(samples): AU 2145602 synthetic v2 (ADDED + REMOVED)

UK demo-visible Australian doc v2 with one new clause appended and
one deprecated paragraph removed. Common-law voice preserved across
both edits.

EOF
)"
```

---

## Task 9: New synthetic v2 — `eu-31366184-v2.md` (MODIFIED + REMOVED)

Goal: add a v2 of the BEREC EU press item with one date reference amended and one time-bounded paragraph removed.

**Files:**
- Read: `data/samples/eu-31366184-v1.md`
- Create: `data/samples/synthetic_v2/eu-31366184-v2.md`

- [ ] **Step 1: Read the v1**

```bash
cat data/samples/eu-31366184-v1.md
```

The doc is small (~2.9k). Identify a date reference that could plausibly be corrected (e.g. publication date, consultation deadline) and a time-bounded paragraph (e.g. interim arrangement, transitional measure).

- [ ] **Step 2: Create the v2 file**

```bash
cp data/samples/eu-31366184-v1.md data/samples/synthetic_v2/eu-31366184-v2.md
```

Apply two edits:
- Change one date token (MODIFIED).
- Remove one time-bounded paragraph (REMOVED).

Add the `<!-- Edits applied vs v1: ... -->` comment block at top.

EU-institutional voice: terse, regulator-press style. No editorial flourishes; no new acronyms.

- [ ] **Step 3: Dry-run the aligner**

```bash
uv run python - <<'PY'
from pathlib import Path
from horizons_core.core.alignment import align, parse

v1 = parse(Path("data/samples/eu-31366184-v1.md").read_text())
v2 = parse(Path("data/samples/synthetic_v2/eu-31366184-v2.md").read_text())
events = align(v1, v2)
for e in events:
    print(f"{e.change_type:9s} before={e.before_path} after={e.after_path}")
PY
```

Expected: 1 `MODIFIED`, 1 `REMOVED`.

- [ ] **Step 4: Self-review**

```bash
diff -u data/samples/eu-31366184-v1.md data/samples/synthetic_v2/eu-31366184-v2.md
```

Confirm the four lawyer-defensibility rules.

- [ ] **Step 5: Commit**

```bash
git add data/samples/synthetic_v2/eu-31366184-v2.md
git commit -m "$(cat <<'EOF'
feat(samples): EU 31366184 synthetic v2 (MODIFIED + REMOVED)

EU demo-visible BEREC press-item v2 with one date amended and one
time-bounded paragraph removed.

EOF
)"
```

---

## Task 10: Update `data/samples/synthetic_v2/README.md`

Goal: the inventory table + diff-intent blocks reflect the three new v2s; the gap-about-US note is still accurate.

**Files:**
- Modify: `data/samples/synthetic_v2/README.md`

- [ ] **Step 1: Update the inventory table**

Add three rows to the table (currently 5 rows). Final shape:

```markdown
| slug | fixture iso | seeded as (jurisdiction, sector) | source | diff intent |
|------|-------------|----------------------------------|--------|-------------|
| `ie-8064194` | IE | (IE, corporate-governance) | … | … (existing) |
| `gb-28914588` | GB | **(UK, BANKING)** — demo relabel | … | … (existing) |
| `fr-31702142` | FR | **(EU, BANKING)** — demo relabel | … | … (existing) |
| `de-20951816` | DE | (DE, employment) | … | … (existing) |
| `it-26863` | IT | (IT, BANKING) | … | … (existing) |
| `ie-27732019` | IE | **(UK, BANKING)** — demo relabel | `data/samples/ie-27732019-v1.md` | MOVED + MODIFIED |
| `au-2145602` | AU | **(UK, BANKING)** — demo relabel | `data/samples/au-2145602-v1.md` | ADDED + REMOVED |
| `eu-31366184` | EU | (EU, BANKING) | `data/samples/eu-31366184-v1.md` | MODIFIED + REMOVED |
```

- [ ] **Step 2: Append diff-intent blocks**

Below the existing five diff-intent sections, add three new sections (`### ie-27732019 …`, `### au-2145602 …`, `### eu-31366184 …`) with one-line bullets per edit, matching the existing pattern.

- [ ] **Step 3: Update the lead paragraph**

The header currently says "Five demo fixtures…". Change to "Eight demo fixtures…" (or count after the actual files in the directory). Update the line "Each pair (`v1`, `v2`) carries small, realistic clause-level edits — one **add**, one **modify**, one **remove**" to reflect that the new pairs have different mixes (some carry MOVED; not every pair carries all three).

- [ ] **Step 4: Commit**

```bash
git add data/samples/synthetic_v2/README.md
git commit -m "$(cat <<'EOF'
docs(samples): document three new synthetic v2 pairs

IE 27732019 (MOVED + MODIFIED), AU 2145602 (ADDED + REMOVED),
EU 31366184 (MODIFIED + REMOVED). Coverage now spans all four
change kinds across the UK + EU demo subscriptions.

EOF
)"
```

---

## Task 11: Update curated_set.yaml comments to reflect new diff coverage

Goal: the comment cluster in `data/curated_set.yaml` calls out which docs are demo-visible diff beats; bring it in sync.

**Files:**
- Modify: `data/curated_set.yaml`

- [ ] **Step 1: Update the UK relabels comment block**

Above the `- id: "27732019"` entry, the comment currently reads "IE — Irish Statute Book Act; the dense PART/Section/(a)/(i) fixture. Relabelled to UK so it shows up in the UK list as a long, well-structured doc to demo the clause-overlay toggle on." Append: "Synthetic v2 staged under `data/samples/synthetic_v2/ie-27732019-v2.md` — carries the demo's only MOVED change event."

Above `- id: "2145602"`, append: "Synthetic v2 staged with ADDED + REMOVED edits."

- [ ] **Step 2: Update the EU relabels comment block**

Above `- id: "31366184"`, append: "Synthetic v2 staged with MODIFIED + REMOVED edits."

- [ ] **Step 3: Commit**

```bash
git add data/curated_set.yaml
git commit -m "$(cat <<'EOF'
docs(curated): annotate which UK/EU docs now have synthetic v2 pairs

EOF
)"
```

---

## Task 12: Extend e2e `documents-viewer.spec.ts` to assert no-stub state

Goal: the Playwright spec already covers the UK demo's flow on the `UK_DOC_TITLE` fixture. Add a second assertion that walks every visible row in the demo's documents list and confirms the detail page renders at least one clause.

**Files:**
- Modify: `packages/horizons-webapp/e2e/documents-viewer.spec.ts`

- [ ] **Step 1: Append a new test case**

Append at the end of the file:

```typescript
test('UK demo: every visible document renders parsed clauses', async ({ page }) => {
  await page.goto('/login')
  await page.getByTestId('email-input').fill(UK_EMAIL)
  await page.getByTestId('password-input').fill(UK_PASSWORD)
  await page.getByTestId('login-submit').click()
  await page.waitForURL('**/')

  await page.goto('/documents')
  const rows = page.getByTestId('document-row')
  const count = await rows.count()
  expect(count).toBeGreaterThanOrEqual(1)

  for (let i = 0; i < count; i++) {
    const title = await rows.nth(i).textContent()
    await rows.nth(i).click()
    await page.waitForURL('**/documents/*')

    // Reader mode renders body text; toggle structure on to count clauses.
    await page.getByTestId('toggle-structure').click()
    const cards = page.getByTestId('clause-card')
    await expect(
      cards.first(),
      `expected at least one clause card for ${title}`,
    ).toBeVisible({ timeout: 10_000 })

    await page.goBack()
    await page.waitForURL('**/documents')
  }
})
```

This case is intentionally strict: any document whose detail page sits on "Loading clauses…" with no clause cards fails the assertion. That's the no-stubs contract.

- [ ] **Step 2: Boot the local stack and run the e2e spec**

Follow `packages/horizons-webapp/e2e/README.md` to boot the stack (Postgres + alembic + `seed_e2e.py` + uvicorn + `npm run build` + `npx vite preview`). The e2e seed file (`packages/horizons-api/scripts/seed_e2e.py`) already inserts a few clauses per doc per WU8.5; no change needed.

Then:

```bash
cd packages/horizons-webapp && npx playwright test e2e/documents-viewer.spec.ts
```

Expected: both test cases PASS.

- [ ] **Step 3: Commit**

```bash
git add packages/horizons-webapp/e2e/documents-viewer.spec.ts
git commit -m "$(cat <<'EOF'
test(e2e): assert every visible document renders parsed clauses

Locks in the no-stubs contract: any document whose detail page sits
on "Loading clauses..." with no clause cards fails the assertion.

EOF
)"
```

---

## Task 13: Verify the MOVED change kind renders in the Changes view

Goal: confirm the IE 27732019 MOVED event renders as a `before → after` path lozenge in the Changes view, end-to-end.

**Files:**
- Inspect: `packages/horizons-webapp/e2e/` (find the changes spec)
- Maybe modify or maybe create: a Playwright spec covering MOVED.

- [ ] **Step 1: Look for an existing changes-viewer spec**

Run: `ls packages/horizons-webapp/e2e/`. Expect to see `documents-viewer.spec.ts`, `login-and-scope.spec.ts`, and possibly a changes-related file.

If a changes spec exists, append a MOVED assertion to it. If not, create a new spec file.

- [ ] **Step 2: Spec the MOVED assertion**

Append (or place in a new file `e2e/changes-moved.spec.ts`):

```typescript
import { expect, test } from '@playwright/test'

const UK_EMAIL = 'uk-client@e2e.example.com'
const UK_PASSWORD = 'e2e-test-pass-uk'

test('UK demo: changes view renders a MOVED event with before → after lozenge', async ({ page }) => {
  await page.goto('/login')
  await page.getByTestId('email-input').fill(UK_EMAIL)
  await page.getByTestId('password-input').fill(UK_PASSWORD)
  await page.getByTestId('login-submit').click()
  await page.waitForURL('**/')

  await page.goto('/changes')

  // At least one MOVED pill should be present in the UK list once the
  // IE 27732019 synthetic v2 is staged via run_seed + stage_synthetic_v2.
  const movedPills = page.locator('[data-change-type="MOVED"]')
  await expect(
    movedPills.first(),
    'expected at least one MOVED change event in the UK demo changes list',
  ).toBeVisible({ timeout: 10_000 })

  // Click the first one and confirm the detail view shows both paths.
  await movedPills.first().click()
  await page.waitForURL('**/changes/*')
  // The ChangeTypePill + path lozenge wires use data-change-type on
  // the pill and a separate path lozenge element; sanity-check both
  // sides of the move are visible in body text.
  await expect(page.locator('[data-change-type="MOVED"]')).toBeVisible()
})
```

Note: this assertion depends on the e2e seed including an IE 27732019 entry with the staged MOVED event. The current `seed_e2e.py` may not do that — if the test fails because the corpus doesn't contain the IE v2 in e2e mode, extend `seed_e2e.py` to call `stage_synthetic_v2` on the `ie-27732019` pair (mirroring how it already inserts a few clauses). The simpler alternative: skip Task 13 e2e and rely on the unit + integration tests for MOVED, since the integration test in Task 4 + the existing `ChangeTypePill.spec.ts` already cover the rendering path.

**Decision rule:** if the e2e seed doesn't yet stage synthetic v2 pairs, do NOT bend it for this — log the gap in the journal and rely on `pytest packages/horizons-ingestion/tests/test_seed_integration.py` plus existing Vitest unit tests for MOVED rendering. The e2e is nice-to-have, not contract.

- [ ] **Step 3: Run the spec (if applicable)**

```bash
cd packages/horizons-webapp && npx playwright test e2e/changes-moved.spec.ts
```

Expected: PASS if the e2e seed stages an IE v2, otherwise expected to FAIL — in which case revert the new spec file and leave a journal note.

- [ ] **Step 4: Commit (or revert)**

```bash
git add packages/horizons-webapp/e2e/changes-moved.spec.ts
git commit -m "$(cat <<'EOF'
test(e2e): MOVED event renders in the changes view

EOF
)"
```

If reverted: `git rm packages/horizons-webapp/e2e/changes-moved.spec.ts` and skip the commit.

---

## Task 14: Local full-suite verification before push

Goal: the entire local quality gate is green before pushing main.

**Files:** none modified — all are read.

- [ ] **Step 1: Run the Python suite (excluding integration)**

Run: `uv run pytest -m "not integration"`
Expected: all PASS.

- [ ] **Step 2: Run the Python integration suite (if Docker is up)**

Run: `uv run pytest -m integration`
Expected: all PASS, including the new `test_seed_integration.py` cases.

- [ ] **Step 3: Run ruff + pyright**

Run: `uv run ruff check . && uv run ruff format . && uv run pyright`
Expected: clean.

- [ ] **Step 4: Run the webapp checks**

Run: `cd packages/horizons-webapp && npm run lint:check && npm run build && npm run test:unit -- --run`
Expected: clean. `lint:check` matches what CI runs.

- [ ] **Step 5: Run pre-commit**

Run: `uv run pre-commit run --all-files`
Expected: clean. (Per memory `feedback_run_precommit_before_push`: this MUST be run before pushing to main.)

If any of these fail, fix the underlying issue and re-run from Step 1.

---

## Task 15: Stage deploy + manual demo-user walkthrough

Goal: the staging corpus reflects the new v1 + v2 set; the demo users see realistic lists with parsed clauses and visible diffs.

**Files:** none modified — all are operator steps.

- [ ] **Step 1: Push the branch + fast-forward main**

Per the CLAUDE.md merge cadence:

```bash
git push -u origin <feature>
# From the main checkout:
git -C /Users/john/projects/syncthing/agent-lxc/horizons merge --ff-only <feature>
git -C /Users/john/projects/syncthing/agent-lxc/horizons push origin main
git push origin --delete <feature>
```

- [ ] **Step 2: Wait for the build-and-push.yml workflow to publish the new worker image**

Watch: `gh run watch --exit-status` from the repo root, or check `gh run list -w build-and-push.yml --limit 3` until the latest run is `completed success`.

- [ ] **Step 3: Bump the reseed Job's image to the latest worker SHA**

Per the open punch-list item — Bicep-managed Job images don't refresh on image-only pushes. Grab the SHA from the latest build run:

```bash
LATEST_WORKER_SHA=$(gh run list -w build-and-push.yml --limit 1 --json headSha -q '.[0].headSha' | cut -c1-12)
az containerapp job update \
  --name horizons-dev-reseed-corpus \
  -g horizons-nonprod \
  --image "ghcr.io/johnmathews/horizons-worker:sha-${LATEST_WORKER_SHA}"
```

- [ ] **Step 4: Dispatch the reseed**

```bash
scripts/reseed_aca.sh --yes
```

Type the confirmation token when prompted. Expected output (paraphrasing the run log):

```
inserted: 31 document(s) / 31 schedule row(s)
staged:   ~26 v1 document version(s) / <thousands> v1 clause row(s)
staged:    8 synthetic v2 document(s) / <thousands> clause row(s) / <dozens> change_event row(s)
```

- [ ] **Step 5: Manual walkthrough as the UK and EU demo users**

Open the deployed SPA, log in as `demo-uk@demo.example.com`, navigate to `/documents`. Confirm:
- ≥10 documents in the list.
- Every document opens to a detail page that renders parsed clauses (no "Loading clauses…" stuck state).
- The clause-structure toggle reveals anchor chips for every doc.
- The `/changes` view shows ≥1 entry from each of GB (ADDED+REMOVED+MODIFIED) and IE (MOVED+MODIFIED) and AU (ADDED+REMOVED).
- A MOVED event renders the `before → after` path lozenge correctly.

Repeat as `demo-eu@demo.example.com`. Confirm ≥4 of the docs (DE, FR, IT, EU-BEREC) show diffs.

If any of these fail, capture in the journal entry and decide whether to roll back, hotfix, or proceed.

- [ ] **Step 6: Write the journal entry**

Create `journal/260607-corpus-no-stubs.md` with:

- What landed (v1 staging in `run_seed`; 3 new synthetic v2 pairs).
- Lawyer-review pass: what I reviewed, with diffs attached or summarised. Defer to John for sign-off if he wants a separate review.
- Any v1 parser failures observed in the staging reseed (the docs that landed as stubs in the new flow — likely a small set of non-English fixtures). Add to a post-demo follow-up list.
- Any deviations from this plan and why.

- [ ] **Step 7: Commit the journal**

```bash
git add journal/260607-corpus-no-stubs.md
git commit -m "$(cat <<'EOF'
docs(journal): WU8.6 — corpus no-stubs + diff expansion landed

EOF
)"
git push origin main
```

---

## Self-review

**Spec coverage:**

- ✅ Stage v1 from on-disk fixtures (spec Approach A) — Tasks 1, 2, 3, 4, 5.
- ✅ Park `next_poll_at = 2026-12-31` for v1-staged docs — Task 3 Step 3.
- ✅ Per-doc parser-failure tolerance — Task 3 Step 3 + Task 4 Step 1.
- ✅ Reseed wipe order covers `document_versions` — Task 6.
- ✅ Three new English-language synthetic v2 pairs (IE / AU / EU-BEREC) — Tasks 7, 8, 9.
- ✅ MOVED carried by IE only; existing v2s not re-opened — Task 7 (IE has MOVED; GB / DE / FR / IT untouched).
- ✅ `synthetic_v2/README.md` + curated_set comments updated — Tasks 10, 11.
- ✅ E2E asserts no-stub state — Task 12.
- ✅ MOVED rendering in the Changes view verified (with a documented escape hatch if e2e seed doesn't yet stage v2) — Task 13.
- ✅ Local quality gate + staging deploy + walkthrough + journal — Tasks 14, 15.

**Placeholder scan:** No TBDs, TODOs, or vague directives. Every code-changing step shows the code or precise editing rules.

**Type consistency:** `SeedResult` gains four new fields in Task 3 Step 1, and the same field names are used consistently in Tasks 3 Step 3 (returns), 5 Step 1 (prints), and 4 Step 1 (asserts). `V1StagingPayload` + `compute_v1_staging_payload` + `_insert_v1_only` are referenced with the same signatures everywhere.

**Scope check:** One implementation plan, one PR-class change. The new fixtures (Tasks 7–9) could each ship independently if any fails lawyer review — they're committed separately for that reason.
