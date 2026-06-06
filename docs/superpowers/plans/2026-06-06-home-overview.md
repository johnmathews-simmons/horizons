# Home Overview Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a post-login home dashboard that summarises the caller's accessible corpus by jurisdiction and sector, makes subscription scoping visible at a glance, and routes drill-down clicks into `/changes` filtered by the card. Admins see the entire corpus on both the dashboard and `/changes`.

**Architecture:** Two thin layers. (1) A `SECURITY DEFINER` SQL function (`app_public.corpus_shape()`) returns the corpus matrix unscoped, consumed by a new `/v1/me/overview` endpoint that joins it against the caller's subscription scope. (2) A new `admin_or_app_session` FastAPI dependency assumes `admin_bypass` for admin callers (with one audit-log row per request) and is applied to the public primitives so admin's `/changes` view returns corpus-wide data. The Vue HomeView consumes the new endpoint, the ChangesView reads `jurisdiction` / `sector` from the route query and threads them through the existing discovery call.

**Tech Stack:** FastAPI · SQLAlchemy 2 / asyncpg · Postgres 18 with RLS · Alembic · Vue 3 + Pinia + vue-query · Vitest · Playwright · pytest + testcontainers.

**Spec:** `docs/superpowers/specs/260606-home-overview-design.md`

---

## File Structure

**New files:**
- `packages/horizons-core/migrations/versions/0013_corpus_shape_function.py` — Alembic migration adding `app_public.corpus_shape()` + grants.
- `packages/horizons-core/src/horizons_core/core/corpus.py` — `corpus_shape(session)` async helper that calls the function and returns typed rows.
- `packages/horizons-core/tests/test_corpus_shape.py` — integration test for the helper.
- `packages/horizons-api/src/horizons_api/deps/admin_or_app.py` — `admin_or_app_session` dependency.
- `packages/horizons-api/tests/test_admin_or_app_session.py` — integration test for the dep.
- `packages/horizons-api/tests/test_overview.py` — integration test for `/v1/me/overview`.
- `packages/horizons-webapp/src/api/overview.ts` — typed fetch wrapper for the new endpoint.
- `packages/horizons-webapp/src/composables/useMeOverview.ts` — vue-query composable.
- `packages/horizons-webapp/src/components/overview/JurisdictionCard.vue` — presentation component.
- `packages/horizons-webapp/src/components/overview/SectorCard.vue` — presentation component.
- `packages/horizons-webapp/src/components/overview/__tests__/JurisdictionCard.spec.ts`
- `packages/horizons-webapp/src/components/overview/__tests__/SectorCard.spec.ts`
- `packages/horizons-webapp/src/views/__tests__/HomeView.spec.ts`
- `journal/260606-home-overview.md` — session journal entry.

**Modified files:**
- `packages/horizons-api/src/horizons_api/routes/me.py` — add `/v1/me/overview` route.
- `packages/horizons-api/src/horizons_api/routes/primitives.py` — swap `session_for_request` for `admin_or_app_session` on discovery/temporal/differential.
- `packages/horizons-webapp/src/views/HomeView.vue` — full rebuild.
- `packages/horizons-webapp/src/views/ChangesView.vue` — read `jurisdiction` / `sector` from route, render "Filtered by" chip.
- `packages/horizons-webapp/src/views/__tests__/ChangesView.spec.ts` — extend if exists, otherwise add filter test inline in HomeView spec.
- `packages/horizons-webapp/src/composables/useChangeEvents.ts` — accept filter args, include in `queryKey`.
- `packages/horizons-webapp/src/api/changes.ts` — extend `DiscoveryParams`.
- `packages/horizons-webapp/e2e/login-and-scope.spec.ts` — extend with overview + admin assertions.
- `packages/horizons-core/src/horizons_core/db/roles.md` — document `app_public.corpus_shape` posture.
- `docs/api/horizons-primitives.md` — add `/v1/me/overview` section.
- `docs/api/endpoints.md` — regenerated.

---

## Task 1: Alembic migration — `app_public.corpus_shape()` function

**Files:**
- Create: `packages/horizons-core/migrations/versions/0013_corpus_shape_function.py`

- [ ] **Step 1: Write the migration**

```python
"""Add app_public.corpus_shape() — SECURITY DEFINER corpus-matrix view.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-06

The WU8.5 home dashboard needs the corpus-wide (jurisdiction, sector,
document_count) matrix even when the caller is a scoped client who
cannot read the underlying ``documents`` rows. Corpus *shape* is
non-sensitive catalog data — clients already know the subscription
token vocabulary — so we expose an unscoped count via a SECURITY
DEFINER function rather than escalating to admin_bypass per request
(which would force an audit row for every page load).

The function is owned by ``postgres`` (the only role that can read
``documents`` unscoped), so SECURITY DEFINER inherits that read.
EXECUTE is granted to ``api_app``; ``admin_bypass`` already reads
the documents table directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_public.corpus_shape()
        RETURNS TABLE (
            jurisdiction text,
            sector text,
            document_count bigint
        )
        LANGUAGE sql
        SECURITY DEFINER
        STABLE
        SET search_path = public, pg_temp
        AS $$
            SELECT jurisdiction, sector, COUNT(*)::bigint
            FROM public.documents
            GROUP BY jurisdiction, sector;
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION app_public.corpus_shape() FROM PUBLIC;")
    op.execute("GRANT EXECUTE ON FUNCTION app_public.corpus_shape() TO api_app;")
    op.execute("GRANT EXECUTE ON FUNCTION app_public.corpus_shape() TO admin_bypass;")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app_public.corpus_shape();")
```

- [ ] **Step 2: Run the migration locally**

Boot the local Postgres per `docs/runbooks/local-dev.md` (Postgres on 5432 with the standard role set), then:

```bash
uv run alembic upgrade head
```

Expected: migration `0013` applied cleanly; the prior head was `0012`.

- [ ] **Step 3: Smoke-check the function**

```bash
PGPASSWORD=postgres psql -h localhost -U postgres -d horizons \
  -c "SELECT * FROM app_public.corpus_shape() ORDER BY jurisdiction, sector;"
```

Expected: one row per `(jurisdiction, sector)` pair present in `documents`; empty result is acceptable if nothing has been seeded yet.

- [ ] **Step 4: Commit**

```bash
git add packages/horizons-core/migrations/versions/0013_corpus_shape_function.py
git commit -m "feat(db): add app_public.corpus_shape() SECURITY DEFINER function"
```

---

## Task 2: Core helper — `corpus_shape(session)`

**Files:**
- Create: `packages/horizons-core/src/horizons_core/core/corpus.py`
- Create: `packages/horizons-core/tests/test_corpus_shape.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/horizons-core/tests/test_corpus_shape.py
"""Integration test for ``corpus_shape`` — runs against a real Postgres."""

from __future__ import annotations

import uuid

import pytest
from horizons_core.core.corpus import CorpusShapeRow, corpus_shape
from horizons_core.db.models.documents import Document
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _insert_document(
    session: AsyncSession, *, jurisdiction: str, sector: str
) -> None:
    session.add(
        Document(
            jurisdiction=jurisdiction,
            sector=sector,
            lawstronaut_document_id=f"test-{uuid.uuid4()}",
            title=f"{jurisdiction}/{sector}",
        )
    )
    await session.flush()


async def test_corpus_shape_returns_grouped_counts(pg_session_admin: AsyncSession) -> None:
    """Seed three docs across two pairs; expect the rolled-up counts."""
    await _insert_document(pg_session_admin, jurisdiction="UK", sector="BANKING")
    await _insert_document(pg_session_admin, jurisdiction="UK", sector="BANKING")
    await _insert_document(pg_session_admin, jurisdiction="EU", sector="BANKING")

    rows = await corpus_shape(pg_session_admin)

    by_pair = {(r.jurisdiction, r.sector): r.document_count for r in rows}
    assert by_pair[("UK", "BANKING")] == 2
    assert by_pair[("EU", "BANKING")] == 1


async def test_corpus_shape_visible_under_api_app(
    pg_session_api_app: AsyncSession, pg_session_admin: AsyncSession
) -> None:
    """A scoped api_app session sees the same rows via SECURITY DEFINER."""
    await _insert_document(pg_session_admin, jurisdiction="UK", sector="BANKING")

    rows = await corpus_shape(pg_session_api_app)

    assert any(
        r.jurisdiction == "UK" and r.sector == "BANKING" and r.document_count >= 1
        for r in rows
    )


async def test_corpus_shape_dto_types(pg_session_admin: AsyncSession) -> None:
    """Returned rows are CorpusShapeRow instances with the documented fields."""
    await _insert_document(pg_session_admin, jurisdiction="IE", sector="BANKING")

    rows = await corpus_shape(pg_session_admin)

    assert rows
    row = rows[0]
    assert isinstance(row, CorpusShapeRow)
    assert isinstance(row.jurisdiction, str)
    assert isinstance(row.sector, str)
    assert isinstance(row.document_count, int)
```

