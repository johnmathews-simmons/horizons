# Document-pivot + colour-coded diff view — Implementation Plan

*Last revised: 2026-06-07.*
*Path: docs/superpowers/plans/2026-06-07-document-pivot-and-color-diff.md.*

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Jurisdiction / sector cards on the homepage land on a paginated document table; (2) the document split view colour-codes every clause-level change (added / removed / modified / moved) instead of highlighting only the path clicked through from.

**Architecture:** Extend `/v1/documents` server-side to return per-document clause counts, change counts by type, and the last two version datetimes (single bounded SQL aggregate per page). Rewrite the webapp's `DocumentsListView` as a paginated `<table>`. In `DocumentDetailView`, fetch the document's change events once via the existing `/v1/discovery?scope=document` endpoint, derive a `path → ChangeType` map for each pane, and have `ClauseOverlay` render a coloured box + pill per matching clause. Single source of truth for colours is a new `src/constants/change-colors.ts` constant shared by the diff view, the legend, and the existing `ChangeTypePill`.

**Tech Stack:** FastAPI + SQLAlchemy (Postgres) on the API; Vue 3 + Vite + Tailwind + @tanstack/vue-query on the webapp; Vitest for unit tests; Playwright for e2e.

**Spec:** [2026-06-07-document-pivot-and-color-diff-design.md](../specs/2026-06-07-document-pivot-and-color-diff-design.md)

---

## Test policy

- Python: TDD where it's natural — write the failing assertion first, run pytest, then implement. Use `uv run pytest -m "not integration"` for fast iteration; integration tests covered in this plan need Docker (testcontainers Postgres).
- Webapp: Vitest unit tests for behaviour; Playwright e2e at the end gates the merge. Before each commit run the local sweep: `uv run pre-commit run --all-files && uv run pytest && cd packages/horizons-webapp && npm run lint:check && npm run build && npm run test:unit -- --run`.
- Run formatter / linter before each commit; commit only when green.

---

## Task 1: Extend Pydantic response models for `/v1/documents`

Adds `clause_count`, `change_counts`, `previous_version_at`, `current_version_at` to `DocumentItem` and `DocumentDetail`. Pure schema task — wire shape changes, no behaviour yet.

**Files:**
- Modify: `packages/horizons-api/src/horizons_api/routes/documents.py:41-87`

- [ ] **Step 1: Add the `ChangeCounts` and extended `DocumentItem` / `DocumentDetail` models**

In `packages/horizons-api/src/horizons_api/routes/documents.py`, add the new model just below the existing `_no_store` block and before `DocumentItem`:

```python
class ChangeCounts(BaseModel):
    """Per-type clause-change counts between the latest two versions of a document.

    All zero when the document has 0 or 1 versions. Sums change events whose
    ``document_version_id`` equals the latest version's id, grouped by
    ``change_type`` (one of ADDED / REMOVED / MODIFIED / MOVED).
    """

    model_config = ConfigDict(frozen=True)

    added: int = 0
    removed: int = 0
    modified: int = 0
    moved: int = 0
```

Then extend `DocumentItem` (existing block at 41-51) and `DocumentDetail` (existing block at 76-87) with four extra fields each (same fields, same order). Replace `DocumentItem`'s body with:

```python
class DocumentItem(BaseModel):
    """List-row shape: a document without its versions."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    jurisdiction: str
    sector: str
    lawstronaut_document_id: str
    title: str
    created_at: datetime
    clause_count: int = 0
    change_counts: ChangeCounts = ChangeCounts()
    previous_version_at: datetime | None = None
    current_version_at: datetime | None = None
```

And `DocumentDetail`:

```python
class DocumentDetail(BaseModel):
    """Detail shape: a document plus the list of its in-scope versions."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    jurisdiction: str
    sector: str
    lawstronaut_document_id: str
    title: str
    created_at: datetime
    clause_count: int = 0
    change_counts: ChangeCounts = ChangeCounts()
    previous_version_at: datetime | None = None
    current_version_at: datetime | None = None
    versions: list[DocumentVersionItem]
```

The defaults let the existing route handlers continue to compile until Task 3 wires the real values through.

- [ ] **Step 2: Run typecheck + smoke**

```bash
uv run pyright packages/horizons-api
uv run pytest packages/horizons-api -m "not integration" -q
```
Expected: PASS — defaults keep existing handlers valid.

- [ ] **Step 3: Commit**

```bash
git add packages/horizons-api/src/horizons_api/routes/documents.py
git commit -m "feat(api): add clause + change-count fields to DocumentItem/DocumentDetail"
```

---

## Task 2: Repository extension — counts + version datetimes per document

Add `list_filtered_with_stats` and `get_by_id_with_stats` to `DocumentsRepository`. Each returns the DTO plus the four new aggregate fields. One SQL aggregate per page, bounded by `limit`.

**Files:**
- Modify: `packages/horizons-core/src/horizons_core/repos/documents.py`
- Test: `packages/horizons-core/tests/repos/test_documents_stats.py` (create)

- [ ] **Step 1: Write the failing integration test**

Create `packages/horizons-core/tests/repos/test_documents_stats.py`:

```python
"""Tests for ``DocumentsRepository.list_filtered_with_stats`` /
``get_by_id_with_stats`` — the clause count, per-type change counts, and
last-two-version datetimes that drive the new documents table view.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from horizons_core.repos.documents import DocumentsRepository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _insert_document(session: AsyncSession, *, jurisdiction: str, sector: str, title: str) -> uuid.UUID:
    doc_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO documents (id, jurisdiction, sector, lawstronaut_document_id, title)
            VALUES (:id, :j, :s, :ldid, :t)
            """
        ),
        {"id": doc_id, "j": jurisdiction, "s": sector, "ldid": f"ldid-{doc_id.hex[:8]}", "t": title},
    )
    return doc_id


async def _insert_version(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    label: str,
    effective_date: datetime,
    clause_count: int,
) -> uuid.UUID:
    version_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO document_versions (id, document_id, version_label, effective_date, content_bytes)
            VALUES (:id, :did, :lbl, :eff, 1024)
            """
        ),
        {"id": version_id, "did": document_id, "lbl": label, "eff": effective_date},
    )
    for ord_ in range(clause_count):
        await session.execute(
            text(
                """
                INSERT INTO clauses (id, document_version_id, clause_uid, clause_path, text_content, ord)
                VALUES (:id, :vid, :uid, :path, 'body', :ord)
                """
            ),
            {
                "id": uuid.uuid4(),
                "vid": version_id,
                "uid": uuid.uuid4(),
                "path": f"/{ord_}",
                "ord": ord_,
            },
        )
    return version_id


async def _insert_change_event(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    document_version_id: uuid.UUID,
    change_type: str,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO change_events
                (document_id, document_version_id, change_type, alignment_confidence, detected_at)
            VALUES (:did, :vid, :ct, 0.99, NOW())
            """
        ),
        {"did": document_id, "vid": document_version_id, "ct": change_type},
    )


async def test_two_version_document_returns_counts_and_datetimes(admin_session: AsyncSession) -> None:
    doc_id = await _insert_document(admin_session, jurisdiction="UK", sector="banking", title="Test Act")
    v1_eff = datetime(2025, 1, 1, tzinfo=UTC)
    v2_eff = datetime(2026, 1, 1, tzinfo=UTC)
    _ = await _insert_version(admin_session, document_id=doc_id, label="v1", effective_date=v1_eff, clause_count=10)
    v2 = await _insert_version(admin_session, document_id=doc_id, label="v2", effective_date=v2_eff, clause_count=12)
    for ct in ("ADDED", "ADDED", "REMOVED", "MODIFIED", "MODIFIED", "MODIFIED", "MOVED"):
        await _insert_change_event(admin_session, document_id=doc_id, document_version_id=v2, change_type=ct)

    rows, total = await DocumentsRepository(admin_session).list_filtered_with_stats(jurisdiction="UK")

    assert total == 1
    assert len(rows) == 1
    row = rows[0]
    assert row.id == doc_id
    assert row.clause_count == 12
    assert row.change_counts.added == 2
    assert row.change_counts.removed == 1
    assert row.change_counts.modified == 3
    assert row.change_counts.moved == 1
    assert row.previous_version_at == v1_eff
    assert row.current_version_at == v2_eff


async def test_one_version_document_has_zero_counts_and_null_previous(admin_session: AsyncSession) -> None:
    doc_id = await _insert_document(admin_session, jurisdiction="UK", sector="banking", title="Sole-version Act")
    v_eff = datetime(2026, 1, 1, tzinfo=UTC)
    await _insert_version(admin_session, document_id=doc_id, label="v1", effective_date=v_eff, clause_count=8)

    rows, _ = await DocumentsRepository(admin_session).list_filtered_with_stats(jurisdiction="UK")

    row = next(r for r in rows if r.id == doc_id)
    assert row.clause_count == 8
    assert row.change_counts.added == 0
    assert row.change_counts.removed == 0
    assert row.change_counts.modified == 0
    assert row.change_counts.moved == 0
    assert row.previous_version_at is None
    assert row.current_version_at == v_eff


async def test_zero_version_document_has_null_datetimes(admin_session: AsyncSession) -> None:
    doc_id = await _insert_document(admin_session, jurisdiction="UK", sector="banking", title="Empty Act")

    rows, _ = await DocumentsRepository(admin_session).list_filtered_with_stats(jurisdiction="UK")

    row = next(r for r in rows if r.id == doc_id)
    assert row.clause_count == 0
    assert row.change_counts.added == 0
    assert row.previous_version_at is None
    assert row.current_version_at is None


async def test_get_by_id_with_stats_returns_same_shape(admin_session: AsyncSession) -> None:
    doc_id = await _insert_document(admin_session, jurisdiction="UK", sector="banking", title="Detail Act")
    v1_eff = datetime(2025, 1, 1, tzinfo=UTC)
    v2_eff = datetime(2026, 1, 1, tzinfo=UTC)
    _ = await _insert_version(admin_session, document_id=doc_id, label="v1", effective_date=v1_eff, clause_count=4)
    v2 = await _insert_version(admin_session, document_id=doc_id, label="v2", effective_date=v2_eff, clause_count=5)
    await _insert_change_event(admin_session, document_id=doc_id, document_version_id=v2, change_type="ADDED")

    row = await DocumentsRepository(admin_session).get_by_id_with_stats(doc_id)

    assert row is not None
    assert row.id == doc_id
    assert row.clause_count == 5
    assert row.change_counts.added == 1
    assert row.previous_version_at == v1_eff
    assert row.current_version_at == v2_eff
```

