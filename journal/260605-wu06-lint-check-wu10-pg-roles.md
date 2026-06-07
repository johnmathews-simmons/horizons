# 2026-06-05 — WU0.6 lint:check + WU1.0 Postgres role model

*Last revised: 2026-06-05.*
*Path: journal/260605-wu06-lint-check-wu10-pg-roles.md.*

Second session of the day. Closed the one known gap in Track 0 and opened
Track 1.

## What shipped

### WU0.6 — Webapp CI uses no-fix lint scripts (`56114d4`)

The previous CI workflow ran `npm run lint`, whose underlying
`lint:oxlint` / `lint:eslint` invocations both pass `--fix`. That made CI
auto-correct lint issues in the ephemeral runner workspace rather than
failing the build — exactly the regression-mask `--fix` is famous for.

Fix is one-line CI swap backed by new script aliases:

- `lint:check`         → `run-s lint:check:*`
- `lint:check:oxlint`  → `oxlint .`
- `lint:check:eslint`  → `eslint . --cache`

The original `lint` / `lint:oxlint` / `lint:eslint` scripts keep `--fix`
for local developer ergonomics.

**Caveat noted:** because `npm-run-all2`'s `lint:*` glob matches
single-colon children, running `npm run lint` locally now also invokes
`lint:check` after the `--fix` variants. Harmless (re-runs against
just-fixed code) but roughly doubles local lint time. Left unchanged for
now — renaming `lint:check` to dodge the glob would solve it cleanly.

### WU1.0 — Postgres role model via Alembic (`0befda9`)

First Track 1 unit. Bundled the Alembic harness setup into the same
commit as the role-model migration (Q4 decision).

Four roles, all `NOLOGIN` (Q3 decision — permission containers, not
connectable accounts):

| Role | BYPASSRLS | Purpose |
| --- | --- | --- |
| `schema_owner` | no | Owns DDL objects. Used by migrations only. |
| `api_app` | no | Public API service. Reads/writes within RLS. |
| `ingestion_worker` | no | Ingestion writer; no client-private reads. |
| `admin_bypass` | **yes** | Audited admin escape hatch. |

Per-environment LOGIN users will be provisioned out-of-band by
ops/IaC and granted the appropriate role. This separates permission
grants (in migrations, slow-changing, same per env) from connection
credentials (in secret storage, rotate independently).

Harness layout (Q1/Q2 decisions):

- `alembic.ini` at repo root, `script_location =
  packages/horizons-core/migrations`.
- `migrations/env.py` reads `HORIZONS_DB_URL` from the environment —
  credentials never live in `alembic.ini`.
- Sync driver `psycopg[binary]` for Alembic; the app continues to use
  `asyncpg` separately.
- Date-prefixed sequential revisions (`0001_role_model.py`,
  `revision = "0001"`). Merge-conflict risk acknowledged; mitigation
  will be a tiny registry doc if/when it bites.
- `target_metadata = None` until ORM models land — autogenerate gets
  wired up later.
- `script.py.mako` template rewritten to emit modern PEP-604 unions
  (`str | None` instead of `Optional[str]`) and `from __future__ import
  annotations`, matching the rest of the codebase.

Migration body uses `DO $$ ... IF NOT EXISTS (SELECT 1 FROM pg_roles
...) ... CREATE ROLE ...` blocks for idempotency, then `COMMENT ON
ROLE` for self-documenting role intent.

Integration test (`tests/test_role_model_migration.py`) applies the
migration tree against a fresh testcontainers PG 17 and asserts each
role's `rolbypassrls` / `rolcanlogin` / `rolcreatedb` / `rolcreaterole`
attributes against a fixture dict.

Role-model design notes — why NOLOGIN, why two non-bypass app roles,
why `admin_bypass` is separate, how `SET LOCAL app.user_id` will key
into RLS in WU1.5 — captured in
`packages/horizons-core/src/horizons_core/db/roles.md`.

## Discoveries / decisions worth remembering

1. **pytest-asyncio session-scoped engine + function-scoped event
   loop don't mix.** The first version of the role-model test was
   async and used the existing session-scoped `engine` fixture from
   `tests/conftest.py`. It failed with `RuntimeError: ... attached to a
   different loop` because pytest-asyncio's default loop scope is
   function. Switching the test to sync (Alembic is sync-only anyway,
   and the assertion query is tiny) avoided changing the shared async
   fixture. If future tests need a long-lived async engine across
   tests, the right move is `asyncio_default_fixture_loop_scope =
   "session"` in `pyproject.toml`, not patching each test.
2. **CREATE ROLE has no `IF NOT EXISTS`.** Hence the DO-block guard.
   Future migrations that touch role attributes will need similar
   defensive patterns.
3. **Alembic `file_template` quoting.** `%%(rev)s_%%(slug)s` — the
   double `%%` is required because the file is read through
   ConfigParser, which uses `%` for interpolation.

## Still open

1. **Branch protection on `main` requires both CI checks.** Needs
   manual GitHub UI / API action — neither workflow can self-enable
   it. Required status checks: `Python CI / lint, typecheck, test`
   and `Webapp CI / lint, build, test`.
2. **Next track-1 unit (WU1.1).** Per the prior plan, this is the
   first set of ORM tables for tenancy: `clients`, `subscriptions`,
   `subscription_scopes`. Will need autogenerate wired up (set
   `target_metadata` in `env.py` to the declarative `Base.metadata`
   from the models package).
3. **The `lint:check` glob-collision quirk** in WU0.6 is not a
   blocker but is annoying for local dev. Possible follow-up rename.

## State of the world at session end

- Track 0 (Repo scaffold + tooling) — closed.
- Track 1 (Tenancy spine) — first unit (WU1.0) landed. Five units to go
  (per the prior plan): WU1.1 ORM tables, WU1.2 RLS policies, WU1.3
  repository layer, WU1.4 connection-pool session GUCs, WU1.5
  integration tests across two tenants.

Local sweep on `main` after this session:

```
uv run pytest --cov                                  → 6 passed, htmlcov/ written
uv run ruff check .                                  → All checks passed
uv run pyright                                       → 0 errors, 2 warnings (testcontainers stubs)
uv run pre-commit run --all-files                    → all hooks passed
cd packages/horizons-webapp && npm run lint:check    → 0 warnings, 0 errors
```