- [ ] **Step 2: Run the failing test**

```bash
uv run pytest packages/horizons-core/tests/test_corpus_shape.py -v
```

Expected: FAIL with `ModuleNotFoundError: horizons_core.core.corpus`.

- [ ] **Step 3: Implement the helper**

```python
# packages/horizons-core/src/horizons_core/core/corpus.py
"""``corpus_shape(session)`` — the corpus-wide ``(jurisdiction, sector)`` matrix.

Reads through the ``app_public.corpus_shape()`` SECURITY DEFINER
function (migration 0013). Returns *every* pair present in
``documents``, regardless of the caller's subscription scope. Used by
``/v1/me/overview`` to render "not subscribed" cards on the home
dashboard.

Why SECURITY DEFINER: corpus shape is non-sensitive catalog data
(clients already know the subscription token vocabulary), and reading
it via ``admin_bypass`` per request would force a per-page-load audit
row. The function is owned by the database role that can read
``documents`` unscoped; ``api_app`` and ``admin_bypass`` are granted
``EXECUTE``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, String, select
from sqlalchemy.dialects.postgresql import BIGINT
from sqlalchemy.sql import func

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class CorpusShapeRow(BaseModel):
    """One ``(jurisdiction, sector, document_count)`` row."""

    model_config = ConfigDict(frozen=True)

    jurisdiction: str
    sector: str
    document_count: int


async def corpus_shape(session: AsyncSession) -> list[CorpusShapeRow]:
    """Return every ``(jurisdiction, sector)`` pair with its document count."""
    cs = (
        func.app_public.corpus_shape()
        .table_valued(
            Column("jurisdiction", String),
            Column("sector", String),
            Column("document_count", BIGINT),
        )
        .alias("cs")
    )
    rows = (
        await session.execute(
            select(cs.c.jurisdiction, cs.c.sector, cs.c.document_count)
        )
    ).all()
    return [
        CorpusShapeRow(
            jurisdiction=r.jurisdiction,
            sector=r.sector,
            document_count=int(r.document_count),
        )
        for r in rows
    ]
```

- [ ] **Step 4: Run the test to verify pass**

```bash
uv run pytest packages/horizons-core/tests/test_corpus_shape.py -v
```

Expected: 3 passed.

> **Conftest note:** if `pg_session_admin` / `pg_session_api_app` fixtures don't yet exist in this package, prefer adopting the fixtures used by `packages/horizons-core/tests/test_subscriptions.py` (or whichever existing integration test in the same package brackets sessions under both roles). The two fixtures must yield an `AsyncSession` bound under `admin_bypass` and `api_app` respectively. Do **not** invent a new fixture name if one already exists.

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-core/src/horizons_core/core/corpus.py \
        packages/horizons-core/tests/test_corpus_shape.py
git commit -m "feat(core): add corpus_shape() helper over app_public.corpus_shape()"
```

---

## Task 3: API dependency — `admin_or_app_session`

**Files:**
- Create: `packages/horizons-api/src/horizons_api/deps/admin_or_app.py`
- Create: `packages/horizons-api/tests/test_admin_or_app_session.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/horizons-api/tests/test_admin_or_app_session.py
"""``admin_or_app_session`` switches role + audits for admin callers.

Mirrors the role-switch contract from ``deps/session.py``: client
callers run as ``api_app`` (RLS narrows), admin callers run as
``admin_bypass`` (BYPASSRLS) and write one ``admin_access_log`` row
per request.
"""

from __future__ import annotations

import uuid

import pytest
from horizons_core.core.auth import Principal, Role
from horizons_core.db.models.admin_access_log import AdminAccessMode
from horizons_core.repos.admin_access_log import AdminAccessLogRepository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from horizons_api.deps.admin_or_app import admin_or_app_session

pytestmark = pytest.mark.integration


async def _consume(gen) -> AsyncSession:
    return await anext(gen)


async def test_client_principal_runs_as_api_app(client_principal: Principal) -> None:
    gen = admin_or_app_session(principal=client_principal, request_path="/v1/discovery")
    session = await _consume(gen)

    role = (await session.execute(text("SELECT current_user"))).scalar_one()
    assert role == "api_app"

    await gen.aclose()


async def test_admin_principal_runs_as_admin_bypass(admin_principal: Principal) -> None:
    gen = admin_or_app_session(principal=admin_principal, request_path="/v1/discovery")
    session = await _consume(gen)

    role = (await session.execute(text("SELECT current_user"))).scalar_one()
    assert role == "admin_bypass"

    await gen.aclose()


async def test_admin_request_writes_audit_row(
    admin_principal: Principal, pg_session_admin: AsyncSession
) -> None:
    """One admin_access_log row with mode=OPERATOR per request."""
    before = len(
        await AdminAccessLogRepository(pg_session_admin).list_for_admin(
            admin_principal.user_id
        )
    )

    gen = admin_or_app_session(principal=admin_principal, request_path="/v1/temporal")
    await _consume(gen)
    await gen.aclose()

    after_rows = await AdminAccessLogRepository(pg_session_admin).list_for_admin(
        admin_principal.user_id
    )
    assert len(after_rows) == before + 1
    newest = after_rows[0]
    assert newest.mode == AdminAccessMode.OPERATOR
    assert newest.reason == "/v1/temporal"
    assert newest.target_user_id is None


async def test_client_request_writes_no_audit_row(
    client_principal: Principal,
    admin_principal: Principal,
    pg_session_admin: AsyncSession,
) -> None:
    before = len(
        await AdminAccessLogRepository(pg_session_admin).list_for_admin(
            admin_principal.user_id
        )
    )

    gen = admin_or_app_session(principal=client_principal, request_path="/v1/discovery")
    await _consume(gen)
    await gen.aclose()

    after = len(
        await AdminAccessLogRepository(pg_session_admin).list_for_admin(
            admin_principal.user_id
        )
    )
    assert after == before