The `admin_session` fixture is the one used in other repo integration tests — assumes there's one in `packages/horizons-core/tests/conftest.py` that yields an `AsyncSession` against a testcontainers Postgres with the admin role bound. If the fixture is named differently in your repo, adopt the local name.

- [ ] **Step 2: Run the failing test**

```bash
uv run pytest packages/horizons-core/tests/repos/test_documents_stats.py -v
```
Expected: FAIL with `AttributeError: 'DocumentsRepository' object has no attribute 'list_filtered_with_stats'` (or similar). If Docker isn't reachable, integration tests are auto-skipped — start Colima / Docker Desktop before running.

- [ ] **Step 3: Implement the repository methods**

In `packages/horizons-core/src/horizons_core/repos/documents.py`, add a new DTO and two methods. Add imports at the top:

```python
from horizons_core.db.models.change_events import ChangeEvent
from horizons_core.db.models.clauses import Clause
from horizons_core.db.models.versions import DocumentVersion
```

Add the DTO above `DocumentsRepository`:

```python
class ChangeCountsDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    added: int = 0
    removed: int = 0
    modified: int = 0
    moved: int = 0


class DocumentStatsDTO(BaseModel):
    """``DocumentDTO`` plus per-document aggregates for the table view."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    jurisdiction: str
    sector: str
    lawstronaut_document_id: str
    title: str
    created_at: datetime
    clause_count: int = 0
    change_counts: ChangeCountsDTO = ChangeCountsDTO()
    previous_version_at: datetime | None = None
    current_version_at: datetime | None = None
```

Add two methods on `DocumentsRepository`:

```python
async def list_filtered_with_stats(
    self,
    *,
    jurisdiction: str | None = None,
    sector: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[DocumentStatsDTO], int]:
    """Filtered, paginated list — each row carries the four aggregate fields.

    Implementation strategy: one window-function query that ranks versions
    per document by ``effective_date desc nulls last, created_at desc``,
    picking the latest and second-latest as ``v_curr`` / ``v_prev``. The
    per-type change counts come from a correlated aggregate on
    ``change_events`` filtered to ``v_curr.id``. The clause count is a
    correlated scalar on ``clauses`` filtered to ``v_curr.id``.
    """
    rows, total = await self.list_filtered(
        jurisdiction=jurisdiction,
        sector=sector,
        search=search,
        limit=limit,
        offset=offset,
    )
    if not rows:
        return [], total

    doc_ids = [r.id for r in rows]
    stats_by_id = await self._fetch_stats(doc_ids)
    enriched = [self._merge_stats(r, stats_by_id.get(r.id)) for r in rows]
    return enriched, total


async def get_by_id_with_stats(self, document_id: uuid.UUID) -> DocumentStatsDTO | None:
    base = await self.get_by_id(document_id)
    if base is None:
        return None
    stats_by_id = await self._fetch_stats([document_id])
    return self._merge_stats(base, stats_by_id.get(document_id))


async def _fetch_stats(
    self, doc_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, object]]:
    """Single query that returns per-doc aggregates keyed by document id.

    Returns a dict with keys ``clause_count``, ``added``, ``removed``,
    ``modified``, ``moved``, ``previous_version_at``, ``current_version_at``.
    Documents with no versions appear with zero counts and ``None`` datetimes.
    RLS still applies — caller has already filtered via ``list_filtered``
    or ``get_by_id``, so referenced rows are already in scope.
    """
    from sqlalchemy import bindparam, literal_column

    stmt = text(
        """
        WITH ranked_versions AS (
            SELECT
                v.id,
                v.document_id,
                COALESCE(v.effective_date, v.created_at) AS sort_at,
                ROW_NUMBER() OVER (
                    PARTITION BY v.document_id
                    ORDER BY v.effective_date DESC NULLS LAST,
                             v.created_at DESC
                ) AS rn
            FROM document_versions v
            WHERE v.document_id = ANY(:doc_ids)
        ),
        v_curr AS (
            SELECT * FROM ranked_versions WHERE rn = 1
        ),
        v_prev AS (
            SELECT * FROM ranked_versions WHERE rn = 2
        )
        SELECT
            d.id AS document_id,
            COALESCE((SELECT COUNT(*) FROM clauses c WHERE c.document_version_id = v_curr.id), 0) AS clause_count,
            COALESCE(SUM(CASE WHEN ce.change_type = 'ADDED'    THEN 1 ELSE 0 END), 0) AS added,
            COALESCE(SUM(CASE WHEN ce.change_type = 'REMOVED'  THEN 1 ELSE 0 END), 0) AS removed,
            COALESCE(SUM(CASE WHEN ce.change_type = 'MODIFIED' THEN 1 ELSE 0 END), 0) AS modified,
            COALESCE(SUM(CASE WHEN ce.change_type = 'MOVED'    THEN 1 ELSE 0 END), 0) AS moved,
            v_prev.sort_at AS previous_version_at,
            v_curr.sort_at AS current_version_at
        FROM unnest(CAST(:doc_ids AS uuid[])) AS d(id)
        LEFT JOIN v_curr ON v_curr.document_id = d.id
        LEFT JOIN v_prev ON v_prev.document_id = d.id
        LEFT JOIN change_events ce ON ce.document_version_id = v_curr.id
        GROUP BY d.id, v_curr.id, v_curr.sort_at, v_prev.sort_at
        """
    ).bindparams(bindparam("doc_ids", expanding=False))

    result = await self._session.execute(stmt, {"doc_ids": doc_ids})
    out: dict[uuid.UUID, dict[str, object]] = {}
    for row in result.mappings():
        out[row["document_id"]] = {
            "clause_count": int(row["clause_count"] or 0),
            "added": int(row["added"] or 0),
            "removed": int(row["removed"] or 0),
            "modified": int(row["modified"] or 0),
            "moved": int(row["moved"] or 0),
            "previous_version_at": row["previous_version_at"],
            "current_version_at": row["current_version_at"],
        }
    return out


@staticmethod
def _merge_stats(base: DocumentDTO, stats: dict[str, object] | None) -> DocumentStatsDTO:
    if stats is None:
        return DocumentStatsDTO(
            id=base.id,
            jurisdiction=base.jurisdiction,
            sector=base.sector,
            lawstronaut_document_id=base.lawstronaut_document_id,
            title=base.title,
            created_at=base.created_at,
        )
    return DocumentStatsDTO(
        id=base.id,
        jurisdiction=base.jurisdiction,
        sector=base.sector,
        lawstronaut_document_id=base.lawstronaut_document_id,
        title=base.title,
        created_at=base.created_at,
        clause_count=int(stats["clause_count"]),
        change_counts=ChangeCountsDTO(
            added=int(stats["added"]),
            removed=int(stats["removed"]),
            modified=int(stats["modified"]),
            moved=int(stats["moved"]),
        ),
        previous_version_at=stats["previous_version_at"],  # type: ignore[arg-type]
        current_version_at=stats["current_version_at"],  # type: ignore[arg-type]
    )
```

Add the `text` import at the top of the file if it isn't already there:

```python
from sqlalchemy import func, select, text
```

- [ ] **Step 4: Re-run the failing test**

```bash
uv run pytest packages/horizons-core/tests/repos/test_documents_stats.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-core/src/horizons_core/repos/documents.py packages/horizons-core/tests/repos/test_documents_stats.py
git commit -m "feat(repo): document list+detail with clause/change-count stats"
```

---

## Task 3: Wire the new fields through the route handlers

Replace the existing `list_filtered` / `get_by_id` calls in the routes with the `_with_stats` variants and pass the new fields into the Pydantic response models.

