# 2026-06-05 — WU1.5 connection layer + SET LOCAL request bracket

*Last revised: 2026-06-05.*
*Path: journal/260605-wu15-session-bracket.md.*

Fifth unit of the day. WU1.4 left the RLS spine asserting on a GUC
nothing was setting; this WU lands the bracket that does the setting.
Every protected read now has a sanctioned path from the application
layer down to the policy.

## What shipped

- `packages/horizons-core/src/horizons_core/db/session.py` — new module.
  Three public surfaces:
  - `make_engine(url)` builds an `AsyncEngine` with
    `connect_args={"statement_cache_size": 0}` and a `checkin` event
    handler that issues `DISCARD ALL` against the raw asyncpg
    connection.
  - `session_for_user(engine, user_id)` is the testable primitive — an
    `@asynccontextmanager` that opens a session, begins a transaction,
    issues `SELECT set_config('app.user_id', :u, true)`, yields the
    session, and lets `session.begin()` commit-or-rollback at exit.
  - `get_session(user_id)` is the FastAPI-Depends-shaped wrapper that
    reuses a lazy module-level engine read from `HORIZONS_DB_URL` on
    first call.
- `tests/test_session_bracket.py` — 6 behavioural integration tests:
  GUC is bound inside the bracket; commit persists; rollback reverts;
  `DISCARD ALL` actually clears session-level GUCs across pool reuse
  (set a non-LOCAL `SET app.test_marker = 'leaked'`, return conn,
  reacquire, assert value differs); end-to-end with `SET LOCAL ROLE
  api_app` the `watchlists` RLS policy is owner-scoped; the lazy global
  path is covered (including both branches of the `_engine is None`
  check).
- `tests/test_raw_sql_isolation.py` — 1 architectural test. AST-walks
  every `.py` under `packages/horizons-*/src/` and fails on any
  `text(...)` call (`Call(func=Name('text'))` or
  `Call(func=Attribute(attr='text'))`) outside the allow list. Allow
  list: `session.py` (the sanctioned home) and `db/models/*.py` (where
  `text("uuidv7()")` and `text("now()")` are declarative
  `server_default=` arguments, not raw-SQL execution).
- `db/rls.md` — new "Session contract (WU1.5)" section. Documents the
  bracket shape, the commit/rollback semantics, the `DISCARD ALL`
  discipline, the two SQLAlchemy/asyncpg implementation notes (see
  Gotchas below), and the rationale for setting only `app.user_id`
  today. Header updated from "As of WU1.4 the spine is live" to "As of
  WU1.5 the spine and its bracket are live". The "Session GUC" section
  switched from `SET LOCAL app.user_id = '...'` to the actual
  `set_config('app.user_id', :u, true)` form the bracket issues.
- `db/roles.md` — the "How `SET LOCAL app.user_id` will work (WU1.5)"
  forward-looking section rewritten in present tense, pointing at the
  session module and the new Session contract section.

## Q1–Q4 decisions

1. **Which GUCs the bracket sets today.** Picked `app.user_id` only.
   The plan listed three (`app.user_id`, `app.user_role`,
   `app.subscription_id`) but no RLS policy or helper reads the other
   two. `current_scope()` derives the subscription set from
   `app_private.user_subscriptions` keyed on `app.user_id`, not from a
   GUC — and a multi-subscription client has no canonical
   `subscription_id` for the bracket to bind. The two missing GUCs can
   be added cheaply when a real consumer appears; setting them today
   would be cargo cult.
2. **Async-only `get_session`.** API is the only consumer; YAGNI on a
   sync flavour. Migrations and ad-hoc smoke tests keep their own sync
   engine path.
3. **Architectural pytest as the sole `text()` ban mechanism.** One
   mechanism is enough — pytest runs in pre-commit and CI. A ruff
   plugin would mean either custom Rust code (heavy) or
   `banned-api` which targets imports not call sites (probably not
   even effective for this case). Revisit if a cheaper IDE-time signal
   becomes possible.
4. **`DISCARD ALL` on checkin, not narrower `RESET`.** Matches the
   plan, demo-scale plan-cache churn is negligible, and "anything we
   forget to RESET" stays covered. Cost showed up in the form of two
   implementation gotchas (below) rather than perf.

