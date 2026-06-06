# Multi-tenant isolation: implementation risks and de-risking decisions

*Audit date: 2026-06-04. Pre-code; post-design (docs 1–4).*

Findings numbered; `[VERIFIED]` = cited / well-known; `[SUSPECTED]` = judgement, confirm in Phase 2. Code sketches illustrative.

---

## A. Database-side RLS implementation (Postgres)

### 1. The PgBouncer + session-GUC trap — pick `SET LOCAL` inside an explicit transaction. [VERIFIED]

`SET app.user_id = '...'` (session scope) **does not** survive PgBouncer transaction-pool mode. Statement pooling is even worse — RLS with `SET`/`SET LOCAL` "will not work properly … you will likely return rows for the wrong users" (Daniel Imfeld, [PostgreSQL RLS notes](https://imfeld.dev/notes/postgresql_row_level_security); also called out by [Bytebase footguns](https://www.bytebase.com/blog/postgres-row-level-security-footguns/)). The only safe pattern under transaction pooling is:

```sql
BEGIN;
SET LOCAL app.user_id = '...';
SET LOCAL app.user_role = 'client';
-- queries
COMMIT;
```

GUCs scoped by `SET LOCAL` are bound to the transaction and PgBouncer keeps that transaction on one backend until COMMIT.

**Lock in:**

- **No PgBouncer for the demo.** Azure DB for PG Flexible Server's built-in PgBouncer stays disabled; SQLAlchemy's `AsyncAdaptedQueuePool` is enough at demo scale.
- **If enabled later, `pool_mode = transaction` only** (never `statement`); session-scoped `SET` is banned codebase-wide.
- **Every API request = explicit transaction** opened with `SET LOCAL app.user_id`, `SET LOCAL app.user_role`, and (client role) `SET LOCAL app.subscription_id`. Read-only routes use `BEGIN READ ONLY`.
- **Belt-and-braces:** SQLAlchemy `checkin` event emits `DISCARD ALL` on pool return. Note async asyncpg has cancellation-leak edges ([SQLAlchemy #12460](https://github.com/sqlalchemy/sqlalchemy/discussions/12460), [pooling docs](https://docs.sqlalchemy.org/en/20/core/pooling.html)).

### 2. SECURITY DEFINER scope function — cache the scope, don't re-join per row. [VERIFIED]

Naïve corpus RLS would be:

```sql
CREATE POLICY corpus_scope ON change_events
  FOR SELECT TO client
  USING (
    (jurisdiction, sector) IN (
      SELECT j, s FROM subscription_scopes
      WHERE user_id = current_setting('app.user_id')::uuid
    )
  );
```

This re-runs the subscription subquery for every row evaluated, and (per [Supabase RLS perf notes](https://supabase.com/docs/guides/troubleshooting/rls-performance-and-best-practices-Z5Jjwv) and [makerkit RLS best practices](https://makerkit.dev/blog/tutorials/supabase-rls-best-practices)) the planner cannot always hoist it. The recommended pattern is a `SECURITY DEFINER STABLE` function that returns the scope, wrapped in `(SELECT …)` so the planner evaluates it once per query:

```sql
CREATE FUNCTION app_private.current_scope()
RETURNS TABLE (jurisdiction text, sector text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
  SELECT s.jurisdiction, s.sector
  FROM app_private.subscription_scopes s
  WHERE s.user_id = current_setting('app.user_id')::uuid
$$;

CREATE POLICY corpus_scope ON change_events
  FOR SELECT TO client
  USING (
    (jurisdiction, sector) IN (SELECT j, s FROM (SELECT * FROM app_private.current_scope()) x)
  );
```

**Lock in:**

- `SECURITY DEFINER` + `SET search_path = ''` is mandatory (CVE history).
- Functions in `app_private` schema, `REVOKE ALL FROM client` — can't be invoked directly.
- **[SUSPECTED]** Premature: per-connection cached scope via `SET LOCAL app.scope = '<json>'` is faster but adds complexity. Start with the SECURITY DEFINER function; switch only if `EXPLAIN ANALYZE` shows it dominating.
- Repository-layer scope join is the first/hot path; RLS is the safety net.

### 3. Admin bypass: `BYPASSRLS` is cleaner than `FORCE ROW LEVEL SECURITY` + role policy. [VERIFIED]

Two patterns per [PG 17 docs](https://www.postgresql.org/docs/17/ddl-rowsecurity.html):

| Pattern | How | Trade-off |
|---|---|---|
| `ALTER ROLE admin BYPASSRLS` | Role attribute makes admin skip RLS entirely on all tables | Simple, audit-friendly (single grant), but coarse — admin sees *everything* and you cannot easily add a "support viewing this client" policy layer |
| `ALTER TABLE x FORCE ROW LEVEL SECURITY` + admin policy `USING (true)` | Owner doesn't skip; admin role gets explicit allow policy | More flexible (you can require admin to declare an impersonation target via `SET LOCAL app.impersonating_user_id`), but more policies to audit |

**Recommendation:** admin has **two connection modes**:

1. **Operator mode** — `BYPASSRLS` role, for system-health / cross-corpus views; audited at the route.
2. **Impersonation mode** — `SET ROLE client` + `SET LOCAL app.user_id = <target>` + `SET LOCAL app.impersonating_admin_id = <admin>`, for support views.

Impersonation reuses client RLS — admins exercise the same scoping path clients do, so isolation regressions surface fast. The impersonation marker is the audit-log key.

`FORCE ROW LEVEL SECURITY` is still required on private-state tables (the app role owns them and would otherwise bypass).

### 4. Pytest fixture pattern — two connections, two transactions, disjoint scopes. [VERIFIED]

Recommended layout:

```python
@pytest.fixture(scope="session")
def pg_container() -> PostgresContainer: ...  # testcontainers

@pytest.fixture(scope="session")
def engine(pg_container) -> AsyncEngine: ...  # run migrations + RLS policies

@pytest.fixture
async def two_clients(engine) -> tuple[ClientCtx, ClientCtx]:
    # Factory creates user A (UK), user B (EU), disjoint subscriptions.
    # Returns two ClientCtx, each with its own AsyncSession and app.user_id set.
    ...

async def test_a_cannot_see_b_watchlist(two_clients):
    a, b = two_clients
    await a.repo.watchlists.create(document_id=...)
    rows = await b.repo.watchlists.list_all()  # B's session, B's user_id
    assert rows == []
```

**Decisions:** two distinct `AsyncSession`s per test (never `SET ROLE` swapping on one connection — masks bugs); session-scoped container + function-scoped TRUNCATE ([testcontainers/pytest](https://qxf2.com/blog/using-testcontainers-with-pytest/)); same conftest provides `admin_ctx` for bypass tests.

---

## B. Application-side repository layer (Python)

### 5. Lint-banned raw SQL — Ruff + custom check + AST grep, not magic. [VERIFIED]

No native Ruff rule bans `sqlalchemy.text()` ([Ruff settings](https://docs.astral.sh/ruff/settings/), [#10980](https://github.com/sqlalchemy/sqlalchemy/discussions/10980)). Layered approach:

1. **Ruff `flake8-tidy-imports` `banned-api`** on `sqlalchemy.text` outside an allowlist (`app/db/session.py`, migrations).
2. **Pre-commit grep** for `.execute(text(` and `.execute("` outside allowlist.
3. **Semgrep** later if leaks slip through.
4. **Architectural pytest** asserting no `app/api/**` module transitively imports `sqlalchemy.text`.

**Ship (1)+(2)+(4) day one.**

### 6. Repository pattern shape — mandatory `user_id`, returns typed result. [VERIFIED]

```python
class WatchlistsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for(self, user_id: UUID) -> list[Watchlist]:
        # user_id MUST be passed even though RLS would scope it anyway:
        # belt-and-braces, and makes the test "did we pass the right user?" trivial.
        stmt = (
            select(WatchlistRow)
            .where(WatchlistRow.user_id == user_id)
            .order_by(WatchlistRow.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [Watchlist.model_validate(row) for row in result.scalars()]

    async def create(self, *, user_id: UUID, document_id: UUID) -> Watchlist:
        # Subscription-subset check is done in the service layer, not here.
        ...
```

Rules: `user_id` required (type-error if forgotten); no `**kwargs` WHERE; methods return Pydantic domain models, not ORM rows; one repo per private-state table; corpus repos take `scope: SubscriptionScope`.

### 7. FastAPI middleware → connection setup. Sketch. [VERIFIED]

```
HTTP request
  └─ AuthMiddleware: bearer → (user_id, role)
       └─ Depends(get_session): open AsyncSession, BEGIN
            └─ Depends(scope_session): SET LOCAL app.user_id, app.user_role, app.subscription_id
                 └─ route handler
                      └─ COMMIT (yield-based teardown)
```

Concrete:

```python
async def get_session(
    request: Request,
    user: AuthenticatedUser = Depends(current_user),
) -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        async with session.begin():  # explicit transaction
            await session.execute(
                text("SET LOCAL app.user_id = :u"), {"u": str(user.id)}
            )
            await session.execute(
                text("SET LOCAL app.user_role = :r"), {"r": user.role.value}
            )
            yield session
```

Decisions: `text()` only in `app/db/session.py`; parameter binding (no f-strings); one session + one transaction per request; no streaming responses (doc 1 primitives don't need them).

---

## C. Subscription-scoping implementation

### 8. Subscription model — separate join table, not arrays. [SUSPECTED, but well-justified]

Options:

| Shape | Pros | Cons |
|---|---|---|
| `subscriptions(user_id, jurisdictions text[], sectors text[])` | One row per client; easy to read | Hard to constrain referentially; arrays don't index well for membership in RLS predicates; awkward to audit "when did the UK get added?" |
| `subscriptions(id, user_id, valid_from, valid_to)` + `subscription_scopes(subscription_id, jurisdiction, sector)` | Normalised, easy to index, audit-friendly, FK to `jurisdictions`/`sectors` | More rows |

**Recommend the second.** One row per `(jurisdiction, sector)` pair; `current_scope()` selects directly; B-tree on `(user_id, jurisdiction, sector)`. Add `valid_from/valid_to` at the subscription level so "subscription reduction" (doc 4 open question) becomes "insert superseding row" — append-only, matches doc 3 principle 1.

### 9. Watchlist ⊂ subscription — application-level CHECK plus deferred trigger. [VERIFIED]

Postgres `CHECK` can't reference another table. Use **service-layer validation (clean 400) + INSERT/UPDATE trigger (safety net)**. RLS `WITH CHECK` on `watchlists` provides a third layer.

**Subscription reduction:** mark watchlist rows **inactive (soft-hidden), not delete**. UX continuity wins; rows reappear if scope is restored. Append-only, matches doc 3 principle 1.

### 10. Admin-as-support — impersonation token, not role bypass. [VERIFIED]

**Recommend impersonation token** (over role bypass on admin endpoints). Server mints a 5-min token with `(user_id=admin, impersonating=client_X)`; middleware sets `app.user_id = client_X` and `app.impersonating_admin_id = admin_id`. Every query runs through **client RLS** — same code path as production. One audit row per token mint. Reuses repository layer unchanged. Role bypass + app-level filter is one missed `WHERE` from a cross-tenant leak.

---

## D. Test scaffolding

### 11. Test layout that catches the bugs that matter. [VERIFIED]

Directory:

```
tests/
  isolation/
    conftest.py          # two_clients, admin_ctx, scope factories
    test_private_state_isolation.py
    test_corpus_subscription_isolation.py
    test_admin_impersonation_audit.py
    test_aggregate_leak_vectors.py
```

Bugs the tests **must** catch:

- A's watchlist visible to B via direct/list/by-id (must 404, not 403 — 403 leaks existence).
- Join to corpus that forgets subscription scope returns out-of-scope rows.
- `count(*)` under client role returns table-wide count (RLS handles, but prove it).
- `RETURNING` on insert returns row no longer in scope after reduction.
- 500 traceback leaks document title.
- UK client's "last 7 days discovery" returns zero EU rows.
- Admin impersonation logs exactly one audit row per session; row is FK-immutable.

Each test uses two distinct `AsyncSession`s (no `SET LOCAL` swapping). Add a **Hypothesis property test**: `(N clients × M subscriptions × K writes)` → each client's reads ⊆ (their writes ∪ scope-allowed). Catches the bugs you didn't think of.

### 12. CI — testcontainers, no socket. [VERIFIED]

- **Testcontainers** over GHA service container — same fixture works on dev laptop and CI.
- `tests/isolation/` is a **required check** on branch protection. No shared long-lived Postgres (masks flake).
- Alembic runs in the engine fixture; RLS policies live in `alembic_utils.PGPolicy` (finding 13).

---

## E. Risks not addressed by the current design

These are leak vectors the docs do not currently name. Each needs a decision before code.

### 13. Manage RLS policies as Alembic migrations from day one. [VERIFIED]

Docs don't say how policies are versioned. Traps: hand-created policies drift, `DROP TABLE` silently drops policies, environments diverge. Use [`alembic_utils.PGPolicy`](https://github.com/olirice/alembic_utils) (or [Delfina `rls`](https://github.com/DelfinaCare/rls)); see [Adriano Vieira's guide](https://www.adrianovieira.eng.br/en/posts/architecture/row-level-security-sqlachemy-alembic-guide/). Lock in: all policies in `app/db/policies.py`, autogenerated migrations; raw `op.execute("CREATE POLICY")` only as escape hatch.

### 14. Cache-key poisoning. [SUSPECTED]

If CDN / `Cache-Control: public` ever hits a scoped response, that's a cross-tenant leak. **Decision:** all per-user and scope-filtered responses are `Cache-Control: private, no-store`. Future Redis cache keys must include `user_id` — code-review checklist item.

### 15. Error-message leakage. [VERIFIED]

404 vs 403 leaks existence. **Decision:** repo `get_by_id` returns `None` for both "doesn't exist" and "not yours"; API returns 404 either way. Same for "can't watch that document". Document in API conventions.

### 16. Sequence / serial ID information leak. [VERIFIED]

Serial IDs reveal inter-tenant write rates via the id gap. **Decision: UUIDv7** for all private-state PKs (time-ordered → index-friendly, opaque → no leakage). Corpus tables keep serials (not user-scoped).

### 17. Timing side-channels on subscription enforcement. [SUSPECTED]

A UK client querying an EU `change_event_id` must have the same latency as a non-existent id. **Decision:** PK lookups on corpus tables run one combined `WHERE id = :id AND <scope>` query — planner sees missing and out-of-scope identically.

### 18. Aggregate queries under RLS — correct but worth testing. [VERIFIED]

Aggregates under RLS are scoped — correct but counterintuitive. Risk: admin dashboard accidentally running as client mode shows UK-scoped numbers we read as global. Add explicit assertion test.

### 19. Background workers — the ingestion service is a privileged actor. [VERIFIED]

Worker is corpus-global. **It must not see private-state tables.** Two roles: `ingestion_worker` (writes corpus, zero on private-state) and `api_app` (reads corpus via scope policy, full private-state via RLS). `REVOKE ALL ON ALL TABLES IN SCHEMA private_state FROM ingestion_worker`. A worker bug cannot leak a watchlist because the role can't see it.

### 20. Backup / restore paths bypass RLS. [VERIFIED]

`pg_dump` bypasses RLS (expected). Risk: restore to staging without re-applying grants leaves RLS effectively off. **Decision:** restore script ends with `FORCE ROW LEVEL SECURITY` + role REVOKEs. In runbook before demo.

### 21. Logs and observability. [SUSPECTED]

Logs containing client A's row data must not be readable by a process serving B. Mostly an Application Insights config concern. Flag it: when adding observability, no client-facing surface shows server logs.

### 22. Connection-string secrets and role boundaries. [VERIFIED]

API connects as `api_app`, never as superuser or schema owner. Otherwise `FORCE RLS` and `BYPASSRLS` don't mean what we think. Four roles: `schema_owner` (Alembic, out-of-band), `api_app` (requests), `ingestion_worker` (worker), `admin_bypass` (operator-mode admin only).

---

## Phase 2 checklist — decisions to lock in before any RLS code

1. PgBouncer: off by default; if on, transaction mode only.
2. Every API request = explicit `BEGIN` + `SET LOCAL app.user_id` / `app.user_role` / `app.subscription_id` + COMMIT.
3. SECURITY DEFINER `app_private.current_scope()` function in a private schema with `search_path = ''`; revoke from client role.
4. Roles: `schema_owner`, `api_app`, `ingestion_worker`, `admin_bypass`. Document grants.
5. Admin = `BYPASSRLS` role for operator mode; impersonation token for support mode. Impersonation always writes one audit row.
6. Two-table subscription model: `subscriptions` + `subscription_scopes`. Append-only.
7. Watchlist ⊂ subscription enforced by service layer + trigger. Subscription reduction = soft-hide watchlist entries, not delete.
8. Repository layer: `user_id` mandatory; one repo per table; no `**kwargs` WHERE.
9. `text()` allowed only in `app/db/session.py` (Ruff TID banned-api allowlist + grep hook + architectural pytest).
10. RLS policies live in `app/db/policies.py` as `alembic_utils.PGPolicy` objects; migrations autogen.
11. UUIDv7 primary keys on every private-state table.
12. All per-user responses: `Cache-Control: private, no-store`.
13. 404 (not 403) for any "out of scope" or "not yours" case in the corpus and private-state surfaces.
14. PK lookups on corpus tables always run scope predicate in the same `WHERE` — never branch.
15. Test suite: two `AsyncSession`s per test, testcontainers Postgres 17, TRUNCATE between tests, isolation tests required-check in CI.
16. Hypothesis-driven property tests over `(N clients × M subscriptions × K writes)`.
17. Worker role has zero access to private-state tables (`REVOKE ALL`).
18. Restore runbook re-applies `FORCE ROW LEVEL SECURITY` and role revokes as final step.

---

## Sources

Primary: [PG 17 RLS](https://www.postgresql.org/docs/17/ddl-rowsecurity.html); [PgBouncer config](https://www.pgbouncer.org/config.html); [Imfeld RLS notes](https://imfeld.dev/notes/postgresql_row_level_security); [Bytebase RLS footguns](https://www.bytebase.com/blog/postgres-row-level-security-footguns/); [Supabase RLS perf](https://supabase.com/docs/guides/troubleshooting/rls-performance-and-best-practices-Z5Jjwv); [makerkit RLS](https://makerkit.dev/blog/tutorials/supabase-rls-best-practices); [SQLAlchemy 2.0 pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html); [alembic_utils](https://github.com/olirice/alembic_utils); [Vieira RLS+Alembic](https://www.adrianovieira.eng.br/en/posts/architecture/row-level-security-sqlachemy-alembic-guide/); [Rico Fritzsche RLS multi-tenancy](https://ricofritzsche.me/mastering-postgresql-row-level-security-rls-for-rock-solid-multi-tenancy/); [pganalyze RLS Rails](https://pganalyze.com/blog/postgres-row-level-security-ruby-rails).