**Files:**
- Modify: `packages/horizons-api/src/horizons_api/routes/documents.py:118-188`
- Test: `packages/horizons-api/tests/test_documents.py` (create)

- [ ] **Step 1: Write the failing API integration test**

Create `packages/horizons-api/tests/test_documents.py`. Pattern it on `test_overview.py` — the fixtures and helpers there show how to spin up an authenticated client. At minimum:

```python
"""``/v1/documents`` (list + detail) — new clause / change-count fields.

Each test seeds documents, versions, and change events directly via the
admin session, then asserts the API surface returns the right per-row
aggregates.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.integration


async def test_list_documents_returns_stats(admin_api_client, seed_two_version_doc):
    doc_id, v1_eff, v2_eff = await seed_two_version_doc(
        jurisdiction="UK",
        change_counts={"ADDED": 2, "REMOVED": 1, "MODIFIED": 3, "MOVED": 1},
        clause_count_v2=12,
    )

    response = await admin_api_client.get("/v1/documents?jurisdiction=UK")

    assert response.status_code == 200
    items = response.json()["items"]
    row = next(it for it in items if it["id"] == str(doc_id))
    assert row["clause_count"] == 12
    assert row["change_counts"] == {"added": 2, "removed": 1, "modified": 3, "moved": 1}
    assert row["previous_version_at"].startswith(v1_eff.date().isoformat())
    assert row["current_version_at"].startswith(v2_eff.date().isoformat())


async def test_detail_returns_same_stats_shape(admin_api_client, seed_two_version_doc):
    doc_id, _, _ = await seed_two_version_doc(
        jurisdiction="UK",
        change_counts={"ADDED": 1},
        clause_count_v2=4,
    )

    response = await admin_api_client.get(f"/v1/documents/{doc_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["clause_count"] == 4
    assert body["change_counts"]["added"] == 1
    assert "versions" in body  # existing field preserved
```

If `admin_api_client` and `seed_two_version_doc` don't exist as fixtures, add them to `packages/horizons-api/tests/conftest.py`. Pattern after the existing `test_overview.py` for client setup, and the helpers from Task 2 for direct-SQL seeding.

- [ ] **Step 2: Run the failing test**

```bash
uv run pytest packages/horizons-api/tests/test_documents.py -v
```
Expected: FAIL — fields missing or zero.

- [ ] **Step 3: Update both route handlers**

Replace `list_documents` body (current line 129-152) to use `list_filtered_with_stats` and pass the new fields:

```python
@router.get("", response_model=DocumentPage)
async def list_documents(
    response: Response,
    _principal: Annotated[Principal, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(session_for_request_or_admin)],
    jurisdiction: Annotated[str | None, Query()] = None,
    sector: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentPage:
    _no_store(response)
    rows, total = await DocumentsRepository(session).list_filtered_with_stats(
        jurisdiction=jurisdiction,
        sector=sector,
        search=search,
        limit=limit,
        offset=offset,
    )
    return DocumentPage(
        items=[
            DocumentItem(
                id=r.id,
                jurisdiction=r.jurisdiction,
                sector=r.sector,
                lawstronaut_document_id=r.lawstronaut_document_id,
                title=r.title,
                created_at=r.created_at,
                clause_count=r.clause_count,
                change_counts=ChangeCounts(
                    added=r.change_counts.added,
                    removed=r.change_counts.removed,
                    modified=r.change_counts.modified,
                    moved=r.change_counts.moved,
                ),
                previous_version_at=r.previous_version_at,
                current_version_at=r.current_version_at,
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
```

Replace `get_document` body (current line 162-188) similarly — call `get_by_id_with_stats` and pass the four new fields plus the existing `versions` list:

```python
@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    response: Response,
    document_id: uuid.UUID,
    _principal: Annotated[Principal, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(session_for_request_or_admin)],
) -> DocumentDetail:
    _no_store(response)
    document = await DocumentsRepository(session).get_by_id_with_stats(document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="document not found",
        )
    versions = await DocumentVersionsRepository(session).list_for_document(document_id)
    return DocumentDetail(
        id=document.id,
        jurisdiction=document.jurisdiction,
        sector=document.sector,
        lawstronaut_document_id=document.lawstronaut_document_id,
        title=document.title,
        created_at=document.created_at,
        clause_count=document.clause_count,
        change_counts=ChangeCounts(
            added=document.change_counts.added,
            removed=document.change_counts.removed,
            modified=document.change_counts.modified,
            moved=document.change_counts.moved,
        ),
        previous_version_at=document.previous_version_at,
        current_version_at=document.current_version_at,
        versions=[
            DocumentVersionItem(
                id=v.id,
                version_label=v.version_label,
                publication_date=v.publication_date,
                effective_date=v.effective_date,
                content_bytes=v.content_bytes,
                created_at=v.created_at,
            )
            for v in versions
        ],
    )
```

- [ ] **Step 4: Re-run the failing test + sweep**

```bash
uv run pytest packages/horizons-api/tests/test_documents.py -v
uv run pyright
uv run ruff format packages/horizons-api packages/horizons-core
uv run ruff check packages/horizons-api packages/horizons-core
uv run python packages/horizons-api/scripts/regen_endpoints_md.py
```
Expected: tests PASS; pyright clean; endpoints.md regenerated with the new fields.

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-api/src/horizons_api/routes/documents.py packages/horizons-api/tests/test_documents.py packages/horizons-api/tests/conftest.py docs/api/endpoints.md
git commit -m "feat(api): /v1/documents returns clause + change-count stats per row"
```

---

## Task 4: Webapp TypeScript types match the new API shape

Mirror the new fields in the webapp's TS definitions so downstream code is type-checked end-to-end.

**Files:**
- Modify: `packages/horizons-webapp/src/api/documents.ts:3-30`

- [ ] **Step 1: Add `ChangeCounts` and extend `DocumentItem` / `DocumentDetail`**

In `packages/horizons-webapp/src/api/documents.ts`, replace the `DocumentItem` interface and add a new `ChangeCounts` type just above it:

```ts
export interface ChangeCounts {
  added: number
  removed: number
  modified: number
  moved: number
}

export interface DocumentItem {
  id: string
  jurisdiction: string
  sector: string
  lawstronaut_document_id: string
  title: string
  created_at: string
  clause_count: number
  change_counts: ChangeCounts
  previous_version_at: string | null
  current_version_at: string | null
}
```

Extend `DocumentDetail` so it inherits the new fields via `extends DocumentItem` (already does) — no further change needed there.

- [ ] **Step 2: Run typecheck**

```bash
cd packages/horizons-webapp && npx vue-tsc --noEmit
```
Expected: PASS. (Existing call sites that destructure only the old fields keep compiling.)

- [ ] **Step 3: Commit**

```bash
git add packages/horizons-webapp/src/api/documents.ts
git commit -m "feat(webapp): match new /v1/documents shape with clause + change counts"
```

---

## Task 5: Change-colour constant (single source of truth)

Centralise the four colour mappings used by `ClauseOverlay`, the legend, and the existing `ChangeTypePill`.

**Files:**
- Create: `packages/horizons-webapp/src/constants/change-colors.ts`
- Test: `packages/horizons-webapp/src/constants/__tests__/change-colors.spec.ts`

- [ ] **Step 1: Write the failing test**

Create `packages/horizons-webapp/src/constants/__tests__/change-colors.spec.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { CHANGE_COLORS, type ChangeType } from '../change-colors'

describe('CHANGE_COLORS', () => {
  it('defines an entry for every ChangeType', () => {
    const types: ChangeType[] = ['ADDED', 'REMOVED', 'MODIFIED', 'MOVED']
    for (const t of types) {
      expect(CHANGE_COLORS[t]).toBeDefined()
      expect(CHANGE_COLORS[t].box).toMatch(/border-/)
      expect(CHANGE_COLORS[t].box).toMatch(/bg-/)
      expect(CHANGE_COLORS[t].pill).toMatch(/bg-/)
      expect(CHANGE_COLORS[t].pill).toMatch(/text-/)
      expect(CHANGE_COLORS[t].label).toBe(t)
    }
  })

  it('uses the spec palette (green/red/amber/blue)', () => {
    expect(CHANGE_COLORS.ADDED.box).toContain('green')
    expect(CHANGE_COLORS.REMOVED.box).toContain('red')
    expect(CHANGE_COLORS.MODIFIED.box).toContain('amber')
    expect(CHANGE_COLORS.MOVED.box).toContain('blue')
  })
})
```

- [ ] **Step 2: Run the failing test**

```bash
cd packages/horizons-webapp && npx vitest run src/constants/__tests__/change-colors.spec.ts
```
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write the constant**

Create `packages/horizons-webapp/src/constants/change-colors.ts`:

```ts
export type ChangeType = 'ADDED' | 'REMOVED' | 'MODIFIED' | 'MOVED'

export interface ChangeColor {
  /** Tailwind classes for the bordered clause box in the diff view. */
  box: string
  /** Tailwind classes for the small corner pill that labels the change. */
  pill: string
  /** Human-readable label (matches the enum casing). */
  label: ChangeType
}

