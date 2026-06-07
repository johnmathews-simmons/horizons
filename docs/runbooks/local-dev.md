# Local development

*Last revised: 2026-06-07.*
*Path: docs/runbooks/local-dev.md.*

Boot the Horizons stack on your laptop: Postgres + API + webapp. The
worker is documented at the end but is **not** part of the default
local flow — it needs Azure Blob and Lawstronaut credentials and there
is no local-emulator path.

The Playwright e2e (`packages/horizons-webapp/e2e/README.md`) reuses
the DB + API + webapp boot from this runbook with three substitutions:
`seed_e2e.py` instead of `seed_curated_set.py`, `npm run build` + `vite
preview` instead of `npm run dev`, and the test as a third terminal.

## Prerequisites

- `uv` (workspace + Python deps).
- `docker` (for the Postgres container).
- `node` 22 + `npm` (webapp).
- One-time setup after cloning:

  ```bash
  uv sync
  uv run pre-commit install
  (cd packages/horizons-webapp && npm install)
  ```

  If `pre-commit install` says `Cowardly refusing to install hooks with
  core.hooksPath set`, run `git config --unset core.hooksPath` and
  retry. Verify with `ls .git/hooks/pre-commit`.

## 1. Postgres

Postgres 18 is required — `uuidv7()` is a v18 built-in and migrations
will fail on older majors.

```bash
docker run --rm -d --name horizons-pg \
  -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 \
  postgres:18-alpine

export HORIZONS_DB_URL='postgresql+psycopg://postgres:postgres@localhost:5432/postgres'
uv run alembic upgrade head
```

The migrate step uses the **sync** `+psycopg` URL because alembic +
seed scripts are sync. The API later uses the **async** `+asyncpg` URL
against the same database — both are correct, neither is a typo. See
`docs/runbooks/migrations.md` for the migration tree and expand-contract
policy, and `packages/horizons-core/src/horizons_core/db/roles.md`
for the role model.

## 2. Seed data

For a realistic local stack matching what the demo shows, use the
curated set:

```bash
uv run python scripts/seed_curated_set.py
```

Add `--stage-synthetic-v2` to also stage every hand-authored v2
document under `data/samples/synthetic_v2/` (currently 8 pairs;
parked at `next_poll_at = 2026-12-31` so the worker can't claim
them). Pass `--dry-run` to parse + align without writing.

For a minimal multi-tenant fixture (UK client + EU client + admin,
three documents, scripted change events) use `seed_e2e.py` instead —
that's what the Playwright test runs against. `--teardown` purges.

To create the three demo accounts used in `docs/runbooks/demo.md`:

```bash
uv run python packages/horizons-api/scripts/create_demo_accounts.py
```

See `docs/runbooks/demo-accounts.md` for password overrides and the
no-downgrade guard.

## 3. API (terminal B)

The API needs five env vars: a JWT keypair, issuer, audience, and CORS
origins. Generate an ephemeral keypair for local dev (these are throwaway
— do not reuse them in any deployed environment):

```bash
# One-liner to mint and export an RSA-2048 keypair for this shell.
python - <<'PY'
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
priv = k.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
pub = k.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()
import shlex
print(f"export HORIZONS_JWT_PRIVATE_KEY_PEM={shlex.quote(priv)}")
print(f"export HORIZONS_JWT_PUBLIC_KEY_PEM={shlex.quote(pub)}")
PY
# Paste the two `export` lines into your shell.

export HORIZONS_JWT_ISSUER='horizons-local'
export HORIZONS_JWT_AUDIENCE='horizons-local'
export HORIZONS_CORS_ORIGINS='http://localhost:5173'

# Async driver for the running API:
export HORIZONS_DB_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/postgres'

uv run uvicorn horizons_api.app:create_app --factory --port 8000 --reload
```

`--reload` is the only flag that differs from production. Drop it if
you're profiling. The startup will fail loudly if any of the five
required env vars is unset — that's deliberate (see the docstring at
the top of `packages/horizons-api/src/horizons_api/config.py`).

Smoke test: `curl http://localhost:8000/healthz` should return `200`.

## 4. Webapp (terminal C)

```bash
cd packages/horizons-webapp
npm run dev
```

Vite serves on `http://localhost:5173`. The app fetches `/config.json`
at boot; the committed default already points `apiBaseUrl` at
`http://localhost:8000`, so no config edits are needed for the standard
flow.

If you want to point the dev webapp at a deployed API instead, edit
`packages/horizons-webapp/public/config.json` locally (it is committed,
so don't push that change).

## Cleanup

```bash
docker rm -f horizons-pg
```

The webapp `dist/`, alembic state, and exported env vars are
process-local; closing the terminals is enough.

## Worker (optional, not local-friendly)

The ingestion worker (`packages/horizons-ingestion`) is a long-running
asyncio loop per ADR-0001. It requires:

- `HORIZONS_DB_URL` — same Postgres as the API.
- `HORIZONS_INGESTION_BLOB_ACCOUNT_URL` — an Azure Blob account URL
  (`https://<acct>.blob.core.windows.net`). There is no local emulator
  path wired up; the `AzureBlobStore` uses `DefaultAzureCredential`.
- `LAWSTRONAUT_EMAIL` / `LAWSTRONAUT_PASSWORD` — real Lawstronaut
  credentials (no fixture mode).

Boot:

```bash
uv run python -m horizons_ingestion
```

The worker serves `/healthz` on port 8001 by default (see
`ClaimLoopConfig`) and drains in-flight work on SIGTERM. For local UI
work, **skip the worker** and rely on `seed_curated_set.py` /
`seed_e2e.py` to populate the database — the API surface and SPA do
not depend on the worker being running.

A local-friendly worker mode (filesystem blob store + Lawstronaut
fixture replay) is post-demo work; track it in the work-unit roadmap
if you need it.

## Where to go next

- `docs/runbooks/migrations.md` — alembic tree, role model, how to
  author a new migration.
- `docs/runbooks/seeding.md` — what each seed script writes and why.
- `docs/runbooks/demo-accounts.md` — the three demo accounts and how
  passwords are rotated.
- `docs/runbooks/deploy.md` — the staging / production deploy
  pipeline (Bicep + container app updates + SPA upload).
- `packages/horizons-webapp/e2e/README.md` — Playwright e2e specifics
  on top of this runbook.
