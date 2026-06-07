# WU4.4 — Three primitives at all three scopes

*Last revised: 2026-06-05.*
*Path: journal/260605-wu44-three-primitives.md.*

*2026-06-05.*

Shipped `GET /v1/discovery`, `GET /v1/temporal`, `GET /v1/differential`
behind the same scope discriminator (`corpus` / `document` / `clause`).
Lands the headline backend feature on the WU5.3 critical path: the
clause-diff demo UX reads from these endpoints, and the WU4.6 OpenAPI
generator builds on them.

Merged to main as four commits: discovery (`f296747`), temporal
(`d015f50`), differential + arch test + p95 budget (`4fbf8f6`), and a
hygiene fix for unrelated WU7.0/7.1 format drift (`e68dba1`).

## Decisions (made up-front, before first edit)

1. **One worktree, three commits.** Matches WU3.4's pattern — single
   ff-merge, per-endpoint review surface.
2. **Single endpoint with discriminator** (`?scope=...`) over three
   sub-paths per primitive. Pydantic discriminated-union-style
   validation in the route layer; 422 with a clear message on
   missing required filters.
3. **Opaque base64-encoded cursor** over raw query params. Encodes
   the `(detected_at, id)` keyset position; clients must not parse
   or generate. Server can change the encoding without breaking
   clients.
4. **`include_content=true` at corpus scope rejected when
   `limit > 10`.** A 50-event corpus differential is megabytes of
   clause body text; the cap forces clients to page in small chunks
   or use document/clause scope.
5. **p95 < 3 s asserted inline** against a 500-event synthetic seed.
   Runs in the normal integration suite (~5 s overhead). The real
   deployed smoke lands with WU6.3.

## What I had to build before the endpoints

- **`ChangeEvent` ORM model** — migration 0010 had shipped the table
  + RLS + grants but no SQLAlchemy class existed. `id` is
  `BigInteger` (mirrors `bigserial`); every other PK in the schema
  is `uuidv7()`.
- **`ChangeEventsRepository`** — scope-aware reads via `select()`
  (no raw `text()`; the arch test would catch it). One typed
  scope union (`CorpusScope | DocumentScope | ClauseScope`),
  three methods (`list_discovery`, `timeline`, `differential`)
  sharing a single `_fetch_page` so the wire-shape decisions stay
  at the route layer.
- **`docs/api/horizons-primitives.md`** — design-of-record for the
  wire contract. The upstream `docs/api/endpoints.md` is the
  Lawstronaut reference; WU4.6 will publish an OpenAPI-generated
  Horizons reference. Added a pointer from `docs/api/README.md`.

## Architectural defence at the API boundary

- **`tests/test_no_direct_corpus_access.py`** — AST-walks
  `packages/horizons-api/src/` and fails on any import of
  `Document` / `DocumentVersion` / `Clause` / `ChangeEvent` from
  `horizons_core.db.models`. The API must read the corpus through
  `horizons_core.repos.*`. Mirrors `test_raw_sql_isolation.py`'s
  shape — same AST traversal, same allow-list pattern.
- The arch test caught one real concern as I built: it's tempting
  to import the ORM `ChangeEvent` directly into the route to keep
  query shapes flat. The test makes that a build break.

## Things that bit me

1. **`AsyncSession` under `TYPE_CHECKING`.** First pass put the
   `AsyncSession` import inside an `if TYPE_CHECKING:` block. With
   `from __future__ import annotations`, FastAPI's parameter
   introspection couldn't resolve `Annotated[AsyncSession,
   Depends(...)]` at decoration time and classified `session` as a
   query parameter, returning 422 with `loc: ["query", "session"]`.
   Moved the import to runtime. Worth checking other route files
   if any get added later — `watchlists.py` already imports
   `AsyncSession` at runtime; following the same pattern is right.
2. **Shared session-scoped Postgres container.** The first
   ordering test asserted `[it.id for it in items] == [tied_second,
   tied_first, old_id]` and saw prior tests' rows under the same
   UK/BANKING scope. Fixed by giving every assertion-sensitive test
   a unique `(jurisdiction, sector)` pair via `uuid.uuid4().hex[:8]`.
   Tests with `in` / membership semantics didn't need the change.
3. **Unrelated format drift on main.** `pre-commit` reformatted
   `packages/horizons-core/tests/observability/test_otel.py` —
   a 3-line cosmetic change that the WU7.0/7.1 commit must have
   landed without the format hook. Couldn't merge without fixing
   it, so committed as a small chore in this branch.
4. **`origin/main` advanced during work** — WU5.0 secfix
   (`017ef3f`) landed while I was building. Rebased cleanly (no
   file overlap), re-ran the full sweep, then merged. The
   parallel-session-collision hazard from the WU3.5 journal is
   still real.

## Numbers

- 4 commits, 12 files, +2,538 lines, -4.
- 33 new tests (12 repo + 13 endpoint + 3 cursor unit + 1 arch
  + 4 differential + 1 load budget).
- Full sweep: 511 passed, 4 skipped (alignment fixture quality),
  1 deselected (nightly).
- p95 in the load test came in well under budget on local Docker
  (sub-second per 50-request batch against 500 seeded events).

## Open questions deferred

- **`change_events.id` cursor stability across DB migrations.** If
  the bigserial gets re-keyed (it won't — append-only), cursors
  would break. Documented in `horizons-primitives.md` as opaque
  so clients can't depend on the underlying shape.
- **`since` / `until` semantics on `effective_date` vs
  `detected_at`.** Filtered on `detected_at` for corpus scope.
  Effective-date filtering (e.g. "what's coming into force in the
  next 90 days?") is a future request and a different index.
- **Aggregations in temporal corpus scope** — "what's the most
  recent change across EU finance laws?" Currently a client
  computes this from the first page. A `?aggregate=latest` mode
  would push the work server-side; deferred until a customer asks.

## What's next

- **WU4.5** (admin subscription endpoints) — independent of this.
- **WU4.6** (OpenAPI + API docs) — reads my route definitions to
  regenerate `docs/api/horizons-primitives.md` from the live
  OpenAPI spec. The wire shape I documented is the spec contract.
- **WU5.3** (clause diff renderer in the Vue webapp) — consumes
  `/v1/differential` at clause scope.