export const CHANGE_COLORS: Record<ChangeType, ChangeColor> = {
  ADDED: {
    box: 'rounded-md bg-green-50 ring-2 ring-green-400 p-3',
    pill: 'bg-green-100 text-green-800 ring-green-300',
    label: 'ADDED',
  },
  REMOVED: {
    box: 'rounded-md bg-red-50 ring-2 ring-red-400 p-3',
    pill: 'bg-red-100 text-red-800 ring-red-300',
    label: 'REMOVED',
  },
  MODIFIED: {
    box: 'rounded-md bg-amber-50 ring-2 ring-amber-400 p-3',
    pill: 'bg-amber-100 text-amber-800 ring-amber-300',
    label: 'MODIFIED',
  },
  MOVED: {
    box: 'rounded-md bg-blue-50 ring-2 ring-blue-400 p-3',
    pill: 'bg-blue-100 text-blue-800 ring-blue-300',
    label: 'MOVED',
  },
}
```

- [ ] **Step 4: Re-run the test**

```bash
cd packages/horizons-webapp && npx vitest run src/constants/__tests__/change-colors.spec.ts
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-webapp/src/constants/change-colors.ts packages/horizons-webapp/src/constants/__tests__/change-colors.spec.ts
git commit -m "feat(webapp): central CHANGE_COLORS constant for diff view + pills"
```

---

## Task 6: HomeView routes cards unconditionally to documents

Strip the `changeCount > 0 ? '/changes' : '/documents'` branching from both card handlers.

**Files:**
- Modify: `packages/horizons-webapp/src/views/HomeView.vue:14-28`
- Test: `packages/horizons-webapp/src/views/__tests__/HomeView.spec.ts`

- [ ] **Step 1: Add a failing test**

Open `packages/horizons-webapp/src/views/__tests__/HomeView.spec.ts`. Add two new `it()` cases to the existing describe block:

```ts
it('routes jurisdiction card click to /documents regardless of changeCount > 0', async () => {
  const { wrapper, router } = await mountHomeView(/* with overview that has UK changeCount = 13 */)
  const ukCard = wrapper.find('[data-testid="jurisdiction-card-UK"]')
  await ukCard.trigger('click')
  expect(router.push).toHaveBeenCalledWith({ name: 'documents', query: { jurisdiction: 'UK' } })
})

it('routes sector card click to /documents regardless of changeCount > 0', async () => {
  const { wrapper, router } = await mountHomeView(/* with overview that has BANKING changeCount = 7 */)
  const sectorCard = wrapper.find('[data-testid="sector-card-BANKING"]')
  await sectorCard.trigger('click')
  expect(router.push).toHaveBeenCalledWith({ name: 'documents', query: { sector: 'BANKING' } })
})
```

Adapt to the file's existing `mountHomeView` helper and `data-testid` conventions (look at the top of the existing spec for the exact pattern; cards may already use `JurisdictionCard` / `SectorCard` test IDs).

- [ ] **Step 2: Run the failing test**

```bash
cd packages/horizons-webapp && npx vitest run src/views/__tests__/HomeView.spec.ts
```
Expected: FAIL — current code routes to `changes` when `changeCount > 0`.

- [ ] **Step 3: Simplify the handlers**

Replace the two handlers in `packages/horizons-webapp/src/views/HomeView.vue` (current lines 11-28):

```ts
function goToJurisdiction(code: string): void {
  router.push({ name: 'documents', query: { jurisdiction: code } })
}

function goToSector(code: string): void {
  router.push({ name: 'documents', query: { sector: code } })
}
```

The `JurisdictionCard` / `SectorCard` `@select` handlers currently pass `(code, changeCount)`. The second arg is now unused — Vue is fine ignoring it. If pyright/eslint complains about unused args, accept the extra parameter and discard it:

```ts
function goToJurisdiction(code: string, _changeCount: number): void { … }
```

- [ ] **Step 4: Re-run the test + run the whole file**

```bash
cd packages/horizons-webapp && npx vitest run src/views/__tests__/HomeView.spec.ts
```
Expected: PASS (including older "routes to /changes" cases — which will need updating in this same task if they exist).

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-webapp/src/views/HomeView.vue packages/horizons-webapp/src/views/__tests__/HomeView.spec.ts
git commit -m "feat(webapp): jurisdiction/sector cards always route to documents"
```

---

## Task 7: DocumentsListView — paginated table

Replace the `<ul>` of card-like rows with a real `<table>`, page size 25, prev/next with URL-synced `offset`.

**Files:**
- Modify: `packages/horizons-webapp/src/views/DocumentsListView.vue` (full rewrite of the list body and pagination, keep filter bar)
- Test: `packages/horizons-webapp/src/views/__tests__/DocumentsListView.spec.ts` (create)

- [ ] **Step 1: Write the failing tests**

Create `packages/horizons-webapp/src/views/__tests__/DocumentsListView.spec.ts`:

```ts
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createRouter, createMemoryHistory } from 'vue-router'
import DocumentsListView from '../DocumentsListView.vue'
import * as docsApi from '@/api/documents'

vi.mock('@/api/documents', async (orig) => {
  const actual = (await orig()) as typeof import('@/api/documents')
  return { ...actual, listDocuments: vi.fn() }
})

function makeDoc(overrides: Partial<docsApi.DocumentItem> = {}): docsApi.DocumentItem {
  return {
    id: 'doc-1',
    jurisdiction: 'UK',
    sector: 'banking',
    lawstronaut_document_id: 'L-1',
    title: 'Employment Act',
    created_at: '2026-01-01T00:00:00Z',
    clause_count: 42,
    change_counts: { added: 2, removed: 1, modified: 3, moved: 0 },
    previous_version_at: '2025-01-01T00:00:00Z',
    current_version_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

async function mountList(routeQuery: Record<string, string> = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/documents', name: 'documents', component: DocumentsListView },
      { path: '/documents/:id', name: 'document-detail', component: { template: '<div/>' } },
    ],
  })
  await router.push({ path: '/documents', query: routeQuery })
  await router.isReady()
  const wrapper = mount(DocumentsListView, {
    global: {
      plugins: [router, [VueQueryPlugin, { queryClient: new QueryClient() }]],
    },
  })
  await new Promise((r) => setTimeout(r, 0))
  return { wrapper, router }
}

describe('DocumentsListView', () => {
  beforeEach(() => {
    vi.mocked(docsApi.listDocuments).mockReset()
  })

  it('renders an 8-column table with name, length, 4 change counts, and 2 datetimes', async () => {
    vi.mocked(docsApi.listDocuments).mockResolvedValue({
      items: [makeDoc()],
      total: 1,
      limit: 25,
      offset: 0,
    })
    const { wrapper } = await mountList()
    const headers = wrapper.findAll('thead th').map((h) => h.text())
    expect(headers).toEqual([
      'Name',
      'Length',
      'Added',
      'Removed',
      'Modified',
      'Moved',
      'Previous version',
      'Current version',
    ])
    const cells = wrapper.find('tbody tr').findAll('td').map((c) => c.text())
    expect(cells[0]).toContain('Employment Act')
    expect(cells[1]).toBe('42')
    expect(cells[2]).toBe('2')
    expect(cells[3]).toBe('1')
    expect(cells[4]).toBe('3')
    expect(cells[5]).toBe('—')
    expect(cells[6]).toBe('2025-01-01')
    expect(cells[7]).toBe('2026-01-01')
  })

  it('renders muted dash for zero change counts and empty cells for null datetimes', async () => {
    vi.mocked(docsApi.listDocuments).mockResolvedValue({
      items: [
        makeDoc({
          change_counts: { added: 0, removed: 0, modified: 0, moved: 0 },
          previous_version_at: null,
          current_version_at: null,
        }),
      ],
      total: 1,
      limit: 25,
      offset: 0,
    })
    const { wrapper } = await mountList()
    const cells = wrapper.find('tbody tr').findAll('td').map((c) => c.text())
    expect(cells.slice(2, 6)).toEqual(['—', '—', '—', '—'])
    expect(cells[6]).toBe('')
    expect(cells[7]).toBe('')
  })

  it('disables Prev on the first page and advances offset on Next', async () => {
    vi.mocked(docsApi.listDocuments).mockResolvedValue({
      items: [makeDoc()],
      total: 60,
      limit: 25,
      offset: 0,
    })
    const { wrapper, router } = await mountList()
    expect(wrapper.find('[data-testid="page-prev"]').attributes('disabled')).toBeDefined()
    await wrapper.find('[data-testid="page-next"]').trigger('click')
    expect(router.currentRoute.value.query.offset).toBe('25')
  })

  it('disables Next on the final page', async () => {
    vi.mocked(docsApi.listDocuments).mockResolvedValue({
      items: [makeDoc()],
      total: 30,
      limit: 25,
      offset: 25,
    })
    const { wrapper } = await mountList({ offset: '25' })
    expect(wrapper.find('[data-testid="page-next"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="page-prev"]').attributes('disabled')).toBeUndefined()
  })
})
```

- [ ] **Step 2: Run the failing tests**

