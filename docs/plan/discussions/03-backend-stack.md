# Backend stack & ingestion: framework / library recommendations

*Audit date: 2026-06-04. Pre-code, post-design. Reviewer: senior backend engineer.*

Turns the design chain (docs 1–4) into concrete library choices for the Python service layer, calls out implementation gotchas, ranks top risks. **[VERIFIED]** = checked against current public docs/benchmarks; **[SUSPECTED]** = reasoned, not directly confirmed.

---

## A. API framework

### 1. FastAPI vs Litestar vs Django REST vs Flask — pick FastAPI [VERIFIED]

**Choice: FastAPI.** Runner-up: Litestar.

Constraints from doc 4: async-native, type-driven (Pydantic everywhere), OpenAPI generation. Both FastAPI and Litestar satisfy all three; Django REST is sync-first and ORM-tied; Flask needs Pydantic + async + OpenAPI bolted on manually.

Litestar's pitch is real (msgspec is 10–20× faster than Pydantic v2; DI is more explicit). What decides it here:

- **Onboarding.** FastAPI has 100× more Stack Overflow answers for auth/middleware/OpenAPI customisation — the things a senior data eng new to Python web frameworks will Google first.
- **3s p95 budget (doc 3) is Postgres-dominated.** 2× serialisation buys nothing on a corpus-scope differential touching thousands of clauses. Litestar wins synthetic benchmarks; FastAPI wins production decisions.
- **Ecosystem.** OTel instrumentation, fastapi-users, SQLAlchemy integration — all FastAPI-first.

Code shape:

```python
# api/main.py
from fastapi import FastAPI, Depends
from .deps import get_session, current_user

app = FastAPI(title="Horizons API", version="1.0.0")

@app.get("/v1/changes")
async def changes(
    scope: ScopeQuery = Depends(),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Page[ChangeEvent]: ...
```

Revisit Litestar after the demo if measured serialisation cost on differential responses becomes the bottleneck. Treat it as a swappable router, not a foundational bet.

### 2. ASGI server — uvicorn [VERIFIED]

**Choice: uvicorn (behind gunicorn for prod worker management on ACA).** Runner-up: Granian.

Granian is 20–50% faster on CPU-bound synthetic benchmarks; for CRUD APIs the gap collapses to ~10%. Horizons is DB-bound by an order of magnitude — the 10% does not pay for the operational risk of a younger Rust binary. Revisit only if `/metrics` shows ASGI as a measurable share of the request budget.

ACA-specific: run `uvicorn --workers 1` per container and let ACA's HTTP scale rule add replicas. Don't run gunicorn-multi-worker inside a container — it fights ACA autoscaling.

---

## B. Postgres access layer

### 3. Driver — psycopg3 (async) [VERIFIED]

**Choice: psycopg3 (`psycopg[binary,pool]`) in async mode.** Runner-up: asyncpg.

Asyncpg is ~28% faster on raw QPS. But:

- **`LISTEN/NOTIFY` for runtime-config invalidation.** Doc 2 wants shingling-*k* / signature size / similarity threshold tunable at runtime; admins tweak in UI, worker picks up without restart. psycopg3 supports this ergonomically in async; asyncpg's API is more bespoke.
- **Server-side types.** psycopg3 carries libpq's type system — native JSONB, ranges, `tstzrange` for `valid_from/valid_to`. asyncpg needs codec configuration.
- **psycopg3 pipeline mode** closes most of the throughput gap. SQLAlchemy 2.0 supports both dialects equally.

Throughput delta is invisible at demo scale (50 documents, handful of clients).

### 4. Query layer — SQLAlchemy 2.0 async + thin repositories [VERIFIED]

**Choice: SQLAlchemy 2.0 async Core + a hand-rolled repository layer; ORM mappers only where they pay for themselves (private-state tables).** Runner-up: raw psycopg with hand-rolled repos.

The repo pattern is *load-bearing* per doc 4 — every private-table access takes `user_id` as a required argument. Typed result objects via Pydantic at the boundary. Ranked:

- **SQLAlchemy 2.0 Core + selective ORM** — Core gives composable SQL expressions (essential for the subscription-join applied to every corpus query); ORM mappers for the small set of private-state CRUD tables.
- **SQLModel** — half-abandoned per commit log; do not adopt.
- **Raw psycopg + hand-rolled repos** — loses Alembic autogen and the expression language we need for runtime-built scoped SELECTs.
- **Piccolo / Tortoise** — small ecosystems, no Alembic, no RLS prior art.

The Core/ORM split:

```python
# core/repo/changes.py — corpus-side, expression-builder style
async def list_changes(
    session: AsyncSession, *, scope: Scope, subscription: Subscription, page: PageReq
) -> Page[ChangeEvent]:
    stmt = (
        select(change_events)
        .where(_scope_predicate(scope))
        .where(_subscription_predicate(subscription))  # belt
        .order_by(change_events.c.detected_at.desc())
        .limit(page.size).offset(page.offset)
    )
    # RLS provides the braces (database-side); this is the belt (app-side).
    rows = (await session.execute(stmt)).all()
    return Page(items=[ChangeEvent.model_validate(r) for r in rows], ...)

# core/repo/watchlists.py — private-state, ORM style
async def get_watchlist(session: AsyncSession, *, user_id: UUID) -> list[Watchlist]:
    return (await session.scalars(select(Watchlist).where(Watchlist.user_id == user_id))).all()
```

The repo layer requires `user_id` as a kwarg on every private-state function; the lint rule against raw SQL backs that signature. Pair with a `before_cursor_execute` event that asserts `SET LOCAL app.user_id` was issued in the current transaction.

### 5. Migrations — Alembic [VERIFIED]

**Choice: Alembic, with RLS policies managed as hand-written migration ops.** Runner-up: Atlas.

Atlas's declarative model is nicer for RLS in isolation (HCL + reconcile). But Alembic is de-facto with SQLAlchemy 2.0, has actively-maintained RLS libraries (DelfinaCare/rls registers RLS metadata on the declarative base and hooks autogenerate to emit `CREATE/DROP POLICY` ops), and the team will know it. Atlas is a second tool with a younger Python integration story.

Sketch:

```python
# alembic/versions/0003_rls_watchlists.py
def upgrade():
    op.execute("ALTER TABLE watchlists ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY watchlists_isolation ON watchlists
        USING (user_id = current_setting('app.user_id')::uuid);
    """)

def downgrade():
    op.execute("DROP POLICY watchlists_isolation ON watchlists;")
    op.execute("ALTER TABLE watchlists DISABLE ROW LEVEL SECURITY;")
```

**Caveat:** Alembic runs privileged; RLS policies must allow it or it must `BYPASSRLS`. Standard: `migrator` role with `BYPASSRLS`; `client`/`admin` are non-bypass roles set per request.

### 6. Connection pooling — app-level pool + transaction-scoped session vars; PgBouncer only later [VERIFIED]

**Choice:** SQLAlchemy `create_async_engine` with its default pool (per-process), sized to ACA replica count. **No PgBouncer for the demo.**

**PgBouncer in transaction-pooling mode does not preserve `SET` state across transactions** — two transactions from the same client may land on different server connections. Session-scoped `SET app.user_id` will silently leak rows under load.

Mitigations:

1. **`SET LOCAL app.user_id` inside the request transaction**, never plain `SET`. `SET LOCAL` is transaction-scoped and survives transaction pooling. Wire via a SQLAlchemy `begin()` middleware that issues `SET LOCAL` as the *first* statement of every transaction.
2. **Skip PgBouncer for the demo.** Azure DB Flex has a built-in pooler; leave disabled until a measured problem materialises. Demo scale is well below Postgres connection limits.
3. When added later, **session-pooling mode is the safe default for RLS**; transaction mode only after every code path is audited for `SET LOCAL`.

---

## C. Ingestion worker

### 7. Scheduler — SQL-driven `SELECT FOR UPDATE SKIP LOCKED` loop, run as an ACA Job [VERIFIED]

**Choice:** Long-running asyncio loop in a worker container, polling a `document_poll_schedule(document_id, next_poll_at, cadence, last_status)` table via `SELECT ... FOR UPDATE SKIP LOCKED LIMIT N`, updating `next_poll_at` on commit. Deploy as a long-running container (better debuggability for the demo) or an ACA Job on cron.