```

- [ ] **Step 2: Run the failing test**

```bash
uv run pytest packages/horizons-api/tests/test_admin_or_app_session.py -v
```

Expected: FAIL with `ModuleNotFoundError: horizons_api.deps.admin_or_app`.

- [ ] **Step 3: Implement the dependency**

```python
# packages/horizons-api/src/horizons_api/deps/admin_or_app.py
"""``admin_or_app_session`` — per-request bracket aware of admin role.

For ``role='client'`` callers: identical to ``session_for_request``
(``api_app`` role, ``app.user_id`` bound to the principal). RLS
narrows visibility to the caller's subscription scope.

For ``role='admin'`` callers (non-impersonation only): assume
``admin_bypass`` so the session reads every tenant, AND write one
``admin_access_log`` row with ``mode=OPERATOR`` and ``reason=<path>``
in a sibling transaction. The audit row commits before the working
session is yielded — see ``core.auth.admin._record_audit_row`` for
the rationale.

Apply this dependency to public-primitive routes whose RLS-narrowed
results would be empty (or wrong) for an admin caller: ``/v1/discovery``,
``/v1/temporal``, ``/v1/differential``, ``/v1/me/overview``. Plain
``session_for_request`` stays correct everywhere else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request
from horizons_core.core.auth import Principal, Role
from horizons_core.core.auth.admin import _record_audit_row
from horizons_core.db.models.admin_access_log import AdminAccessMode
from horizons_core.db.session import (
    get_engine,
    get_session,
    session_for_user,
    set_local_role,
)
import uuid

from horizons_api.deps.auth import authenticated_user

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession


async def admin_or_app_session(
    request: Request,
    principal: Annotated[Principal, Depends(authenticated_user)],
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a session that escalates to ``admin_bypass`` for admin callers."""
    if principal.role is Role.ADMIN:
        engine = get_engine()
        token_id = uuid.uuid4()
        await _record_audit_row(
            engine,
            mode=AdminAccessMode.OPERATOR,
            admin_id=principal.user_id,
            target_user_id=None,
            token_id=token_id,
            reason=request.url.path,
        )
        async with session_for_user(engine, principal.user_id) as session:
            await set_local_role(session, "admin_bypass")
            yield session
        return

    async with get_session(principal.user_id) as session:
        await set_local_role(session, "api_app")
        yield session
```

> **Test-only entry point.** The integration test imports `admin_or_app_session` as a plain async generator and passes `principal` + `request_path` directly (no FastAPI request). FastAPI's `Depends` shape requires a `Request`; the test bypasses that by calling the underlying function via a tiny shim. If the test imports break under that signature, expose a `_session_bracket(principal, *, request_path)` private function that holds the body of the dep and have the public `admin_or_app_session` call into it with `request_path=request.url.path`. The shim keeps the dep FastAPI-shaped while leaving the bracketed logic directly testable.

- [ ] **Step 4: If the test needs the shim, refactor**

If Step 2's test fails because the FastAPI signature blocks direct calls, split as below. Otherwise skip.

```python
# packages/horizons-api/src/horizons_api/deps/admin_or_app.py (refactor)
async def _session_bracket(
    principal: Principal, *, request_path: str
) -> AsyncGenerator[AsyncSession, None]:
    """Bracket body — testable without a FastAPI Request."""
    if principal.role is Role.ADMIN:
        engine = get_engine()
        token_id = uuid.uuid4()
        await _record_audit_row(
            engine,
            mode=AdminAccessMode.OPERATOR,
            admin_id=principal.user_id,
            target_user_id=None,
            token_id=token_id,
            reason=request_path,
        )
        async with session_for_user(engine, principal.user_id) as session:
            await set_local_role(session, "admin_bypass")
            yield session
        return

    async with get_session(principal.user_id) as session:
        await set_local_role(session, "api_app")
        yield session


async def admin_or_app_session(
    request: Request,
    principal: Annotated[Principal, Depends(authenticated_user)],
) -> AsyncGenerator[AsyncSession, None]:
    async for session in _session_bracket(principal, request_path=request.url.path):
        yield session
```

Update the test to import `_session_bracket` and call `_session_bracket(principal, request_path="/v1/discovery")` instead of `admin_or_app_session`.

- [ ] **Step 5: Run the tests to verify pass**

```bash
uv run pytest packages/horizons-api/tests/test_admin_or_app_session.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/horizons-api/src/horizons_api/deps/admin_or_app.py \
        packages/horizons-api/tests/test_admin_or_app_session.py
git commit -m "feat(api): add admin_or_app_session — escalates+audits for admin callers"
```

---

## Task 4: Apply `admin_or_app_session` to public primitives

**Files:**
- Modify: `packages/horizons-api/src/horizons_api/routes/primitives.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/horizons-api/tests/test_primitives.py` (or create the file if it doesn't yet exist). The asserts below mirror the design's "admin sees corpus-wide rows + audit row written" contract.

```python
# packages/horizons-api/tests/test_primitives.py (append)
import pytest
from horizons_core.repos.admin_access_log import AdminAccessLogRepository
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def test_admin_discovery_returns_corpus_wide(
    api_client_admin: AsyncClient, seeded_change_events_multi_tenant: None
) -> None:
    """Admin's /v1/discovery returns events across every tenant's scope."""
    response = await api_client_admin.get("/v1/discovery", params={"scope": "corpus"})
    assert response.status_code == 200
    items = response.json()["items"]
    jurisdictions = {item["jurisdiction"] for item in items}
    assert len(jurisdictions) >= 2  # multiple tenants visible


async def test_admin_discovery_writes_audit_row(
    api_client_admin: AsyncClient,
    admin_user_id,
    pg_session_admin: AsyncSession,
) -> None:
    """One admin_access_log row per admin request, reason=path."""
    before = await AdminAccessLogRepository(pg_session_admin).list_for_admin(admin_user_id)
    await api_client_admin.get("/v1/discovery", params={"scope": "corpus"})
    after = await AdminAccessLogRepository(pg_session_admin).list_for_admin(admin_user_id)
    assert len(after) == len(before) + 1
    assert after[0].reason == "/v1/discovery"


async def test_client_discovery_still_scoped(
    api_client_uk: AsyncClient, seeded_change_events_multi_tenant: None
) -> None:
    """A UK-only client sees only UK events under the new dependency."""
    response = await api_client_uk.get("/v1/discovery", params={"scope": "corpus"})
    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    assert all(item["jurisdiction"] == "UK" for item in items)
```

> **Fixture note:** if `api_client_admin`, `api_client_uk`, `admin_user_id`, or `seeded_change_events_multi_tenant` don't yet exist, mirror the fixtures used by the existing `tests/test_admin_subscriptions.py` (which already brackets an admin HTTP client). Reuse over invent.

- [ ] **Step 2: Run the failing tests**

```bash
uv run pytest packages/horizons-api/tests/test_primitives.py -k "admin_discovery or client_discovery_still_scoped" -v
```

Expected: admin tests FAIL — admin currently runs under `api_app` and gets zero rows.

- [ ] **Step 3: Swap the dependency in the primitives module**

Open `packages/horizons-api/src/horizons_api/routes/primitives.py` and at the top of the file replace:

```python
from horizons_api.deps import authenticated_user, session_for_request
```

with:

```python
from horizons_api.deps import authenticated_user, session_for_request  # noqa: F401
from horizons_api.deps.admin_or_app import admin_or_app_session
```

For each of the four primitive route handlers (`discovery`, `temporal`, `differential`, `differential_by_id`), replace the session dependency:

```python
session: Annotated[AsyncSession, Depends(session_for_request)],
```

with:

```python
session: Annotated[AsyncSession, Depends(admin_or_app_session)],
```

Leave every other parameter, body, and helper untouched.

- [ ] **Step 4: Run the full primitives test suite**

```bash
uv run pytest packages/horizons-api/tests/test_primitives.py -v
```

Expected: all green, including the new admin-aware tests AND the prior client-scope tests.

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-api/src/horizons_api/routes/primitives.py \
        packages/horizons-api/tests/test_primitives.py
git commit -m "feat(api): primitives use admin_or_app_session so admins see corpus"
```

---

## Task 5: New endpoint — `GET /v1/me/overview`