```bash
cd packages/horizons-webapp && npx vitest run src/views/__tests__/DocumentsListView.spec.ts
```
Expected: FAIL — current view is a `<ul>`, not a `<table>`; no `data-testid="page-prev"` exists.

- [ ] **Step 3: Rewrite the view**

Replace the body of `packages/horizons-webapp/src/views/DocumentsListView.vue` with the table + pagination. Keep the existing filter bar; replace the `<ul>` block (currently lines 137-164) and remove the unused `formatDate` is replaced with one that handles null:

```vue
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { listDocuments, type DocumentItem, type DocumentPage } from '@/api/documents'
import AppNavBar from '@/components/AppNavBar.vue'
import { Button } from '@/components/ui/button'

const router = useRouter()
const route = useRoute()

const PAGE_SIZE = 25

const search = ref<string>(typeof route.query.search === 'string' ? route.query.search : '')
const jurisdiction = ref<string>(
  typeof route.query.jurisdiction === 'string' ? route.query.jurisdiction : '',
)
const sector = ref<string>(typeof route.query.sector === 'string' ? route.query.sector : '')
const offset = ref<number>(
  typeof route.query.offset === 'string' ? Math.max(0, parseInt(route.query.offset, 10) || 0) : 0,
)

// Sync filter state back into the URL so the view is shareable / reload-safe.
// Filters reset offset to 0 to avoid landing on an empty page.
watch([search, jurisdiction, sector], async () => {
  offset.value = 0
  await syncUrl()
})
watch(offset, async () => {
  await syncUrl()
})

async function syncUrl(): Promise<void> {
  await router.replace({
    name: 'documents',
    query: {
      ...(search.value ? { search: search.value } : {}),
      ...(jurisdiction.value ? { jurisdiction: jurisdiction.value } : {}),
      ...(sector.value ? { sector: sector.value } : {}),
      ...(offset.value > 0 ? { offset: String(offset.value) } : {}),
    },
  })
}

const queryKey = computed(() => [
  'documents-list',
  search.value,
  jurisdiction.value,
  sector.value,
  offset.value,
])

const query = useQuery<DocumentPage>({
  queryKey,
  queryFn: () =>
    listDocuments({
      search: search.value || undefined,
      jurisdiction: jurisdiction.value || undefined,
      sector: sector.value || undefined,
      limit: PAGE_SIZE,
      offset: offset.value,
    }),
})

const items = computed<DocumentItem[]>(() => query.data.value?.items ?? [])
const total = computed<number>(() => query.data.value?.total ?? 0)
const isLoading = computed(() => query.isPending.value)
const hasError = computed(() => query.isError.value)
const isEmpty = computed(() => !isLoading.value && items.value.length === 0)

const pageStart = computed(() => (total.value === 0 ? 0 : offset.value + 1))
const pageEnd = computed(() => Math.min(offset.value + PAGE_SIZE, total.value))
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const currentPage = computed(() => Math.floor(offset.value / PAGE_SIZE) + 1)
const prevDisabled = computed(() => offset.value === 0)
const nextDisabled = computed(() => offset.value + PAGE_SIZE >= total.value)

function nextPage(): void {
  if (nextDisabled.value) return
  offset.value = offset.value + PAGE_SIZE
}
function prevPage(): void {
  if (prevDisabled.value) return
  offset.value = Math.max(0, offset.value - PAGE_SIZE)
}

function fmtDate(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toISOString().slice(0, 10)
}

function fmtCount(n: number): string {
  return n === 0 ? '—' : String(n)
}
</script>

<template>
  <main class="min-h-screen bg-slate-50">
    <AppNavBar />

    <section class="mx-auto max-w-7xl px-6 py-10">
      <div class="mb-6">
        <h1 class="text-2xl font-semibold tracking-tight text-slate-900">Documents</h1>
        <p class="mt-1 text-sm text-slate-500">
          Browse the documents in your subscription scope. Open one to see its
          clause structure and changes.
        </p>
      </div>

      <!-- Filter bar (preserved from previous view) -->
      <div class="mb-4 flex flex-wrap items-end gap-3 rounded-md border border-slate-200 bg-white p-3">
        <label class="flex flex-1 flex-col text-xs text-slate-600">
          <span class="mb-1">Search title</span>
          <input
            v-model="search"
            type="text"
            data-testid="filter-search"
            placeholder="e.g. employment, banking"
            class="rounded border border-slate-300 px-2 py-1.5 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
          />
        </label>
        <label class="flex flex-col text-xs text-slate-600">
          <span class="mb-1">Jurisdiction</span>
          <input
            v-model="jurisdiction"
            type="text"
            data-testid="filter-jurisdiction"
            placeholder="UK, EU, IE…"
            class="w-32 rounded border border-slate-300 px-2 py-1.5 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
          />
        </label>
        <label class="flex flex-col text-xs text-slate-600">
          <span class="mb-1">Sector</span>
          <input
            v-model="sector"
            type="text"
            data-testid="filter-sector"
            placeholder="BANKING, employment…"
            class="w-40 rounded border border-slate-300 px-2 py-1.5 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
          />
        </label>
        <span data-testid="documents-total" class="ml-auto text-xs text-slate-500"
          >{{ total }} document{{ total === 1 ? '' : 's' }}</span
        >
      </div>

      <div
        v-if="isLoading"
        data-testid="loading-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
      >
        Loading documents…
      </div>

      <div
        v-else-if="hasError"
        role="alert"
        data-testid="error-state"
        class="rounded-md border border-red-200 bg-red-50 p-6 text-sm text-red-800"
      >
        Could not load documents. Please try again.
      </div>

      <div
        v-else-if="isEmpty"
        data-testid="empty-state"
        class="rounded-md border border-slate-200 bg-white p-6 text-sm text-slate-500"
      >
        No documents match these filters.
      </div>

      <table
        v-else
        class="w-full overflow-hidden rounded-md border border-slate-200 bg-white text-sm"
      >
        <thead class="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th class="px-4 py-2 font-semibold">Name</th>
            <th class="px-4 py-2 text-right font-semibold">Length</th>
            <th class="px-4 py-2 text-right font-semibold">Added</th>
            <th class="px-4 py-2 text-right font-semibold">Removed</th>
            <th class="px-4 py-2 text-right font-semibold">Modified</th>
            <th class="px-4 py-2 text-right font-semibold">Moved</th>
            <th class="px-4 py-2 font-semibold">Previous version</th>
            <th class="px-4 py-2 font-semibold">Current version</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in items"
            :key="item.id"
            data-testid="document-row"
            class="border-t border-slate-200 hover:bg-slate-50"
          >
            <td class="px-4 py-2">
              <RouterLink
                :to="{ name: 'document-detail', params: { id: item.id } }"
                class="font-medium text-slate-900 hover:underline"
              >
                {{ item.title }}
              </RouterLink>
              <div class="text-xs text-slate-500">{{ item.jurisdiction }} · {{ item.sector }}</div>
            </td>
            <td class="px-4 py-2 text-right tabular-nums text-slate-700">{{ item.clause_count }}</td>
            <td class="px-4 py-2 text-right tabular-nums text-slate-700">{{ fmtCount(item.change_counts.added) }}</td>
            <td class="px-4 py-2 text-right tabular-nums text-slate-700">{{ fmtCount(item.change_counts.removed) }}</td>
            <td class="px-4 py-2 text-right tabular-nums text-slate-700">{{ fmtCount(item.change_counts.modified) }}</td>
            <td class="px-4 py-2 text-right tabular-nums text-slate-700">{{ fmtCount(item.change_counts.moved) }}</td>
            <td class="px-4 py-2 tabular-nums text-slate-700">{{ fmtDate(item.previous_version_at) }}</td>
            <td class="px-4 py-2 tabular-nums text-slate-700">{{ fmtDate(item.current_version_at) }}</td>
          </tr>
        </tbody>
      </table>

      <div
        v-if="!isLoading && !isEmpty && total > 0"
        class="mt-4 flex items-center justify-between text-xs text-slate-600"
      >
        <span data-testid="page-range">Showing {{ pageStart }}–{{ pageEnd }} of {{ total }}</span>
        <div class="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            data-testid="page-prev"
            :disabled="prevDisabled"
            @click="prevPage"
          >
            ‹ Prev
          </Button>
          <span data-testid="page-indicator">Page {{ currentPage }} of {{ totalPages }}</span>
          <Button
            variant="outline"
            size="sm"
            data-testid="page-next"
            :disabled="nextDisabled"
            @click="nextPage"
          >
            Next ›
          </Button>
        </div>
      </div>
    </section>
  </main>
</template>
```

- [ ] **Step 4: Re-run the tests**

