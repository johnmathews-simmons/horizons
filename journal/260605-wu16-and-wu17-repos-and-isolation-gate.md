# 2026-06-05 — WU1.6 (repository layer) + WU1.7 (two-client isolation gate)

Closes Track 1. The RLS spine and bracket from WU1.4 / WU1.5 now have an
application-level surface (`horizons_core.repos`) and a two-client
integration gate that asserts both isolation axes hold end-to-end through
the repos. Per the improvement plan, no Track 2 / 3 / 4 work should
merge while either gate file is red.

## What shipped

### WU1.6 — repository layer scaffold

New package: `packages/horizons-core/src/horizons_core/repos/`.

- `base.py` — `Repository[DTO_co]` marker `Protocol`. Carries a single
  `dto_type: ClassVar[type[BaseModel]]` for callers that want to reason
  about "any repo returning `X`". No required methods because each
  aggregate's read/write surface is different and uniformity for its own
  sake would be cargo-culted.
- `watchlists.py` — `WatchlistDTO` (Pydantic v2, `frozen=True`,
  `from_attributes=True`) + `WatchlistsRepository` with `list_for()`,
  `get_by_id(id)`, `create(*, user_id, name)`,
  `delete(*, user_id, watchlist_id) -> bool`.
- `documents.py`, `versions.py`, `clauses.py` — DTOs + read-only
  repos. Each has `list_*` and `get_by_id`. Writes are the ingestion
  worker's surface and arrive in Track 3.
- `repos.md` — layer architecture, `user_id` discipline,
  defence-in-depth posture, ORM-only rule, aggregate inventory.
- `__init__.py` — single import surface for downstream callers.

`pyproject.toml`: `horizons-core` now depends on `pydantic>=2.9` and
declares its `sqlalchemy[asyncio]>=2.0` runtime requirement (it was
implicit before via the workspace dev group). Ruff per-file-ignores
gain `repos/*.py = ["TC003"]` for the same reason `db/models/*.py` has
it — Pydantic v2 resolves annotations via `get_type_hints()` at
model-build time, so stdlib types referenced in DTO fields must be
importable at runtime.

### WU1.7 — two-client integration gate

`tests/isolation/` (new package):

- `conftest.py` — `two_clients` fixture seeding user A (UK / BANKING),
  user B (EU / INSURANCE), one watchlist and one document/version/clause
  chain per scope. Returns a `TwoClients` dataclass whose
  `session_for(user_id)` helper yields a session already bracketed with
  the WU1.5 `app.user_id` binding plus `SET LOCAL ROLE api_app`
  (Track 4's eventual FastAPI request shape). An `admin_session()`
  variant uses `SET LOCAL ROLE admin_bypass`.
- `test_private_state_isolation.py` — five assertions on the
  cross-client privacy axis through `WatchlistsRepository`: B's
  `list_for` cannot see A's rows (and vice versa), `get_by_id` on A's
  watchlist returns `None` from B (the 404-not-403 contract), B's
  `delete` of A's row returns `False` and A's row survives, admin
  bypass sees both.
- `test_corpus_subscription_isolation.py` — six assertions on the
  subscription-scope axis through `DocumentsRepository`,
  `DocumentVersionsRepository`, `ClausesRepository`: A and B see only
  their scope's `list_all`, `get_by_id` returns `None` for out-of-scope
  rows, child tables (`versions`, `clauses`) are filtered via the FK
  walk to `documents`, admin bypass sees both scopes.

## Decisions surfaced before the first edit

Four questions resolved up front via `AskUserQuestion` (see
`.engineering-team/runs/manual-20260604T151127Z/` for the full prompt
shape):

1. **Scope.** WU1.6 + WU1.7 against the tables that exist today;
   `change_events` deferred to Track 3 so it lands with its real
   consumer instead of being built as a stub and reshaped twice. The
   improvement plan's "(c) closes Track 1 cleanly" reading.
2. **Session shape.** Constructor injection — `Repo(session)` — composes
   with `get_session()` and the future FastAPI `Depends` boundary; the
   alternative (session as first method arg) is more functional but
   less ergonomic.
3. **DTO placement.** Alongside the repo — `repos/watchlists.py`
   exports both `WatchlistsRepository` and `WatchlistDTO`. Split when
   there's a reason, not by default.
4. **`user_id` discipline.** Keyword-only on writes; absent on reads.
   The session-bound `app.user_id` and the RLS `USING` predicate already
   filter reads; passing `user_id` again would be redundant noise.
   Writes still take `*, user_id: UUID` because the policy's
   `WITH CHECK` predicate enforces the same equality and an explicit
   keyword keeps the ownership claim visible at the call site.

## Plan drift / corrections

The improvement plan's WU1.6 acceptance specifies
`create(*, user_id, document_id)` on `WatchlistsRepository`. The shipped
`watchlists` table (WU1.4) has a `name` column, not `document_id` —
the table is intentionally a minimal private-state proving ground.
`create(*, user_id, name)` matches the actual schema. Captured in the
docstring so future readers don't second-guess.

The plan also expected `change_events` to exist by WU1.4 and to be
the corpus surface WU1.7 asserts against. WU1.4 as shipped enabled
RLS on the three existing corpus tables (`documents`,
`document_versions`, `clauses`) instead. WU1.7 substitutes those for
the assertion target; the assertion shape is identical (an
out-of-scope row is invisible to the other client and visible to
admin bypass).

## Two gotchas worth keeping

### 1. asyncpg + module-scoped `AsyncEngine` is hostile

First version of `tests/isolation/conftest.py` had `async_engine` and
`two_clients` at `scope="module"` for setup efficiency. First test
passed; second test crashed inside the SQLAlchemy `checkin` event
handler with `asyncpg.exceptions._base.InterfaceError: cannot perform
operation: another operation is in progress`. The engine had bound to
the first test's event loop (function-scoped per pytest-asyncio's
`asyncio_default_test_loop_scope=function`); the second test's loop
hit the same connection from a different loop. Same shape as the
`test_session_bracket.py` pattern that uses function-scoped fixtures.
The fix was to drop everything to function scope and let `alembic
upgrade head` no-op against the already-migrated schema. Re-seeding
per test costs ~250ms; tests use a per-call `uuid` suffix so rows
coexist.