**Files:**
- Modify: `packages/horizons-api/src/horizons_api/routes/me.py`
- Create: `packages/horizons-api/tests/test_overview.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/horizons-api/tests/test_overview.py
"""``GET /v1/me/overview`` — corpus matrix + subscribed flags per role."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_uk_client_sees_one_subscribed_jurisdiction(
    api_client_uk: AsyncClient, seeded_curated_set: None
) -> None:
    response = await api_client_uk.get("/v1/me/overview")
    assert response.status_code == 200
    body = response.json()

    assert body["is_admin"] is False

    juris = body["jurisdictions"]
    subscribed = [j for j in juris if j["subscribed"]]
    assert len(subscribed) == 1
    assert subscribed[0]["code"] == "UK"

    # All cards from the corpus are present (curated set covers >= 8 jurisdictions).
    codes = {j["code"] for j in juris}
    assert {"UK", "IE", "EU"}.issubset(codes)


async def test_eu_client_sees_eu_two_docs(
    api_client_eu: AsyncClient, seeded_curated_set: None
) -> None:
    response = await api_client_eu.get("/v1/me/overview")
    body = response.json()

    eu = next(j for j in body["jurisdictions"] if j["code"] == "EU")
    assert eu["subscribed"] is True
    assert eu["document_count"] == 2

    banking = next(s for s in body["sectors"] if s["code"] == "BANKING")
    assert banking["subscribed"] is True


async def test_admin_sees_full_corpus_no_badges(
    api_client_admin: AsyncClient, seeded_curated_set: None
) -> None:
    response = await api_client_admin.get("/v1/me/overview")
    body = response.json()

    assert body["is_admin"] is True
    assert all(j["subscribed"] for j in body["jurisdictions"])
    assert all(s["subscribed"] for s in body["sectors"])

    totals = body["totals"]
    assert totals["documents"] == sum(j["document_count"] for j in body["jurisdictions"])
    assert totals["jurisdictions"] == len(body["jurisdictions"])
    assert totals["sectors"] == len(body["sectors"])
    assert totals["subscribed_jurisdictions"] == totals["jurisdictions"]
    assert totals["subscribed_sectors"] == totals["sectors"]


async def test_overview_response_is_no_store(
    api_client_uk: AsyncClient, seeded_curated_set: None
) -> None:
    response = await api_client_uk.get("/v1/me/overview")
    assert "no-store" in response.headers["cache-control"].lower()


async def test_overview_lists_sorted_ascending(
    api_client_admin: AsyncClient, seeded_curated_set: None
) -> None:
    body = (await api_client_admin.get("/v1/me/overview")).json()
    juris_codes = [j["code"] for j in body["jurisdictions"]]
    sector_codes = [s["code"] for s in body["sectors"]]
    assert juris_codes == sorted(juris_codes)
    assert sector_codes == sorted(sector_codes)
```

> **Fixture note:** `seeded_curated_set` should seed the project's standard curated-set fixture (10 docs, see `data/curated_set.yaml`). If a fixture already does this in another test module, reuse it. Otherwise add one to the integration `conftest.py` that calls into `scripts/seed_curated_set.py`'s public helper.

- [ ] **Step 2: Run the failing test**

```bash
uv run pytest packages/horizons-api/tests/test_overview.py -v
```

Expected: 5 FAIL with 404 (route not registered).

- [ ] **Step 3: Add response models + the route**

Append to `packages/horizons-api/src/horizons_api/routes/me.py`:

```python
# Imports already include APIRouter, Depends, Response, AsyncSession, Annotated.
# Add new imports at the top of the file:
from horizons_core.core.corpus import corpus_shape
from horizons_core.core.subscriptions import current_scope_pairs

from horizons_api.deps.admin_or_app import admin_or_app_session


class JurisdictionOverviewItem(BaseModel):
    """One jurisdiction card on the home dashboard."""

    model_config = ConfigDict(frozen=True)

    code: str
    document_count: int
    subscribed: bool


class SectorOverviewItem(BaseModel):
    """One sector card on the home dashboard."""

    model_config = ConfigDict(frozen=True)

    code: str
    document_count: int
    subscribed: bool


class OverviewTotals(BaseModel):
    """Top-of-dashboard summary numbers."""

    model_config = ConfigDict(frozen=True)

    documents: int
    jurisdictions: int
    sectors: int
    subscribed_jurisdictions: int
    subscribed_sectors: int


class OverviewResponse(BaseModel):
    """Wire shape for ``GET /v1/me/overview``."""

    model_config = ConfigDict(frozen=True)

    is_admin: bool
    totals: OverviewTotals
    jurisdictions: list[JurisdictionOverviewItem]
    sectors: list[SectorOverviewItem]


@router.get("/me/overview", response_model=OverviewResponse)
async def get_overview(
    response: Response,
    principal: Annotated[Principal, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(admin_or_app_session)],
) -> OverviewResponse:
    """Return the corpus-wide jurisdiction × sector matrix + subscribed flags."""
    response.headers["Cache-Control"] = "private, no-store"

    matrix = await corpus_shape(session)
    is_admin = principal.role.value == "admin"
    scope_pairs = set() if is_admin else await current_scope_pairs(session)

    subscribed_jurisdictions = {j for (j, _) in scope_pairs}
    subscribed_sectors = {s for (_, s) in scope_pairs}

    juris_counts: dict[str, int] = {}
    sector_counts: dict[str, int] = {}
    for row in matrix:
        juris_counts[row.jurisdiction] = juris_counts.get(row.jurisdiction, 0) + row.document_count
        sector_counts[row.sector] = sector_counts.get(row.sector, 0) + row.document_count

    jurisdictions = [
        JurisdictionOverviewItem(
            code=code,
            document_count=count,
            subscribed=is_admin or code in subscribed_jurisdictions,
        )
        for code, count in sorted(juris_counts.items())
    ]
    sectors = [
        SectorOverviewItem(
            code=code,
            document_count=count,
            subscribed=is_admin or code in subscribed_sectors,
        )
        for code, count in sorted(sector_counts.items())
    ]

    total_documents = sum(row.document_count for row in matrix)
    totals = OverviewTotals(
        documents=total_documents,
        jurisdictions=len(juris_counts),
        sectors=len(sector_counts),
        subscribed_jurisdictions=(
            len(juris_counts) if is_admin else len(subscribed_jurisdictions & juris_counts.keys())
        ),
        subscribed_sectors=(
            len(sector_counts) if is_admin else len(subscribed_sectors & sector_counts.keys())
        ),
    )

    return OverviewResponse(
        is_admin=is_admin,
        totals=totals,
        jurisdictions=jurisdictions,
        sectors=sectors,
    )
```

- [ ] **Step 4: Run the tests to verify pass**

```bash
uv run pytest packages/horizons-api/tests/test_overview.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/horizons-api/src/horizons_api/routes/me.py \
        packages/horizons-api/tests/test_overview.py
git commit -m "feat(api): add GET /v1/me/overview — corpus matrix + subscribed flags"
```

---

## Task 6: Docs — regenerate endpoints.md, update primitives doc

**Files:**
- Modify: `docs/api/horizons-primitives.md`
- Modify (regenerated): `docs/api/endpoints.md`

- [ ] **Step 1: Add a section to `horizons-primitives.md`**

After the existing primitives sections, add a new section. Open `docs/api/horizons-primitives.md` and append:

```markdown
## `GET /v1/me/overview` — home dashboard summary

The home dashboard's data source. Returns the full corpus matrix
(every `(jurisdiction, sector)` pair present in `documents`, with
counts) plus a `subscribed` flag per jurisdiction and per sector
indicating whether the caller's subscription covers it.

Admin callers see every pair flagged `subscribed=true`; the body
also sets `is_admin=true`. The route reads corpus shape through the
unscoped `app_public.corpus_shape()` function (see migration 0013
and `db/roles.md`); per-row corpus content remains RLS-scoped on
every other route.

Response:

```json
{
  "is_admin": false,
  "totals": {
    "documents": 10,
    "jurisdictions": 8,
    "sectors": 5,
    "subscribed_jurisdictions": 1,
    "subscribed_sectors": 1
  },
  "jurisdictions": [
    { "code": "IE", "document_count": 1, "subscribed": false },
    { "code": "UK", "document_count": 1, "subscribed": true }
  ],
  "sectors": [
    { "code": "BANKING", "document_count": 5, "subscribed": true },
    { "code": "employment", "document_count": 2, "subscribed": false }
  ]
}
```

Lists are sorted by `code` ascending. `Cache-Control: private, no-store`.

Why this isn't `/v1/me`: keeping the dashboard view separate from the
identity payload means the home page can stale-cache the overview
independently of the principal, and `/v1/me` stays small for clients
that only need user identity.
```