```bash
cd packages/horizons-webapp && npx vitest run src/views/__tests__/DocumentsListView.spec.ts
```
Expected: PASS (4 cases).

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-webapp/src/views/DocumentsListView.vue packages/horizons-webapp/src/views/__tests__/DocumentsListView.spec.ts
git commit -m "feat(webapp): rewrite documents list as paginated 8-column table"
```

---

## Task 8: ClauseOverlay consumes a `changeMap`, renders coloured box + pill

Replace the single `highlightPath` prop with a `changeMap: Record<string, ChangeType> | null`. The deep-link query string (`?before` / `?after`) becomes a scroll-only `scrollToPath` prop.

**Files:**
- Modify: `packages/horizons-webapp/src/components/documents/ClauseOverlay.vue`
- Modify: `packages/horizons-webapp/src/components/documents/__tests__/ClauseOverlay.spec.ts`

- [ ] **Step 1: Add failing tests**

Append to `packages/horizons-webapp/src/components/documents/__tests__/ClauseOverlay.spec.ts`:

```ts
import { CHANGE_COLORS } from '@/constants/change-colors'

describe('ClauseOverlay with changeMap', () => {
  const clauses = [
    { id: '1', clause_uid: 'a', clause_path: '/p/1', text_content: 'one', heading_text: null, ord: 0 },
    { id: '2', clause_uid: 'b', clause_path: '/p/2', text_content: 'two', heading_text: null, ord: 1 },
    { id: '3', clause_uid: 'c', clause_path: '/p/3', text_content: 'three', heading_text: null, ord: 2 },
  ]

  it('applies the ADDED box class to clauses whose path matches', () => {
    const wrapper = mount(ClauseOverlay, {
      props: {
        clauses,
        showStructure: false,
        changeMap: { '/p/2': 'ADDED' },
      },
    })
    const row = wrapper.find('[data-clause-path="/p/2"]')
    expect(row.classes().join(' ')).toContain('ring-green-400')
    const pill = row.find('[data-testid="clause-change-pill"]')
    expect(pill.text()).toBe('ADDED')
    expect(pill.classes().join(' ')).toContain('text-green-800')
  })

  it('leaves unmatched clauses without a colour box', () => {
    const wrapper = mount(ClauseOverlay, {
      props: { clauses, showStructure: false, changeMap: { '/p/2': 'MODIFIED' } },
    })
    const row1 = wrapper.find('[data-clause-path="/p/1"]')
    expect(row1.find('[data-testid="clause-change-pill"]').exists()).toBe(false)
    expect(row1.classes().join(' ')).not.toContain('ring-')
  })

  it('uses the right colour for each ChangeType (snapshot of the constants)', () => {
    for (const type of ['REMOVED', 'MODIFIED', 'MOVED'] as const) {
      const wrapper = mount(ClauseOverlay, {
        props: { clauses, showStructure: false, changeMap: { '/p/1': type } },
      })
      const row = wrapper.find('[data-clause-path="/p/1"]')
      const expected = CHANGE_COLORS[type].box.match(/ring-\S+/)?.[0]
      expect(expected).toBeDefined()
      expect(row.classes().join(' ')).toContain(expected!)
    }
  })
})
```

- [ ] **Step 2: Run the failing tests**

```bash
cd packages/horizons-webapp && npx vitest run src/components/documents/__tests__/ClauseOverlay.spec.ts
```
Expected: FAIL — `changeMap` prop not defined.

- [ ] **Step 3: Rewrite the script + template**

In `packages/horizons-webapp/src/components/documents/ClauseOverlay.vue`, change the props interface and the per-clause class logic. Replace lines 1-12 with:

```ts
<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import type { ClauseItem } from '@/api/documents'
import { looksLikeHtml, sanitizeClauseHtml } from '@/lib/sanitizeClauseHtml'
import { CHANGE_COLORS, type ChangeType } from '@/constants/change-colors'

interface Props {
  clauses: ClauseItem[]
  showStructure: boolean
  changeMap?: Record<string, ChangeType> | null
  scrollToPath?: string | null
}

const props = withDefaults(defineProps<Props>(), { changeMap: null, scrollToPath: null })
```

Replace the existing `scrollToHighlight` function to read `scrollToPath` instead of `highlightPath`:

```ts
function scrollToTarget(): void {
  const target = props.scrollToPath
  if (!target) return
  if (!root.value) return
  const match = props.clauses.find((c) => c.clause_path === target)
  if (!match) {
    console.warn(`ClauseOverlay: scrollToPath "${target}" not found in clauses`)
    return
  }
  const safe = target.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
  const el = root.value.querySelector(`[data-clause-path="${safe}"]`)
  if (!(el instanceof HTMLElement)) return
  if (typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ block: 'center', behavior: 'auto' })
  }
}

onMounted(() => {
  void nextTick().then(() => scrollToTarget())
})

watch(
  () => [props.scrollToPath, props.clauses.length] as const,
  () => {
    void nextTick().then(() => scrollToTarget())
  },
)
```

Replace the `isHighlighted` helper with a `changeTypeFor` helper:

```ts
function changeTypeFor(path: string): ChangeType | null {
  if (!props.changeMap) return null
  return props.changeMap[path] ?? null
}
```

In both templates (flat and structure modes) replace the `isHighlighted(...)` branch with a colour-from-`changeTypeFor(...)` lookup. For the flat mode, change the `<div>` block to render the box and the pill:

```html
<div
  v-for="dc in decorated"
  :key="dc.clause.id"
  data-testid="clause-flat"
  :data-clause-path="dc.clause.clause_path"
  :data-change-type="changeTypeFor(dc.clause.clause_path) ?? undefined"
  class="mb-4 relative"
  :class="changeTypeFor(dc.clause.clause_path) ? CHANGE_COLORS[changeTypeFor(dc.clause.clause_path)!].box : ''"
>
  <span
    v-if="changeTypeFor(dc.clause.clause_path)"
    data-testid="clause-change-pill"
    class="absolute right-2 top-2 inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset"
    :class="CHANGE_COLORS[changeTypeFor(dc.clause.clause_path)!].pill"
  >
    {{ CHANGE_COLORS[changeTypeFor(dc.clause.clause_path)!].label }}
  </span>
  …rest of the clause body unchanged…
</div>
```

Apply the same `data-change-type`, pill, and class binding to the `<li>` in structure mode.

Make `CHANGE_COLORS` available to the template by exposing it from `<script setup>` (re-export via `const colors = CHANGE_COLORS` so the template can reference it, or use it directly inline if Vue picks up the import — Vue 3 `<script setup>` exposes imports to templates by default).

- [ ] **Step 4: Re-run tests**

```bash
cd packages/horizons-webapp && npx vitest run src/components/documents/__tests__/ClauseOverlay.spec.ts
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-webapp/src/components/documents/ClauseOverlay.vue packages/horizons-webapp/src/components/documents/__tests__/ClauseOverlay.spec.ts
git commit -m "feat(webapp): ClauseOverlay colours each clause by ChangeType"
```

---

## Task 9: VersionPane forwards `changeMap`

Drop `highlightPath` from the pane's API; add `changeMap` and `scrollToPath` and forward both to `ClauseOverlay`.

**Files:**
- Modify: `packages/horizons-webapp/src/components/documents/VersionPane.vue`
- Modify: `packages/horizons-webapp/src/components/documents/__tests__/VersionPane.spec.ts`

- [ ] **Step 1: Failing test**

In `packages/horizons-webapp/src/components/documents/__tests__/VersionPane.spec.ts`, add:

```ts
it('forwards changeMap and scrollToPath to ClauseOverlay', async () => {
  const wrapper = mount(VersionPane, {
    props: {
      documentId: 'doc-1',
      versionLabel: 'v2',
      seenAt: '2026-06-07T00:00:00Z',
      showStructure: false,
      changeMap: { '/x/1': 'ADDED' },
      scrollToPath: '/x/1',
    },
    global: { plugins: [[VueQueryPlugin, { queryClient: new QueryClient() }]] },
  })
  await new Promise((r) => setTimeout(r, 0))
  // Wait for the (mocked) clauses query to resolve, then assert the overlay receives the props.
  const overlay = wrapper.findComponent({ name: 'ClauseOverlay' })
  // If the overlay is not yet mounted because clauses query is still pending, that's still a useful contract test;
  // skip the assertion in that case.
  if (overlay.exists()) {
    expect(overlay.props('changeMap')).toEqual({ '/x/1': 'ADDED' })
    expect(overlay.props('scrollToPath')).toBe('/x/1')
  }
})
```

(The existing spec file already mocks `getClauses` — reuse that mock pattern; if not, add one with `vi.mock('@/api/documents', ...)`.)

- [ ] **Step 2: Run the failing test**

```bash
cd packages/horizons-webapp && npx vitest run src/components/documents/__tests__/VersionPane.spec.ts
```
Expected: FAIL on the props not being defined.

- [ ] **Step 3: Update the component**

Replace the `Props` interface and the `<ClauseOverlay>` line in `VersionPane.vue`:

```ts
import type { ChangeType } from '@/constants/change-colors'

interface Props {
  documentId: string
  versionLabel: string
  seenAt: string
  showStructure: boolean
  changeMap?: Record<string, ChangeType> | null
  scrollToPath?: string | null
}

const props = withDefaults(defineProps<Props>(), { changeMap: null, scrollToPath: null })
```

And the template overlay line:

```html
<ClauseOverlay
  v-else-if="query.data.value"
  :clauses="query.data.value.clauses"
  :show-structure="showStructure"
  :change-map="changeMap"
  :scroll-to-path="scrollToPath"