## Gotchas hit during implementation

1. **`DISCARD ALL` rejected inside a transaction block.** First
   implementation used `dbapi_connection.cursor().execute("DISCARD
   ALL")` on the SQLAlchemy asyncpg adapter cursor. asyncpg's adapter
   wraps every cursor execute in an implicit transaction, and Postgres
   raises `ActiveSQLTransactionError: DISCARD ALL cannot run inside a
   transaction block`. Fix: bypass the cursor entirely — pull
   `dbapi_connection.driver_connection` (the raw `asyncpg.Connection`)
   and run `await_only(driver_connection.execute("DISCARD ALL"))` via
   the SQLAlchemy greenlet bridge. asyncpg's bare `Connection.execute`
   uses the simple query protocol and does not wrap in a txn.
2. **`statement_cache_size=0` is mandatory when `DISCARD ALL` runs on
   checkin.** Second test pass failed with
   `InvalidSQLStatementNameError: prepared statement
   "__asyncpg_stmt_1d__" does not exist`. `DISCARD ALL` includes
   `DEALLOCATE ALL`, so the server-side prepared statements asyncpg
   thinks it cached are gone — and asyncpg's client-side cache does
   not learn about it. Solution: `connect_args={"statement_cache_size":
   0}` on `create_async_engine`. Trades a re-prepare per call for not
   carrying the stale-cache footgun; at demo scale the trade is fine.
   Both gotchas are documented in the Session contract section and in
   the `make_engine` docstring so the next reader doesn't have to
   discover them from a test failure.
3. **Pyright `reportUnusedFunction` on `@event.listens_for`-decorated
   closures.** Pyright can't see that the decorator stores the function
   as a listener side-effect, so the closure looks dead. Refactored to
   a module-level `_discard_all_on_checkin(...)` plus
   `event.listen(engine.sync_engine, "checkin", _discard_all_on_checkin)`
   — same behaviour, no false positive.
4. **`AsyncIterator` deprecated as `@asynccontextmanager` return
   annotation.** Pyright now prefers `AsyncGenerator[T]` over
   `AsyncIterator[T]` for the wrapped function. Switched both
   signatures.
5. **`text()` ban can't be aspirationally global.** The plan reads
   "`text()` is the only allowed raw-SQL path and is permitted only
   inside this file". Taken literally that would also ban the
   declarative `server_default=text("uuidv7()")` calls in
   `db/models/*.py`. Reinterpreted pragmatically: the ban is on
   imperative raw-SQL execution; declarative server-default expressions
   are a different use of the same constructor. The architectural test
   allow-lists `db/models/*.py` for this reason; the rationale is
   captured in the test docstring and the Session contract section.

## Tests

64 pytest tests passing (was 57 at the end of WU1.4). 100% line +
branch coverage on tracked Python source. Six new behavioural tests
in `tests/test_session_bracket.py`; one new architectural test in
`tests/test_raw_sql_isolation.py`.

Full local sweep before push: `uv run pytest`, `uv run ruff check .`,
`uv run pyright`, `uv run pre-commit run --all-files`, plus
`npm run lint:check`, `npm run build`, and `npm run test:unit -- --run`
in `packages/horizons-webapp`. All green.

## Next session candidates

| WU | Title | Notes |
| --- | --- | --- |
| 1.6 | Repository layer | First consumer of `get_session`. Per-aggregate repositories that take a session, hide raw SQL, expose typed ORM queries. |
| 1.4-residual | `change_events` table | The original plan WU1.4 listed `change_events`; WU1.4 shipped without it. Append-only fact table, FK to `clauses`, RLS scope policy walking up through `clauses` → `document_versions` → `documents` → `current_scope()`. |
| 2.x | API surface | First HTTP endpoint. Will exercise `get_session` via FastAPI `Depends`, prove the bracket integrates with the framework, and decide whether a separate "admin context" entry point earns its keep. |

WU1.5 closes the WU1.x track: the spine is live, the bracket binds the
GUC, RLS fires end-to-end. The next reads against `watchlists` /
`documents` / etc. from real application code will go through this
module.