- [ ] **Step 2: Regenerate endpoints.md**

```bash
uv run python packages/horizons-api/scripts/regen_endpoints_md.py
```

Expected: `docs/api/endpoints.md` updated to include `GET /v1/me/overview`.

- [ ] **Step 3: Commit**

```bash
git add docs/api/horizons-primitives.md docs/api/endpoints.md
git commit -m "docs(api): document GET /v1/me/overview"
```

---

## Task 7: Update `db/roles.md` for `app_public.corpus_shape`

**Files:**
- Modify: `packages/horizons-core/src/horizons_core/db/roles.md`

- [ ] **Step 1: Add a Functions row**

Open the file. Find the table titled `## Function grants` (or the closest function-grants table). Add a row:

```markdown
| `app_public.corpus_shape()` | `api_app` (EXECUTE), `admin_bypass` (EXECUTE) | SECURITY DEFINER. Returns `(jurisdiction, sector, document_count)` for the whole corpus unscoped. Powers `GET /v1/me/overview`'s "Not subscribed" cards; corpus *shape* is non-sensitive catalog data, so the function bypasses RLS without an audit row. Per-row corpus content remains scoped on every other route. |
```

If the structure of the existing function-grants table doesn't match this row shape exactly, follow that table's columns instead — keep `app_public.corpus_shape()` in the leftmost cell and the rationale in the rightmost cell.

- [ ] **Step 2: Commit**

```bash
git add packages/horizons-core/src/horizons_core/db/roles.md
git commit -m "docs(db): document app_public.corpus_shape role grants"
```

---

## Task 8: Webapp API client + composable

**Files:**
- Create: `packages/horizons-webapp/src/api/overview.ts`
- Create: `packages/horizons-webapp/src/composables/useMeOverview.ts`

- [ ] **Step 1: Add the API client**

```ts
// packages/horizons-webapp/src/api/overview.ts
import { apiClient } from './client'

export interface JurisdictionOverviewItem {
  code: string
  document_count: number
  subscribed: boolean
}

export interface SectorOverviewItem {
  code: string
  document_count: number
  subscribed: boolean
}

export interface OverviewTotals {
  documents: number
  jurisdictions: number
  sectors: number
  subscribed_jurisdictions: number
  subscribed_sectors: number
}

export interface OverviewResponse {
  is_admin: boolean
  totals: OverviewTotals
  jurisdictions: JurisdictionOverviewItem[]
  sectors: SectorOverviewItem[]
}

export async function fetchOverview(): Promise<OverviewResponse> {
  const response = await apiClient.get<OverviewResponse>('/v1/me/overview')
  return response.data
}
```

- [ ] **Step 2: Add the composable**

```ts
// packages/horizons-webapp/src/composables/useMeOverview.ts
import { useQuery } from '@tanstack/vue-query'
import { fetchOverview, type OverviewResponse } from '@/api/overview'

const STALE_MS = 30_000

export function useMeOverview() {
  return useQuery<OverviewResponse>({
    queryKey: ['me', 'overview'],
    queryFn: fetchOverview,
    staleTime: STALE_MS,
  })
}
```

- [ ] **Step 3: Verify TypeScript + lint pass**

```bash
cd packages/horizons-webapp && npm run lint:check && npx vue-tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add packages/horizons-webapp/src/api/overview.ts \
        packages/horizons-webapp/src/composables/useMeOverview.ts
git commit -m "feat(webapp): add overview API client + useMeOverview composable"
```

---

## Task 9: Card components — `JurisdictionCard` + `SectorCard`

**Files:**
- Create: `packages/horizons-webapp/src/components/overview/JurisdictionCard.vue`
- Create: `packages/horizons-webapp/src/components/overview/SectorCard.vue`
- Create: `packages/horizons-webapp/src/components/overview/__tests__/JurisdictionCard.spec.ts`
- Create: `packages/horizons-webapp/src/components/overview/__tests__/SectorCard.spec.ts`

- [ ] **Step 1: Write the failing JurisdictionCard test**

```ts
// packages/horizons-webapp/src/components/overview/__tests__/JurisdictionCard.spec.ts
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import JurisdictionCard from '../JurisdictionCard.vue'

describe('JurisdictionCard', () => {
  it('renders the code and document count', () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'UK', documentCount: 3, subscribed: true },
    })
    expect(wrapper.text()).toContain('UK')
    expect(wrapper.text()).toContain('3')
  })

  it('shows the Not subscribed badge when not subscribed', () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'IE', documentCount: 1, subscribed: false },
    })
    expect(wrapper.text()).toContain('Not subscribed')
  })

  it('emits select on click when subscribed', async () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'UK', documentCount: 3, subscribed: true },
    })
    await wrapper.trigger('click')
    expect(wrapper.emitted('select')).toEqual([['UK']])
  })

  it('does not emit select on click when not subscribed', async () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'IE', documentCount: 1, subscribed: false },
    })
    await wrapper.trigger('click')
    expect(wrapper.emitted('select')).toBeUndefined()
  })

  it('sets a tooltip on the not-subscribed state', () => {
    const wrapper = mount(JurisdictionCard, {
      props: { code: 'IE', documentCount: 1, subscribed: false },
    })
    expect(wrapper.attributes('title')).toBe('Subscribe to view')
  })
})
```

- [ ] **Step 2: Run the failing test**

```bash
cd packages/horizons-webapp && npm run test:unit -- --run JurisdictionCard
```

Expected: FAIL — component does not exist.

- [ ] **Step 3: Implement JurisdictionCard**

```vue
<!-- packages/horizons-webapp/src/components/overview/JurisdictionCard.vue -->
<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  code: string
  documentCount: number
  subscribed: boolean
}>()

const emit = defineEmits<{ select: [code: string] }>()

const title = computed(() => (props.subscribed ? '' : 'Subscribe to view'))

function onClick(): void {
  if (props.subscribed) emit('select', props.code)
}
</script>

<template>
  <button
    type="button"
    :title="title"
    :disabled="!subscribed"
    :class="[
      'flex w-full flex-col items-start rounded-md border p-4 text-left transition',
      subscribed
        ? 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50 cursor-pointer'
        : 'border-slate-100 bg-slate-50 text-slate-400 cursor-not-allowed',
    ]"
    data-testid="jurisdiction-card"
    :data-code="code"
    :data-subscribed="subscribed"
    @click="onClick"
  >
    <span class="text-lg font-semibold tracking-tight">{{ code }}</span>
    <span class="mt-1 text-sm">
      {{ documentCount }} {{ documentCount === 1 ? 'document' : 'documents' }}
    </span>
    <span
      v-if="!subscribed"
      class="mt-2 inline-flex items-center rounded-full bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-600"
    >
      Not subscribed
    </span>
  </button>
</template>
```

- [ ] **Step 4: Repeat for SectorCard**

Test file `SectorCard.spec.ts` is identical to `JurisdictionCard.spec.ts` except `JurisdictionCard` → `SectorCard`, `code: 'UK'` → `code: 'BANKING'`, `code: 'IE'` → `code: 'employment'`, and `data-testid="jurisdiction-card"` → `data-testid="sector-card"`. Component file `SectorCard.vue` is identical to `JurisdictionCard.vue` except `data-testid="jurisdiction-card"` → `data-testid="sector-card"`.

- [ ] **Step 5: Run tests to verify pass**