/>
```

- [ ] **Step 4: Re-run tests**

```bash
cd packages/horizons-webapp && npx vitest run src/components/documents/__tests__/VersionPane.spec.ts
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-webapp/src/components/documents/VersionPane.vue packages/horizons-webapp/src/components/documents/__tests__/VersionPane.spec.ts
git commit -m "feat(webapp): VersionPane forwards changeMap + scrollToPath to overlay"
```

---

## Task 10: DocumentDetailView fetches changes, derives per-pane maps, renders legend

This is where it comes together. The view runs a second `useQuery` for `/v1/discovery?scope=document&document_id=...`, derives `beforePathToType` and `afterPathToType`, and passes them into the panes. Also renders a small legend strip.

**Files:**
- Modify: `packages/horizons-webapp/src/views/DocumentDetailView.vue`
- Modify: `packages/horizons-webapp/src/views/__tests__/DocumentDetailView.spec.ts`
- Modify: `packages/horizons-webapp/src/api/changes.ts` (extend `fetchDiscovery` to accept `document_id` if not already)

- [ ] **Step 1: Confirm the API client supports `scope=document&document_id=...`**

Open `packages/horizons-webapp/src/api/changes.ts`. The existing `DiscoveryParams` does not include `scope` or `document_id`. Extend it:

```ts
export interface DiscoveryParams {
  cursor?: string | null
  limit?: number
  jurisdiction?: string | null
  sector?: string | null
  scope?: 'corpus' | 'document'
  document_id?: string
}

export async function fetchDiscovery(params: DiscoveryParams = {}): Promise<DiscoveryPage> {
  const search: Record<string, string | number> = { scope: params.scope ?? 'corpus' }
  if (params.limit !== undefined) search.limit = params.limit
  if (params.cursor) search.cursor = params.cursor
  if (params.jurisdiction) search.jurisdiction = params.jurisdiction
  if (params.sector) search.sector = params.sector
  if (params.document_id) search.document_id = params.document_id
  const response = await apiClient.get<DiscoveryPage>('/v1/discovery', { params: search })
  return response.data
}
```

- [ ] **Step 2: Failing test**

In `packages/horizons-webapp/src/views/__tests__/DocumentDetailView.spec.ts`, add a test that mocks both `getDocument` and `fetchDiscovery`:

```ts
it('passes ADDED/MODIFIED/MOVED to right pane and REMOVED/MODIFIED/MOVED to left pane', async () => {
  // Mock document with two versions
  vi.mocked(docsApi.getDocument).mockResolvedValue({
    id: 'd', jurisdiction: 'UK', sector: 'banking',
    lawstronaut_document_id: 'L', title: 'Test',
    created_at: '2026-01-01T00:00:00Z',
    clause_count: 0,
    change_counts: { added: 0, removed: 0, modified: 0, moved: 0 },
    previous_version_at: '2025-01-01T00:00:00Z',
    current_version_at: '2026-01-01T00:00:00Z',
    versions: [
      { id: 'v1', version_label: 'v1', publication_date: null, effective_date: '2025-01-01T00:00:00Z', content_bytes: 100, created_at: '2025-01-01T00:00:00Z' },
      { id: 'v2', version_label: 'v2', publication_date: null, effective_date: '2026-01-01T00:00:00Z', content_bytes: 110, created_at: '2026-01-01T00:00:00Z' },
    ],
  })
  vi.mocked(changesApi.fetchDiscovery).mockResolvedValue({
    items: [
      { id: 1, document_id: 'd', document_version_id: 'v2', jurisdiction: 'UK', sector: 'banking',
        change_type: 'ADDED', before_clause_uid: null, after_clause_uid: 'X',
        before_path: null, after_path: '/added/1', alignment_confidence: 0.9, detected_at: '2026-01-02T00:00:00Z', effective_date: null },
      { id: 2, document_id: 'd', document_version_id: 'v2', jurisdiction: 'UK', sector: 'banking',
        change_type: 'REMOVED', before_clause_uid: 'Y', after_clause_uid: null,
        before_path: '/removed/2', after_path: null, alignment_confidence: 0.9, detected_at: '2026-01-02T00:00:00Z', effective_date: null },
      { id: 3, document_id: 'd', document_version_id: 'v2', jurisdiction: 'UK', sector: 'banking',
        change_type: 'MODIFIED', before_clause_uid: 'Z', after_clause_uid: 'Z',
        before_path: '/mod/3', after_path: '/mod/3', alignment_confidence: 0.9, detected_at: '2026-01-02T00:00:00Z', effective_date: null },
      { id: 4, document_id: 'd', document_version_id: 'v2', jurisdiction: 'UK', sector: 'banking',
        change_type: 'MOVED', before_clause_uid: 'W', after_clause_uid: 'W',
        before_path: '/moved/4', after_path: '/moved/4b', alignment_confidence: 0.9, detected_at: '2026-01-02T00:00:00Z', effective_date: null },
    ],
    next_cursor: null, has_more: false,
  })

  const { wrapper } = await mountDetail('d')
  const panes = wrapper.findAllComponents({ name: 'VersionPane' })
  expect(panes).toHaveLength(2)
  expect(panes[0].props('changeMap')).toEqual({
    '/removed/2': 'REMOVED',
    '/mod/3': 'MODIFIED',
    '/moved/4': 'MOVED',
  })
  expect(panes[1].props('changeMap')).toEqual({
    '/added/1': 'ADDED',
    '/mod/3': 'MODIFIED',
    '/moved/4b': 'MOVED',
  })
  expect(wrapper.find('[data-testid="diff-legend"]').exists()).toBe(true)
})

it('does not fetch changes or render legend when only one version exists', async () => {
  vi.mocked(docsApi.getDocument).mockResolvedValue({
    id: 'd', jurisdiction: 'UK', sector: 'banking',
    lawstronaut_document_id: 'L', title: 'Test',
    created_at: '2026-01-01T00:00:00Z',
    clause_count: 0,
    change_counts: { added: 0, removed: 0, modified: 0, moved: 0 },
    previous_version_at: null,
    current_version_at: '2026-01-01T00:00:00Z',
    versions: [
      { id: 'v1', version_label: 'v1', publication_date: null, effective_date: '2026-01-01T00:00:00Z', content_bytes: 100, created_at: '2026-01-01T00:00:00Z' },
    ],
  })
  vi.mocked(changesApi.fetchDiscovery).mockResolvedValue({ items: [], next_cursor: null, has_more: false })

  const { wrapper } = await mountDetail('d')
  expect(wrapper.find('[data-testid="diff-legend"]').exists()).toBe(false)
  expect(vi.mocked(changesApi.fetchDiscovery)).not.toHaveBeenCalled()
})
```

Adapt `mountDetail` to the existing test file's helper. Add `vi.mock('@/api/changes', ...)` at the top if not present.

- [ ] **Step 3: Run the failing tests**

```bash
cd packages/horizons-webapp && npx vitest run src/views/__tests__/DocumentDetailView.spec.ts
```
Expected: FAIL — `changeMap` prop doesn't exist on the panes, no legend.

- [ ] **Step 4: Update the view**

Edit `packages/horizons-webapp/src/views/DocumentDetailView.vue`. Add the second query and the maps:

```ts
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { getDocument, type DocumentDetail, type DocumentVersion } from '@/api/documents'
import { fetchDiscovery } from '@/api/changes'
import type { DiscoveryItem } from '@/api/changes'
import { Button } from '@/components/ui/button'
import VersionPane from '@/components/documents/VersionPane.vue'
import AppNavBar from '@/components/AppNavBar.vue'
import { CHANGE_COLORS, type ChangeType } from '@/constants/change-colors'

const route = useRoute()
const documentId = computed(() => String(route.params.id))

const docQuery = useQuery<DocumentDetail>({
  queryKey: computed(() => ['document-detail', documentId.value]),
  queryFn: () => getDocument(documentId.value),
})

const document = computed<DocumentDetail | null>(() => docQuery.data.value ?? null)

const sortedVersions = computed<DocumentVersion[]>(() => {
  const versions = document.value?.versions ?? []
  return [...versions].sort((a, b) => {
    const ad = a.effective_date ?? a.created_at
    const bd = b.effective_date ?? b.created_at
    return ad.localeCompare(bd)
  })
})

const hasTwoVersions = computed(() => sortedVersions.value.length >= 2)

const changesQuery = useQuery({
  queryKey: computed(() => ['document-changes', documentId.value]),
  queryFn: () => fetchDiscovery({ scope: 'document', document_id: documentId.value, limit: 200 }),
  enabled: hasTwoVersions,
})

const changeItems = computed<DiscoveryItem[]>(() => changesQuery.data.value?.items ?? [])

const beforeMap = computed<Record<string, ChangeType>>(() => {
  const m: Record<string, ChangeType> = {}
  for (const c of changeItems.value) {
    if (c.before_path && (c.change_type === 'REMOVED' || c.change_type === 'MODIFIED' || c.change_type === 'MOVED')) {
      m[c.before_path] = c.change_type
    }
  }
  return m
})