### 2. Pydantic v2 + `from __future__ import annotations`

Pydantic resolves field type annotations via `typing.get_type_hints()`
at model-build time, which needs the names to be in the module globals.
With `from __future__ import annotations` (the project convention),
`datetime`, `uuid.UUID`, and the other field types must stay at module
scope — moving them into `TYPE_CHECKING` would break model
construction. Same shape as the existing `db/models/*.py` exemption
for SQLAlchemy `mapped_column`; the ruff per-file-ignore was extended
to cover `repos/*.py`.

`sqlalchemy.ext.asyncio.AsyncSession` is the exception: it appears
only in `__init__(self, session: AsyncSession)`, which is a function
annotation `from __future__` turns into a string and pydantic never
sees. It lives inside `if TYPE_CHECKING:` and TC002 stays clean.

## Process notes

Ruff bump (0.9 → 0.15.16 via `uv sync`) reformatted a few pre-existing
files (`db/session.py`, `tests/test_raw_sql_isolation.py`,
`tests/test_session_bracket.py`) — single-line wraps under the 100-char
limit. Carried in the same commit because the local sweep is the gate
and the new state is the formatter's actual output.

Test count: **86 passing** (was 75 before this WU; +11 from
`test_repos_watchlists.py` and `test_repos_corpus.py`, +11 from the
two `tests/isolation/` files, with the seeded fixture cost amortised
across both files). Coverage on the new repos and DTOs is 100%
line+branch; total tracked Python source remains 100%.

## Status

| Track 1 unit | Status |
| --- | --- |
| WU1.0 | shipped (`0001_role_model.py`) |
| WU1.1 | shipped (`0002_tenancy_tables.py`) |
| WU1.2 | shipped (`0003_corpus_tables.py`) |
| WU1.3 | shipped (`0004_current_scope.py`) |
| WU1.4 | shipped (`0005_rls_spine.py`) |
| WU1.5 | shipped (`db/session.py`) |
| **WU1.6** | **shipped (`repos/`)** |
| **WU1.7** | **shipped (`tests/isolation/`)** — the gate |

Track 1 is closed. Track 2 (alignment), Track 3 (ingestion worker),
and Track 4 (FastAPI surface) can now proceed; each will build on top
of the spine the gate test now defends.

## Next-session candidates

The improvement plan's natural next moves:

- **WU1.8** — Hypothesis property test for isolation (nightly only).
  Generates `(N clients × M subscriptions × K writes)` and asserts each
  client's reads ⊆ (their writes ∪ scope-allowed). Slow; CI on a
  separate `nightly` workflow.
- **WU1.9** — admin operator + impersonation paths. Splits
  `admin_operator_session()` (uses `admin_bypass`) from
  `admin_impersonation_session(admin_id, target_user_id)` (uses
  `api_app` with both GUCs). Token mint writes one
  `admin_access_log` row per token.
- **WU2.x** — alignment pipeline lands; consumes the corpus repos.
- **WU3.x** — ingestion worker lands; will add a parallel set of
  `ingestion_*` repos (or extend the existing ones) running under the
  `ingestion_worker` role.
- **WU4.x** — FastAPI surface. Wraps `get_session()` in a
  request-scoped `Depends`, switches to `api_app`, hands out repos.