```bash
cd packages/horizons-webapp && npm run test:unit -- --run JurisdictionCard SectorCard
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add packages/horizons-webapp/src/components/overview/
git commit -m "feat(webapp): add JurisdictionCard + SectorCard overview components"
```

---

## Task 10: Rebuild `HomeView`

**Files:**
- Modify: `packages/horizons-webapp/src/views/HomeView.vue`
- Create: `packages/horizons-webapp/src/views/__tests__/HomeView.spec.ts`

- [ ] **Step 1: Write the failing test**

```ts
// packages/horizons-webapp/src/views/__tests__/HomeView.spec.ts
import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import HomeView from '../HomeView.vue'

vi.mock('@/api/overview', () => ({
  fetchOverview: vi.fn().mockResolvedValue({
    is_admin: false,
    totals: {
      documents: 10,
      jurisdictions: 8,
      sectors: 5,
      subscribed_jurisdictions: 1,
      subscribed_sectors: 1,
    },
    jurisdictions: [
      { code: 'IE', document_count: 1, subscribed: false },
      { code: 'UK', document_count: 1, subscribed: true },
    ],
    sectors: [
      { code: 'BANKING', document_count: 5, subscribed: true },
      { code: 'employment', document_count: 2, subscribed: false },
    ],
  }),
}))

const routes = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/changes', name: 'changes', component: { template: '<div/>' } },
  { path: '/login', name: 'login', component: { template: '<div/>' } },
  { path: '/watchlists', name: 'watchlists', component: { template: '<div/>' } },
]

async function mountHome() {
  setActivePinia(createPinia())
  const router = createRouter({ history: createMemoryHistory(), routes })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = mount(HomeView, {
    global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
  })
  await flushPromises()
  return { wrapper, router }
}

describe('HomeView', () => {
  it('renders one jurisdiction card per code in the response', async () => {
    const { wrapper } = await mountHome()
    const cards = wrapper.findAll('[data-testid="jurisdiction-card"]')
    expect(cards).toHaveLength(2)
    const codes = cards.map((c) => c.attributes('data-code'))
    expect(codes).toEqual(['IE', 'UK'])
  })

  it('marks not-subscribed cards as disabled', async () => {
    const { wrapper } = await mountHome()
    const ie = wrapper.find('[data-testid="jurisdiction-card"][data-code="IE"]')
    expect(ie.attributes('data-subscribed')).toBe('false')
    const uk = wrapper.find('[data-testid="jurisdiction-card"][data-code="UK"]')
    expect(uk.attributes('data-subscribed')).toBe('true')
  })

  it('clicking a subscribed jurisdiction navigates to /changes?jurisdiction=UK', async () => {
    const { wrapper, router } = await mountHome()
    const push = vi.spyOn(router, 'push')
    await wrapper.find('[data-testid="jurisdiction-card"][data-code="UK"]').trigger('click')
    expect(push).toHaveBeenCalledWith({ name: 'changes', query: { jurisdiction: 'UK' } })
  })

  it('clicking a not-subscribed jurisdiction does not navigate', async () => {
    const { wrapper, router } = await mountHome()
    const push = vi.spyOn(router, 'push')
    await wrapper.find('[data-testid="jurisdiction-card"][data-code="IE"]').trigger('click')
    expect(push).not.toHaveBeenCalled()
  })

  it('clicking a subscribed sector navigates to /changes?sector=BANKING', async () => {
    const { wrapper, router } = await mountHome()
    const push = vi.spyOn(router, 'push')
    await wrapper.find('[data-testid="sector-card"][data-code="BANKING"]').trigger('click')
    expect(push).toHaveBeenCalledWith({ name: 'changes', query: { sector: 'BANKING' } })
  })

  it('shows the summary numbers from totals', async () => {
    const { wrapper } = await mountHome()
    const text = wrapper.text()
    expect(text).toMatch(/Jurisdictions/)
    expect(text).toMatch(/1\s*\/\s*8/)
    expect(text).toMatch(/Sectors/)
    expect(text).toMatch(/1\s*\/\s*5/)
  })
})
```

- [ ] **Step 2: Run the failing test**

```bash
cd packages/horizons-webapp && npm run test:unit -- --run HomeView
```

Expected: FAIL — HomeView renders the old placeholder, no cards present.

- [ ] **Step 3: Rebuild HomeView**

Replace the entire contents of `packages/horizons-webapp/src/views/HomeView.vue`:

```vue
<script setup lang="ts">
import { RouterLink, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useMeOverview } from '@/composables/useMeOverview'
import { Button } from '@/components/ui/button'
import JurisdictionCard from '@/components/overview/JurisdictionCard.vue'
import SectorCard from '@/components/overview/SectorCard.vue'

const auth = useAuthStore()
const router = useRouter()
const overview = useMeOverview()

async function onSignOut(): Promise<void> {
  await auth.logout()
  await router.push({ name: 'login' })
}

function goToJurisdiction(code: string): void {
  router.push({ name: 'changes', query: { jurisdiction: code } })
}

function goToSector(code: string): void {
  router.push({ name: 'changes', query: { sector: code } })
}
</script>

<template>
  <main class="min-h-screen bg-slate-50">
    <header class="border-b border-slate-200 bg-white">
      <div class="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <span class="text-lg font-semibold tracking-tight text-slate-900">Horizons</span>
        <div class="flex items-center gap-3 text-sm">
          <RouterLink
            to="/changes"
            class="rounded-md px-3 py-1.5 text-slate-700 hover:bg-slate-100"
            data-testid="nav-changes"
          >
            Browse recent changes
          </RouterLink>
          <RouterLink
            to="/watchlists"
            class="rounded-md px-3 py-1.5 text-slate-700 hover:bg-slate-100"
            data-testid="nav-watchlists"
          >
            Manage watchlists
          </RouterLink>
          <span v-if="auth.principal" data-testid="user-email" class="text-slate-600">
            {{ auth.principal.email }}
          </span>
          <Button variant="outline" size="sm" data-testid="sign-out" @click="onSignOut">
            Sign out
          </Button>
        </div>
      </div>
    </header>

    <section class="mx-auto max-w-6xl px-6 py-10">
      <h1 class="text-2xl font-semibold tracking-tight text-slate-900">Your corpus</h1>
      <p class="mt-2 text-slate-600">
        An overview of the regulatory documents your subscription covers.
      </p>

      <div v-if="overview.isPending.value" class="mt-8 text-slate-500" data-testid="overview-loading">
        Loading…
      </div>
      <div v-else-if="overview.isError.value" class="mt-8 rounded-md border border-red-200 bg-red-50 p-4 text-red-700" data-testid="overview-error">
        Couldn't load your overview. Try refreshing.
      </div>
      <template v-else-if="overview.data.value">
        <!-- Summary row -->
        <div class="mt-8 grid grid-cols-1 gap-4 md:grid-cols-2" data-testid="overview-summary">
          <template v-if="overview.data.value.is_admin">
            <div class="rounded-md border border-slate-200 bg-white p-4">
              <div class="text-sm text-slate-500">Access</div>
              <div class="mt-1 text-xl font-semibold text-slate-900">Full corpus</div>
              <div class="mt-1 text-sm text-slate-600">
                {{ overview.data.value.totals.documents }} documents across
                {{ overview.data.value.totals.jurisdictions }} jurisdictions and
                {{ overview.data.value.totals.sectors }} sectors
              </div>
            </div>
          </template>
          <template v-else>
            <div class="rounded-md border border-slate-200 bg-white p-4">
              <div class="text-sm text-slate-500">Jurisdictions</div>
              <div class="mt-1 text-xl font-semibold text-slate-900">
                {{ overview.data.value.totals.subscribed_jurisdictions }} /
                {{ overview.data.value.totals.jurisdictions }}
              </div>
            </div>
            <div class="rounded-md border border-slate-200 bg-white p-4">
              <div class="text-sm text-slate-500">Sectors</div>
              <div class="mt-1 text-xl font-semibold text-slate-900">
                {{ overview.data.value.totals.subscribed_sectors }} /
                {{ overview.data.value.totals.sectors }}
              </div>
            </div>
          </template>
        </div>

        <!-- Jurisdictions -->
        <h2 class="mt-10 text-lg font-semibold tracking-tight text-slate-900">Jurisdictions</h2>
        <div class="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <JurisdictionCard
            v-for="j in overview.data.value.jurisdictions"
            :key="j.code"
            :code="j.code"
            :document-count="j.document_count"
            :subscribed="j.subscribed"
            @select="goToJurisdiction"
          />
        </div>

        <!-- Sectors -->
        <h2 class="mt-10 text-lg font-semibold tracking-tight text-slate-900">Sectors</h2>
        <div class="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3">
          <SectorCard
            v-for="s in overview.data.value.sectors"
            :key="s.code"
            :code="s.code"
            :document-count="s.document_count"
            :subscribed="s.subscribed"
            @select="goToSector"
          />
        </div>
      </template>
    </section>
  </main>
</template>
```