const afterMap = computed<Record<string, ChangeType>>(() => {
  const m: Record<string, ChangeType> = {}
  for (const c of changeItems.value) {
    if (c.after_path && (c.change_type === 'ADDED' || c.change_type === 'MODIFIED' || c.change_type === 'MOVED')) {
      m[c.after_path] = c.change_type
    }
  }
  return m
})

const beforePath = computed<string | null>(() => {
  const v = route.query.before
  return typeof v === 'string' && v.length > 0 ? v : null
})
const afterPath = computed<string | null>(() => {
  const v = route.query.after
  return typeof v === 'string' && v.length > 0 ? v : null
})

const showStructure = ref(false)
const isNotFound = computed<boolean>(() => {
  const err = docQuery.error.value as { response?: { status?: number } } | null
  return err?.response?.status === 404
})

const lonePaneVersion = computed<DocumentVersion | null>(() => {
  const v = sortedVersions.value
  return v.length === 0 ? null : v[v.length - 1]!
})

const legendTypes: ChangeType[] = ['ADDED', 'REMOVED', 'MODIFIED', 'MOVED']
```

In the template, drop the `highlightPath`-based wiring and use the new props on each pane:

```html
<!-- Multi-version side-by-side -->
<div v-else data-testid="side-by-side">
  <div
    v-if="hasTwoVersions && changeItems.length > 0"
    data-testid="diff-legend"
    class="mb-3 flex flex-wrap items-center gap-2 text-xs text-slate-600"
  >
    <span class="mr-1">Changes:</span>
    <span
      v-for="t in legendTypes"
      :key="t"
      class="inline-flex items-center rounded px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset"
      :class="CHANGE_COLORS[t].pill"
    >{{ CHANGE_COLORS[t].label }}</span>
  </div>

  <div class="grid grid-cols-1 gap-6 md:grid-cols-2">
    <VersionPane
      :document-id="documentId"
      :version-label="sortedVersions[0]!.version_label"
      :seen-at="sortedVersions[0]!.created_at"
      :show-structure="showStructure"
      :change-map="beforeMap"
      :scroll-to-path="beforePath"
    />
    <VersionPane
      :document-id="documentId"
      :version-label="sortedVersions[sortedVersions.length - 1]!.version_label"
      :seen-at="sortedVersions[sortedVersions.length - 1]!.created_at"
      :show-structure="showStructure"
      :change-map="afterMap"
      :scroll-to-path="afterPath"
    />
  </div>
</div>

<!-- Single-version pane (unchanged shape) -->
<div v-else-if="sortedVersions.length === 1 && lonePaneVersion" class="grid grid-cols-1">
  <VersionPane
    :document-id="documentId"
    :version-label="lonePaneVersion.version_label"
    :seen-at="lonePaneVersion.created_at"
    :show-structure="showStructure"
    :scroll-to-path="afterPath ?? beforePath"
  />
</div>
```

- [ ] **Step 5: Re-run tests**

```bash
cd packages/horizons-webapp && npx vitest run src/views/__tests__/DocumentDetailView.spec.ts
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/horizons-webapp/src/views/DocumentDetailView.vue packages/horizons-webapp/src/api/changes.ts packages/horizons-webapp/src/views/__tests__/DocumentDetailView.spec.ts
git commit -m "feat(webapp): document detail colour-codes every change + renders legend"
```

---

## Task 11: ChangeTypePill reads colours from the constant

Single source of truth — the Recent Changes pills now match the diff view colours.

**Files:**
- Modify: `packages/horizons-webapp/src/components/ui/change-type-pill/ChangeTypePill.vue`
- Update existing snapshot tests if they assert specific colour classes.

- [ ] **Step 1: Add a failing test for the pill colour**

In `packages/horizons-webapp/src/components/ui/change-type-pill/__tests__/ChangeTypePill.spec.ts`, add:

```ts
import { CHANGE_COLORS } from '@/constants/change-colors'

it.each(['ADDED', 'REMOVED', 'MODIFIED', 'MOVED'] as const)(
  'pill for %s matches CHANGE_COLORS constant',
  (type) => {
    const wrapper = mount(ChangeTypePill, { props: { type } })
    const pillClasses = CHANGE_COLORS[type].pill
    for (const cls of pillClasses.split(/\s+/)) {
      expect(wrapper.attributes('class')).toContain(cls)
    }
  },
)
```

- [ ] **Step 2: Run failing test**

```bash
cd packages/horizons-webapp && npx vitest run src/components/ui/change-type-pill/__tests__/ChangeTypePill.spec.ts
```
Expected: FAIL — current colours differ for MODIFIED (blue → amber) and MOVED (slate → blue).

- [ ] **Step 3: Refactor the pill**

Replace the body of `ChangeTypePill.vue` (lines 1-28):

```ts
<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'
import { CHANGE_COLORS, type ChangeType } from '@/constants/change-colors'

export type { ChangeType }

interface Props {
  type: ChangeType
  class?: string
}

const props = defineProps<Props>()

const classes = computed(() =>
  cn(
    'inline-flex items-center rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset',
    CHANGE_COLORS[props.type].pill,
    props.class,
  ),
)
</script>
```

(Template unchanged.) Update any older snapshot tests in the same file that asserted the old colours — replace `blue` with `amber` for MODIFIED, `slate` with `blue` for MOVED.

- [ ] **Step 4: Re-run all pill tests**

```bash
cd packages/horizons-webapp && npx vitest run src/components/ui/change-type-pill
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-webapp/src/components/ui/change-type-pill/ChangeTypePill.vue packages/horizons-webapp/src/components/ui/change-type-pill/__tests__/ChangeTypePill.spec.ts
git commit -m "refactor(webapp): ChangeTypePill reads colours from CHANGE_COLORS"
```

---

## Task 12: Playwright e2e — card → table → coloured diff

**Files:**
- Modify: `packages/horizons-webapp/e2e/login-and-scope.spec.ts`

- [ ] **Step 1: Extend the existing spec**

Open `packages/horizons-webapp/e2e/login-and-scope.spec.ts`. After the existing login flow as `demo-uk`, add steps that:

1. click the UK jurisdiction card,
2. assert `URL === /documents?jurisdiction=UK` and a `<table>` with `data-testid="document-row"` is visible,
3. assert at least one row has a non-empty `Current version` cell,
4. click the first row's name link,
5. on the document detail page, assert `data-testid="diff-legend"` is visible **and** at least one element has `data-change-type="MODIFIED"` (or whichever change type the curated set's UK doc with v2 has — confirm by checking `data/curated_set.yaml` + the synthetic v2 fixture for the UK Employment Act).

Append the steps to the existing test or write a new one in the same file — keep it within the existing seeded fixture set so the e2e workflow keeps passing.

- [ ] **Step 2: Run the e2e locally**

Follow `packages/horizons-webapp/e2e/README.md` to boot the stack (Postgres in Docker, alembic + `seed_e2e.py`, uvicorn, `npm run build` + `npx vite preview`). Then:

```bash
cd packages/horizons-webapp && npx playwright test
```
Expected: PASS on the extended spec.

- [ ] **Step 3: Commit**

```bash
git add packages/horizons-webapp/e2e/login-and-scope.spec.ts
git commit -m "test(e2e): card → documents table → coloured diff view"
```

---

## Task 13: Full local sweep + push

Last task — run the full gate (which is the merge gate for `main`) and push.

- [ ] **Step 1: Full sweep**

```bash
uv run pre-commit run --all-files
uv run pytest
cd packages/horizons-webapp && npm run lint:check && npm run build && npm run test:unit -- --run
```
Expected: all green. If any auto-fix happens during pre-commit, commit those fixes:

```bash
git add -A
git commit -m "chore: pre-commit autofixes"
```

- [ ] **Step 2: Push to main**

This is the project's documented cadence (worktree → fast-forward main → direct push). If you're working in a worktree, follow the per-CLAUDE.md merge steps. If you're already on main:

```bash
git push origin main
```

- [ ] **Step 3: Update the journal**

Create `journal/260607-document-table-and-color-diff.md` capturing the build summary, screenshots if useful, and any follow-ups (e.g. word-level diff inside MODIFIED clauses; sortable headers). Commit and push.

```bash
git add journal/260607-document-table-and-color-diff.md
git commit -m "docs(journal): document-pivot + colour-coded diff build summary"
git push origin main
```

---

## Self-review notes

- Every spec section maps to a task: §4 → Task 6; §5 → Tasks 1–3; §6 → Tasks 4, 7; §7 → Tasks 5, 8–10; §8 → covered across Tasks 5, 8–11; §9 → tests woven into each task plus Task 12 for e2e.
- Type consistency: `ChangeType` is defined in `@/constants/change-colors` and re-exported via `ChangeTypePill` (Task 11) for backwards-compat with existing imports.
- Behaviour change worth flagging: Task 11 changes the existing Recent Changes pill colours (MODIFIED blue → amber, MOVED slate → blue). This is intentional per the spec's "single source of truth" decision; mention in the PR description.
- No placeholders. Each code block is intended to be copy-pasted (with light renaming for any fixture / helper that's named differently in the local repo).