Runners-up: **APScheduler** (in-process, no horizontal scale); **ARQ** (adds Redis you wouldn't otherwise need); **Celery** (heavy, broker + result backend, overkill); **ACA Jobs alone** (fine for cron but `SKIP LOCKED` is still what makes multi-replica safe). The pattern in doc 4 is production-proven in Solid Queue (Rails) and pg-boss (Node).

Concrete shape:

```python
# worker/run.py
async def claim_due(session: AsyncSession, batch: int) -> list[PollTask]:
    stmt = text("""
        SELECT document_id, cadence
        FROM document_poll_schedule
        WHERE next_poll_at <= now()
        ORDER BY next_poll_at
        FOR UPDATE SKIP LOCKED
        LIMIT :batch
    """)
    return (await session.execute(stmt, {"batch": batch})).all()

async def loop():
    while True:
        async with session_factory() as session, session.begin():
            tasks = await claim_due(session, batch=10)
            for t in tasks:
                await poll_document(session, t)  # see §8
            # next_poll_at update + all changes commit together
        await asyncio.sleep(JITTER + 5)
```

- **Bursty polling.** Staggered `next_poll_at` (±10% jitter) on seeding; cap batch size to keep transactions short.
- **Retry + backoff.** Transient failure rolls back; bump `next_poll_at` with exponential backoff via `retry_count`/`last_error` columns. Persistent failure beyond budget → `ingestion_incident` row + status `paused`.
- **Horizontal scaling.** `SKIP LOCKED` is the contract; multiple replicas safe.
- **Dead-letter.** `paused` status + `ingestion_incident` table; admins resume via API endpoint.

### 8. Atomic-with-blob — outbox/saga, not "everything in one transaction" [SUSPECTED]

Doc 4 implies blob upload + Postgres commit are atomic. They are not — blob upload is an external HTTP call outside the transaction; 2PC between Postgres and Azure Blob is not a real option.

**Recommended pattern: upload-then-commit, content-addressed.** Upload markdown to `originals/<sha256>.md`. Idempotent: re-upload overwrites same bytes. Then write the Postgres row referencing the hash. If commit fails: orphan blob (harmless, swept later). If upload fails: row is never written. **No torn state visible to readers.**

Runner-up: outbox table (`pending_blob_uploads`) with sweeper; more moving parts. Needed companion: a daily sweeper deletes `originals/<sha256>.md` blobs unreferenced by `document_versions` and older than 24h. Alignment + change_events remain inside the Postgres transaction — those are purely Postgres-side and rightly atomic.

Amend doc 4 to describe this pattern explicitly; the current wording implies a guarantee that isn't real.

### 9. Lawstronaut HTTP — httpx + stamina; pre-emptive token refresh [VERIFIED]

**httpx async** (fetch script already uses it). Retries: **stamina** over tenacity — stamina is an opinionated wrapper around tenacity with structlog + Prometheus hooks built in; "does the right thing by default." Tenacity's flexibility is overkill for the small set of retry shapes we need.

```python
import stamina, httpx

@stamina.retry(on=(httpx.TransportError, httpx.HTTPStatusError),
               attempts=4, wait_initial=1.0, wait_jitter=2.0)
async def get_contents(client, token, **params): ...
```

**Token refresh.** Lawstronaut quirk: `refresh_token` is itself the bearer; `expires_in` is 1800s. Don't lazily refresh on 401 — behaviour at expiry is undocumented. Use a `TokenManager` with `near_expiry` threshold (60s before TTL). `scripts/fetch_fixtures.py` already does this — lift into `core/lawstronaut/auth.py`. Add a single in-process lock around refresh (concurrent refresh = potential outage). Multiple replicas refresh independently; login is idempotent.

---

## D. Clause alignment

### 10. Markdown parser — markdown-it-py with a custom token-stream walker [VERIFIED]

**Choice: markdown-it-py.** Runner-up: mistune.

Both produce ASTs (mistune natively; markdown-it-py via the token stream). markdown-it-py wins on CommonMark strictness, an easier-to-walk token stream for attaching inline `**N\.**` markers as clause boundaries, and a more active plugin ecosystem.

The hard problem is the **two substrates**: heading-anchored (IE: `**PART N**` / `**N\.**` / `(N\)`) and inline-numbered (CZ: `ČÁST PRVNÍ` / `Čl. I` / `1\.` with no markdown structure). Build a per-portal parser strategy (config table `portal_id → strategy`) so adding a jurisdiction is data, not code.

### 11. Shingling + MinHash + LSH — datasketch [VERIFIED]

**Choice: datasketch (latest 1.10.x).** Active, widely used, supports LSH, LeanMinHash for memory, optional Redis storage for cross-process LSH. No reason to roll our own.

Use LeanMinHash for persisted signatures. `MinHashLSH(threshold=0.7, num_perm=128)` as starting config (from doc 2's table; must be runtime-tunable). GPU via CuPy exists; irrelevant for ACA.

### 12. Alignment runs in-process; subprocess pool only if p99 hurts [SUSPECTED]

Agreed for the demo: alignment runs in-process. Memory profile:

- p99 5 MB ≈ 10k clauses (per doc 3); MinHash sigs ≈ 10 MB resident.
- Transient shingle sets dominate (*k*=5 over 5 MB ≈ 100k–500k shingles); use streaming `update_batch` instead of materialising full sets.
- LSH index of 10k items is sub-MB.

A single worker on one p99 doc stays under ~200 MB. **Concern:** concurrent 20MB outliers (US IRC, REACH annexes) will OOM. Mitigation: `max_concurrent_alignments` semaphore (start at 2/replica); spill to a "large-doc" pool later if observed. Do **not** start with a subprocess pool — premature optimisation.

---

## E. Auth

### 13. JWT library — PyJWT [VERIFIED]

**Choice: PyJWT.** Runners-up: authlib (if OIDC ever lands), python-jose (do not adopt).

PyJWT is actively maintained; FastAPI's official docs migrated to it from python-jose; python-jose's `ecdsa` dep has unfixed security advisories. No JWE needed — bearer tokens with the API holding the signing key is JWS+HS256. For the future managed-identity seam, authlib's `joserfc` is the migration target if Entra ID/OIDC ever ships.

### 14. Password hashing — argon2-cffi directly; do not use passlib [VERIFIED]

**Choice: argon2-cffi.** Runner-up: bcrypt (via the `bcrypt` package).

**passlib is effectively unmaintained and broken on Python 3.13.** Do not adopt. For a new B2B product with operator-provisioned accounts, argon2-cffi (PHC 2013 winner, GPU/ASIC-resistant) is the boring-good choice. `pwdlib` is a lightweight wrapper with a passlib-shaped API if useful; otherwise call argon2-cffi directly:

```python
from argon2 import PasswordHasher
ph = PasswordHasher()  # sensible defaults
hash = ph.hash(password)
ph.verify(hash, password)   # raises VerifyMismatchError
if ph.check_needs_rehash(hash):  # rotate params
    new = ph.hash(password)
```

### 15. Pluggable auth seam — a `TokenProvider` protocol [SUSPECTED]

A protocol with three methods plus a `Principal` resolver:

```python
# core/auth/provider.py
class TokenProvider(Protocol):
    async def issue_token(self, *, user_id: UUID, role: Role, ttl: timedelta) -> Token: ...
    async def verify_token(self, raw: str) -> Principal: ...   # raises AuthError
    async def revoke_token(self, raw: str) -> None: ...        # may be no-op for stateless JWT

@dataclass(frozen=True)
class Principal:
    user_id: UUID
    role: Role            # client | admin
    subscription_id: UUID | None   # populated for client role
    expires_at: datetime
    raw_token: str         # for downstream propagation if needed
```

Implementations: `LocalJwtProvider(secret_key, algorithm="HS256")` for the demo with revocation via a `revoked_jti` table checked on verify; `EntraIdProvider(tenant_id, client_id, jwks_uri)` later, verifying against Microsoft's JWKS with revocation delegated to the IdP. FastAPI side: a single `Depends(current_principal)` pulling the configured provider from app state. Swap is a config change.

---

## F. Observability

### 16. Stack — Azure Monitor OpenTelemetry Distro [VERIFIED]

**Choice: `azure-monitor-opentelemetry` distro + bundled FastAPI auto-instrumentation.** Runner-up: vanilla OTel + manual Azure Monitor exporter.

ACA's built-in Log Analytics ingests OTLP; the distro wires the rest. Single `configure_azure_monitor()` sets up traces + metrics + logs. **Trap:** call `configure_azure_monitor()` *before* importing FastAPI or auto-instrumentation silently no-ops.

For admin dashboards (rate / latency / error), read OTel metrics via Grafana's Azure Monitor data source, or use Azure Workbooks for the demo. Do **not** stand up Prometheus + `/metrics` *in addition to* OTel — pick one path. Caveat: distro + most instrumentations are still beta. Pin versions.

### 17. Structured logging — structlog [VERIFIED]

**Choice: structlog** with JSON renderer in prod, console in dev. Configure stdlib interop so library logs (httpx, sqlalchemy, alembic) share the pipeline. Killer feature: `contextvars`-bound context — request-scoped fields (`request_id`, `user_id`, `subscription_id`) propagate into every log line without manual passing. Critical for the admin support-views audit log (doc 4 open question).

```python
# core/log.py
import structlog
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)
```

Per-request middleware: `bind_contextvars(request_id=..., user_id=..., role=...)` at entry; `clear_contextvars()` at exit.

---

## G. Repo layout

### 18. Monorepo, four subdirectories [VERIFIED]

**Choice: monorepo.** Three services share a Postgres schema, repository layer, alignment library, and Lawstronaut client — reuse that polyrepo punishes with a private package index.

Layout:

```
horizons/
├── pyproject.toml              # workspace root
├── uv.lock
├── core/                       # shared library (not deployed)
│   ├── pyproject.toml
│   └── src/horizons_core/
│       ├── db/                 # engine, session, RLS middleware
│       ├── repo/               # repository pattern enforced here
│       ├── models/             # SQLAlchemy + Pydantic shared schemas
│       ├── auth/               # TokenProvider protocol + JWT impl
│       ├── lawstronaut/        # API client (lifted from scripts/)
│       ├── alignment/          # parser + minhash + DP
│       └── config/             # runtime-tunable parameters
├── api/                        # public REST service
│   ├── pyproject.toml
│   └── src/horizons_api/
│       └── ...
├── worker/                     # ingestion worker
│   ├── pyproject.toml
│   └── src/horizons_worker/
│       └── ...
├── webapp/                     # SPA — separate, may diverge in tooling
├── alembic/                    # migrations live at root (single DB)
├── scripts/
│   ├── fetch_fixtures.py
│   └── seed_demo.py
├── docs/
├── data/
├── tests/                      # cross-package integration tests
└── .github/workflows/          # build api / worker / webapp images
```

Shared `core` is essential: worker writes corpus tables, API reads them — they must agree byte-for-byte on schema. Polyrepo would inevitably skew.

### 19. uv workspaces — workspace at root, per-service `pyproject.toml` [VERIFIED]

**Choice: uv workspace** with `[tool.uv.workspace]` at root, members `core`, `api`, `worker`. Single `uv.lock`, single `.venv` for dev. Webapp is a separate Node/TS project — outside the workspace.

Root config:

```toml
# pyproject.toml
[tool.uv.workspace]
members = ["core", "api", "worker"]

[tool.uv.sources]
horizons-core = { workspace = true }
```

Each service depends on `horizons-core = { workspace = true }`. Docker build per service: `uv sync --package horizons_api` (etc.) → slim image with only that service's dependency closure.

---

## H. Top 5 implementation risks (ranked)

1. **RLS + connection pool silently leaks rows.** [VERIFIED] Biggest correctness hazard. PgBouncer in transaction-pooling mode, plain `SET` (not `SET LOCAL`), or a forgotten `app.user_id` set turn RLS into false confidence. *Mitigation:* enforce `SET LOCAL` in a SQLAlchemy `begin` event listener; the multi-user integration tests from doc 4 are non-optional in CI for every PR; ban PgBouncer transaction mode for the demo.

2. **Blob upload "atomicity" claim is false.** [VERIFIED] Doc 4's transaction paragraph oversells the guarantee — risk is torn state (blob without row, or row pointing at missing blob). *Mitigation:* upload-then-commit with content-addressed naming + orphan sweeper; amend doc 4. Fixable now, expensive later.

3. **Alignment OOM on 20MB+ outliers under parallel workers.** [SUSPECTED] US IRC / REACH annexes exceed per-doc budgets; concurrent outliers OOM the container. *Mitigation:* `max_concurrent_alignments` semaphore from day one; per-doc size cap with deferral to a "large doc" queue; stream shingles iteratively.

4. **Inline-numbered substrate (CZ-style) parser correctness.** [VERIFIED] Hand-rolled regex over body text; every jurisdiction is a new edge case. Mis-detected boundaries produce phantom `ADDED`/`REMOVED` events at every poll. *Mitigation:* fixture-driven tests (one per substrate from `data/samples/`); parser strategy registered per portal in config; confidence-floor on auto-detected boundaries until tuned.

5. **Lawstronaut auth/format drift mid-demo.** [VERIFIED] operational-notes already records 7+ doc-vs-reality discrepancies (`refresh_token` *is* the bearer, malformed ms in `publication_date`, `document_id` type fluid). A field-name tweak mid-demo breaks ingestion silently. *Mitigation:* anti-corruption layer in `core/lawstronaut/` — typed Pydantic adapters with explicit aliases and tolerant validators; raw response into `upstream_raw` JSONB for forensics; alert on adapter validation failures.

---

## Sources

- [Litestar vs FastAPI – byteiota 2026](https://byteiota.com/litestar-vs-fastapi-python-speed-test-2026-analysis/)
- [Litestar vs FastAPI – Better Stack](https://betterstack.com/community/guides/scaling-python/litestar-vs-fastapi/)
- [SQLAlchemy asyncio docs](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [psycopg3 vs asyncpg – Fernando Arteaga](https://fernandoarteaga.dev/blog/psycopg-vs-asyncpg/)
- [Psycopg2 vs Psycopg3 benchmark – Tiger Data](https://www.tigerdata.com/blog/psycopg2-vs-psycopg3-performance-benchmark)
- [RLS with SQLAlchemy and Alembic – Adriano Vieira](https://www.adrianovieira.eng.br/en/posts/architecture/row-level-security-sqlachemy-alembic-guide/)
- [DelfinaCare/rls](https://github.com/DelfinaCare/rls)
- [Postgres RLS docs](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [PgBouncer features](https://www.pgbouncer.org/features.html)
- [Daniel Imfeld – Postgres RLS notes](https://imfeld.dev/notes/postgresql_row_level_security)
- [datasketch docs](https://ekzhu.com/datasketch/lsh.html)
- [uv workspaces docs](https://docs.astral.sh/uv/concepts/projects/workspaces/)
- [Azure Monitor OpenTelemetry Distro for Python](https://learn.microsoft.com/en-us/python/api/overview/azure/monitor-opentelemetry-readme?view=azure-python)
- [Enable OpenTelemetry in App Insights](https://learn.microsoft.com/en-us/azure/azure-monitor/app/opentelemetry-enable)
- [Migrating from PyJWT – joserfc](https://jose.authlib.org/en/migrations/pyjwt/)
- [FastAPI discussion: python-jose abandoned](https://github.com/fastapi/fastapi/discussions/9587)
- [stamina](https://github.com/hynek/stamina)
- [tenacity](https://github.com/jd/tenacity)
- [SKIP LOCKED job queues – Netdata](https://www.netdata.cloud/academy/update-skip-locked/)
- [Granian vs Uvicorn vs Hypercorn](https://blog.hashhackers.com/blog/granian-uvicorn-asgi/)
- [structlog stdlib integration](https://www.structlog.org/en/stable/standard-library.html)
- [markdown-it-py architecture](https://markdown-it-py.readthedocs.io/en/latest/architecture.html)
- [mistune docs](https://mistune.lepture.com/)