- [ ] **Step 4: Run the test to verify pass**

```bash
cd packages/horizons-webapp && npm run test:unit -- --run HomeView
```

Expected: 6 passed.

- [ ] **Step 5: Boot the dev server + visually inspect**

In one shell:

```bash
cd packages/horizons-webapp && npm run dev
```

In another, ensure the API is running (`docs/runbooks/local-dev.md`). Visit `http://localhost:5173`, log in as `demo-uk@demo.example.com`, and confirm:
- One "UK" card highlighted, others muted with "Not subscribed".
- Summary row shows "1 / 8" jurisdictions and "1 / 5" sectors.
- Clicking the UK card navigates to `/changes?jurisdiction=UK`.

Log out and log in as `admin-demo@demo.example.com`. Confirm:
- Summary row shows "Full corpus" + total counts.
- Every card is enabled, no badges.

- [ ] **Step 6: Commit**

```bash
git add packages/horizons-webapp/src/views/HomeView.vue \
        packages/horizons-webapp/src/views/__tests__/HomeView.spec.ts
git commit -m "feat(webapp): rebuild HomeView as subscription-scoped corpus overview"
```

---

## Task 11: Wire `jurisdiction` + `sector` filter into `/changes`

**Files:**
- Modify: `packages/horizons-webapp/src/api/changes.ts`
- Modify: `packages/horizons-webapp/src/composables/useChangeEvents.ts`
- Modify: `packages/horizons-webapp/src/views/ChangesView.vue`

- [ ] **Step 1: Extend the API client**

In `packages/horizons-webapp/src/api/changes.ts`, replace the `DiscoveryParams` interface and `fetchDiscovery` function with:

```ts
export interface DiscoveryParams {
  cursor?: string | null
  limit?: number
  jurisdiction?: string | null
  sector?: string | null
}

export async function fetchDiscovery(params: DiscoveryParams = {}): Promise<DiscoveryPage> {
  const search: Record<string, string | number> = { scope: 'corpus' }
  if (params.limit !== undefined) search.limit = params.limit
  if (params.cursor) search.cursor = params.cursor
  if (params.jurisdiction) search.jurisdiction = params.jurisdiction
  if (params.sector) search.sector = params.sector
  const response = await apiClient.get<DiscoveryPage>('/v1/discovery', { params: search })
  return response.data
}
```

- [ ] **Step 2: Extend the composable**

Replace `packages/horizons-webapp/src/composables/useChangeEvents.ts`:

```ts
import { computed, type MaybeRefOrGetter, toValue } from 'vue'
import { useInfiniteQuery } from '@tanstack/vue-query'
import { fetchDiscovery, type DiscoveryPage } from '@/api/changes'

const DEFAULT_LIMIT = 50

export interface ChangeEventFilters {
  jurisdiction?: string | null
  sector?: string | null
}

export function useChangeEvents(filters?: MaybeRefOrGetter<ChangeEventFilters>) {
  const resolved = computed(() => toValue(filters) ?? {})
  return useInfiniteQuery({
    queryKey: computed(() => [
      'changes',
      'discovery',
      'corpus',
      resolved.value.jurisdiction ?? null,
      resolved.value.sector ?? null,
    ]),
    queryFn: ({ pageParam }: { pageParam: string | null }) =>
      fetchDiscovery({
        cursor: pageParam,
        limit: DEFAULT_LIMIT,
        jurisdiction: resolved.value.jurisdiction ?? null,
        sector: resolved.value.sector ?? null,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage: DiscoveryPage): string | null =>
      lastPage.has_more && lastPage.next_cursor ? lastPage.next_cursor : null,
  })
}
```

- [ ] **Step 3: Update ChangesView**

In `packages/horizons-webapp/src/views/ChangesView.vue`, replace:

```ts
const query = useChangeEvents()
```

with:

```ts
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const filters = computed(() => ({
  jurisdiction: (route.query.jurisdiction as string | undefined) ?? null,
  sector: (route.query.sector as string | undefined) ?? null,
}))

const query = useChangeEvents(filters)

function clearFilters(): void {
  router.push({ name: 'changes' })
}

const activeFilter = computed(() => {
  if (filters.value.jurisdiction) return { kind: 'Jurisdiction', value: filters.value.jurisdiction }
  if (filters.value.sector) return { kind: 'Sector', value: filters.value.sector }
  return null
})
```

(Place the `import { computed } from 'vue'` alongside existing imports if not already present; `useRoute` and `useRouter` get a fresh import line if absent.)

Then add the "Filtered by" chip near the top of the rendered output (just above the existing list — adapt to match the file's actual structure):

```vue
<div
  v-if="activeFilter"
  class="mb-4 inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-sm text-slate-700"
  data-testid="changes-filter-chip"
>
  Filtered by {{ activeFilter.kind }}: <strong>{{ activeFilter.value }}</strong>
  <button
    type="button"
    class="ml-1 rounded-full px-1 text-slate-500 hover:text-slate-900"
    aria-label="Clear filter"
    @click="clearFilters"
  >
    ✕
  </button>
</div>
```

- [ ] **Step 4: Manual smoke test**

With the dev server + API running, navigate to `/changes?jurisdiction=UK` while logged in as the UK demo user. Expected:
- Only UK rows in the list.
- "Filtered by Jurisdiction: UK ✕" chip visible above the list.
- Clicking ✕ clears the query and the full list (still scope-narrowed by the user's subscription) re-renders.

- [ ] **Step 5: Lint + unit tests**

```bash
cd packages/horizons-webapp && npm run lint:check && npm run test:unit -- --run
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add packages/horizons-webapp/src/api/changes.ts \
        packages/horizons-webapp/src/composables/useChangeEvents.ts \
        packages/horizons-webapp/src/views/ChangesView.vue
git commit -m "feat(webapp): filter /changes by jurisdiction or sector via route query"
```

---

## Task 12: Extend Playwright e2e

**Files:**
- Modify: `packages/horizons-webapp/e2e/login-and-scope.spec.ts`

- [ ] **Step 1: Read the existing spec**

```bash
cat packages/horizons-webapp/e2e/login-and-scope.spec.ts
```

Identify the existing login flow and where it asserts. The new tests reuse that flow.

- [ ] **Step 2: Add the new test cases**

Append to the file:

```ts
test('UK demo user sees one subscribed jurisdiction card and many muted', async ({ page }) => {
  await loginAsDemoUk(page)  // reuse the existing helper from this file
  await page.goto('/')

  // At least one subscribed card.
  const subscribed = page.locator('[data-testid="jurisdiction-card"][data-subscribed="true"]')
  await expect(subscribed).toHaveCount(1)
  await expect(subscribed.first()).toContainText('UK')

  // Multiple muted cards.
  const muted = page.locator('[data-testid="jurisdiction-card"][data-subscribed="false"]')
  expect(await muted.count()).toBeGreaterThan(0)

  // Click UK -> /changes?jurisdiction=UK and only UK rows render.
  await subscribed.first().click()
  await expect(page).toHaveURL(/\/changes\?jurisdiction=UK/)
  await expect(page.locator('[data-testid="changes-filter-chip"]')).toContainText('UK')
})

test('admin demo user sees full corpus with no Not-subscribed badges', async ({ page }) => {
  await loginAsAdminDemo(page)  // add a sibling helper alongside loginAsDemoUk
  await page.goto('/')

  const all = page.locator('[data-testid="jurisdiction-card"]')
  expect(await all.count()).toBeGreaterThanOrEqual(3)

  const notSubscribed = page.locator('[data-testid="jurisdiction-card"][data-subscribed="false"]')
  await expect(notSubscribed).toHaveCount(0)

  // Browse recent changes lists rows from multiple jurisdictions.
  await page.locator('[data-testid="nav-changes"]').click()
  await expect(page).toHaveURL(/\/changes$/)
  const rows = page.locator('[data-testid="change-row"]')  // existing testid on ChangesView rows
  await expect(rows.first()).toBeVisible()
})
```

If `loginAsAdminDemo` does not yet exist, add it next to the existing `loginAsDemoUk`:

```ts
async function loginAsAdminDemo(page) {
  // Match the shape used by the existing loginAsDemoUk helper.
  await page.goto('/login')
  await page.fill('[data-testid="email-input"]', 'admin-demo@demo.example.com')
  await page.fill('[data-testid="password-input"]', process.env.HORIZONS_DEMO_ADMIN_PASSWORD ?? 'demo-admin-pass')
  await page.click('[data-testid="login-submit"]')
  await page.waitForURL('/')
}
```

> **Selectors:** the test attribute names (`[data-testid="change-row"]`, `email-input`, etc.) must match what ChangesView and LoginView actually emit. If the existing spec uses different selectors, mirror those exactly.

- [ ] **Step 3: Run the e2e suite**

Follow `packages/horizons-webapp/e2e/README.md` to boot the stack, then:

```bash
cd packages/horizons-webapp && npx playwright test
```

Expected: existing tests still pass plus the two new tests pass.

- [ ] **Step 4: Commit**

```bash
git add packages/horizons-webapp/e2e/login-and-scope.spec.ts
git commit -m "test(e2e): assert home overview + admin corpus view"
```

---

## Task 13: Pre-push sweep + journal entry

**Files:**
- Create: `journal/260606-home-overview.md`

- [ ] **Step 1: Run the full local sweep**

```bash
uv run pytest
uv run ruff check .
uv run pyright
uv run pre-commit run --all-files
cd packages/horizons-webapp && npm run lint:check && npm run build && npm run test:unit -- --run
```

Expected: all green. Commit any `pre-commit` auto-fixes per `feedback-run-precommit-before-push`.

- [ ] **Step 2: Write the journal entry**

```markdown
<!-- journal/260606-home-overview.md -->
# Home overview dashboard

Built the post-login home dashboard. Goal: make subscription scoping
visible at a glance and give admins a corpus-wide landing page.

## What landed

- New `GET /v1/me/overview` returning the corpus matrix grouped by
  jurisdiction and by sector, with `subscribed` flags per item and an
  `is_admin` discriminator.
- New `app_public.corpus_shape()` SECURITY DEFINER function — non-sensitive
  catalog data, no per-request audit. Migration 0013.
- New `admin_or_app_session` dependency: client callers run under `api_app`
  (RLS narrows); admin callers run under `admin_bypass` and write one
  `admin_access_log` row per request (`reason = request path`). Applied to
  discovery / temporal / differential / overview.
- HomeView rebuilt: summary cards + Jurisdictions / Sectors sections with
  drill-down to `/changes?jurisdiction=…` / `/changes?sector=…`. Not-subscribed
  cards are visibly muted and click-disabled. Admin variant collapses the
  summary into a single "Full corpus" card.
- ChangesView reads `jurisdiction` / `sector` from the route query and
  threads them through the existing discovery call; a "Filtered by …" chip
  with a clear button surfaces the active filter.
- Playwright e2e extended: UK demo user sees 1 subscribed + ≥1 muted card
  and a working drill-down; admin sees no muted badges and corpus-wide rows
  on `/changes`.

## Why this shape

Corpus shape (which jurisdictions / sectors exist, how many docs each) is
catalog data, not tenant data — clients already know the token vocabulary.
Routing it through `admin_bypass` per page load would force a per-load
audit entry for no security gain, so a `SECURITY DEFINER` function is the
right seam. Per-row corpus content stays scoped via RLS everywhere else.

`admin_or_app_session` is the smallest seam that lets admins use the
public primitives directly. Adding `/v1/admin/discovery` etc. was the
alternative; rejected because it doubles the API surface for one reader.

## Follow-ups (post-demo)

- Subscribe-to-view CTA on muted cards.
- Add a `/v1/me/overview` cache-buster on subscription changes.
- Consider whether the existing `/v1/me` subscription DTO shape mismatch
  between server (`scope`/`active_subscriptions`) and webapp client
  (`active_pairs`/`is_admin_bypass`) needs reconciling.
```

- [ ] **Step 3: Commit the journal**

```bash
git add journal/260606-home-overview.md
git commit -m "docs(journal): home overview dashboard session"
```

- [ ] **Step 4: Push and merge**

```bash
git push origin <feature-branch>
git -C /Users/john/projects/syncthing/agent-lxc/horizons merge --ff-only <feature-branch>
git -C /Users/john/projects/syncthing/agent-lxc/horizons push origin main
git push origin --delete <feature-branch>
```

Expected: CI green on main.

---

## Self-Review Notes

**Spec coverage:**
- ✅ UI summary row + jurisdictions section + sectors section: Task 10.
- ✅ `/v1/me/overview` endpoint with the documented shape: Task 5.
- ✅ `app_public.corpus_shape()` migration + grants: Task 1.
- ✅ `current_scope_pairs` joined against matrix for `subscribed` flag: Task 5.
- ✅ `admin_or_app_session` + audit row + apply to four primitives: Tasks 3, 4.
- ✅ Header keeps "Browse recent changes" / "Manage watchlists": Task 10.
- ✅ Click navigates to `/changes?jurisdiction=...` / `?sector=...`: Tasks 9, 10.
- ✅ Not-subscribed cards visually muted, click disabled, tooltip "Subscribe to view": Task 9.
- ✅ Admin variant — no badges, single "Full corpus" summary card: Tasks 5 (server `is_admin`), 10 (template branch).
- ✅ `useChangeEvents` accepts filters + `queryKey` change: Task 11.
- ✅ "Filtered by … ✕" chip in ChangesView: Task 11.
- ✅ Pytest tests for `/v1/me/overview` UK / EU / admin: Task 5.
- ✅ Pytest tests for admin escalation on discovery: Task 4.
- ✅ Vitest tests for HomeView, JurisdictionCard, SectorCard: Tasks 9, 10.
- ✅ Playwright e2e extension: Task 12.
- ✅ Docs updates (horizons-primitives.md, endpoints.md, roles.md): Tasks 6, 7.

**Type consistency:** `OverviewResponse`, `JurisdictionOverviewItem`, `SectorOverviewItem`, `OverviewTotals` field names match between the Python wire model (Task 5) and the TypeScript client (Task 8). `useChangeEvents` and `fetchDiscovery` both use the same `{jurisdiction, sector}` shape (Task 11). `CorpusShapeRow` is the single internal Python DTO; the API hand-rolls the wire DTOs on top of it.

**Scope:** focused on the home dashboard + the admin corpus visibility on `/changes`. Excluded: subscribe CTAs, watchlist changes, new filter axes, `/v1/me` shape reconciliation (called out in journal as post-demo).
