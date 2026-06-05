# WU6.4 — Migration ACA Job

*Session 2026-06-05. Branch `worktree-wu6.4-6.5-6.6-migrations-docs-drift` (Session D).*

The `Microsoft.App/jobs` resource that runs `alembic upgrade head`
against the target Postgres before `deploy.yml` (WU6.3) shifts traffic
to a new revision. Provisioning only — `deploy.yml` is what kicks the
job. The image is the existing API image with a command override; no
new Dockerfile.

## What shipped

```
infra/modules/migration-job.bicep    Microsoft.App/jobs, manual trigger,
                                     replicaTimeout: 600s, UAMI-mounted,
                                     command override → alembic.
infra/main.bicep                     Composes the new module (block 7c)
                                     after the worker block; new
                                     `migrationJobName` output.
```

`az bicep build --file infra/main.bicep` → exit 0, zero warnings.

## Architectural decisions reflected

1. **Reuse `ghcr.io/johnmathews/horizons-api:latest`, don't build a
   migration image.** The API image already bakes in horizons-core
   (which owns the migration tree under
   `packages/horizons-core/migrations/`) plus `alembic.ini` at the
   workspace root — `uv sync --package horizons-api` (WU6.2) resolves
   to a dep closure that includes alembic transitively. A dedicated
   migration image would duplicate the layers and the per-image GHCR
   cache scope for zero benefit. The Bicep module documents this in
   its header so the next person doesn't reach for a `migrate/`
   Dockerfile.
2. **Command override, not a separate entrypoint.** The container's
   default command is uvicorn (from the API Dockerfile's `CMD`); the
   Job overrides via `command: ['uv', 'run', 'alembic', 'upgrade',
   'head']`. Bicep's `command` field maps to the OCI `command`
   (equivalent to docker's `--entrypoint` override) so the API
   `CMD` is discarded entirely — no stray uvicorn process gets
   spawned alongside alembic.
3. **`triggerType: Manual`.** Schedule / event triggers are
   deliberately not wired here. Migrations on a cron would race a
   concurrent deploy; event triggers don't model "before traffic
   shift" cleanly. WU6.3's deploy.yml will run
   `az containerapp job start --name <jobName> --resource-group <rg>`
   immediately before flipping `100` to the new revision and abort
   the shift on failure.
4. **`replicaTimeout: 600` (locked by the acceptance criterion).**
   Long enough for a many-step alembic chain on a cold Postgres
   Burstable instance; short enough to fail a stuck migration inside
   the deploy.yml budget rather than blocking forever.
5. **`replicaRetryLimit: 0`.** Alembic is idempotent in the success
   case but mid-migration retries on a partially-applied step are
   strictly worse than failing fast and letting the operator inspect.
   `deploy.yml` will abort the traffic shift on non-zero exit.
6. **User-assigned managed identity — reuse the WU6.1 UAMI.** The
   `horizons-github-oidc` UAMI already exists in `horizons-nonprod`
   and federates GitHub Actions to Azure. Mounting it on the migration
   Job (`identity.type: 'UserAssigned'`, `existing` keyword to
   reference it without owning its lifecycle) lets the same principal
   authenticate to Postgres once the AAD-user step lands. The job
   also exposes `AZURE_CLIENT_ID` as an env var so
   `DefaultAzureCredential` pins onto the right identity if a
   system-assigned one ever shows up alongside.
7. **AAD-authenticated Postgres connection — with a documented
   password fallback for the demo.** The long-term shape (§5, §10 of
   the locked-in plan) is passwordless: the UAMI authenticates to
   Postgres directly, and `env.py` fetches a token via
   `DefaultAzureCredential`/`ManagedIdentityCredential` to use as the
   connection-string password. **The UAMI must first be registered as
   a Postgres AAD principal** (one-off DB-side action — see
   "Follow-ups" below). Until that lands, the job accepts an optional
   `postgresAdminPassword` secure parameter wired as a job-level
   secret (`postgres-password`) and surfaced as
   `HORIZONS_DB_PASSWORD`. The Python connection-string assembly
   prefers the passwordless path when no password env var is set.

## Decisions inline that warrant a paper trail

- **`Microsoft.App/jobs@2024-10-02-preview` API version.** Same
  preview API version used by the worker module. Stays consistent
  across all Container Apps resources to avoid mixed-version
  oddities in the ARM JSON.
- **No `registries:` block.** Same as the API and worker modules —
  the GHCR images are public for the demo (post-WU6.2 manual
  visibility flip). When private, both the worker module and this
  module gain a registry-credential secret via post-deploy
  `az containerapp job secret set` + `az containerapp job registry
  set`. Documented in `infra/README.md` from WU6.0.
- **`postgresUser` defaults to the admin login.** The acceptance
  criterion says "managed-identity Postgres connection
  (passwordless)"; until the UAMI is registered as a Postgres AAD
  principal, the migration job logs in as the admin (which has
  schema-modification rights). This is the same conservative posture
  as the existing role model — schema changes belong to a
  privileged identity, not to `api_app`/`ingestion_worker`/`client`.
  When the AAD-user step lands, `postgresUser` is set to the UAMI
  name (the AAD principal name Postgres recognises).
- **`environmentName` allowed values match `main.bicep`.** Kept the
  `dev/stg/prd` allowedList so a typo at deploy time fails at
  `what-if` instead of producing a misnamed resource.

## Things deliberately deferred

- **DB-side AAD user provisioning for the UAMI.** One-off action
  against the Postgres server:

  ```sql
  -- as the AAD admin (the user who's the postgres-flex `aad_admin`):
  SELECT * FROM pgaadauth_create_principal(
      'horizons-github-oidc',
      false,        -- is_admin
      false         -- is_mfa
  );
  GRANT CONNECT ON DATABASE horizons TO "horizons-github-oidc";
  GRANT USAGE ON SCHEMA public TO "horizons-github-oidc";
  GRANT CREATE ON SCHEMA public TO "horizons-github-oidc";
  -- Plus whatever per-table grants the alembic tree needs to run
  -- `upgrade head`. Once the role model migration (0001) is the only
  -- thing wired, schema_owner-equivalent grants are the simplest
  -- minimum.
  ```

  Not in this Bicep because (a) Postgres principal creation is
  control-plane against the database, not against Azure RM; (b)
  it requires the AAD admin token to be present at runtime, which
  only the user has. **Follow-up: run this against
  `horizons-nonprod`'s pgsql once the server is provisioned, then
  set `postgresAdminPassword=''` in `main.parameters.example.json`
  and switch `postgresUser` to `'horizons-github-oidc'` in
  `main.bicep`.**
- **Invocation wiring.** `deploy.yml` (WU6.3) will run
  `az containerapp job start --name <jobName> --resource-group <rg>
  --wait` before the traffic shift, capture the exit code, and abort
  the deploy on failure. This module exposes `migrationJobName` as a
  main.bicep output specifically so deploy.yml can read it from the
  deployment outputs.
- **`replicaCompletionCount > 1` for parallel migration shards.**
  N/A for the demo — one Postgres, one alembic tree, one shot.
- **Schedule-trigger variant for routine maintenance.** Periodic
  `VACUUM ANALYZE` or stats refresh would be a separate Job with
  `triggerType: Schedule`; not part of WU6.4's scope.

## Verification gate

```bash
az bicep build --file infra/main.bicep
# → exit 0; zero warnings.
```

Python sweep (no Python touched but the gate is the gate; results
captured at end-of-session in
`journal/260605-wu66-drift-check-workflow.md`).

## External verification (user-only — flagged for post-merge)

The acceptance criterion is `az deployment group what-if` showing the
new Job as an additional Create against `horizons-nonprod`. That call
needs the user's Azure credentials and was therefore NOT attempted
in-session.

```bash
# Pre-requisites:
#   1. az login (interactive, user-only).
#   2. horizons-nonprod RG exists (already, from WU6.1).
#   3. A throwaway Postgres password is supplied for the run — the
#      example parameters file is hand-written; for a first what-if,
#      override with --parameters.

az deployment group what-if \
  --resource-group horizons-nonprod \
  --template-file infra/main.bicep \
  --parameters @infra/main.parameters.example.json \
  --parameters postgresAdminPassword='REPLACE-ME-EPHEMERAL'
```

Expected delta against the WU6.0 baseline: one additional
`Microsoft.App/jobs` resource named `horizons-dev-migrate` (or
`-stg`/`-prd` depending on the `environmentName` parameter) marked as
`Create`. Every other resource keeps its WU6.0 disposition.

If `what-if` reports a Modify on any pre-existing resource, that's a
regression introduced by this WU and a blocker for the WU6.3 wiring.

If it reports an error like
`ResourceNotFound: userAssignedIdentities/horizons-github-oidc`, the
UAMI provisioned by WU6.1 isn't in the target RG — either the RG name
parameter is wrong, or the UAMI was provisioned elsewhere; check
`az identity list -g horizons-nonprod`.

## Follow-ups (recap)

1. Register the `horizons-github-oidc` UAMI as a Postgres AAD
   principal on the `horizons-nonprod` Postgres server (SQL block
   above), then flip `postgresUser` to the UAMI name and drop the
   password fallback. **This is the work that closes the
   "AAD-authenticated Postgres connection (passwordless)" half of the
   WU6.4 acceptance criterion in production.**
2. WU6.3 wires the invocation — Bicep is silent until that lands.
3. Module reuse: the same shape will be the template for any future
   one-shot Job we add (data backfills, content reseeds), so keeping
   it small and parameterised pays off twice.

## Next pickup

WU6.3 (`deploy.yml`) is next on Track 6 once WU4.4 ships a callable
API. Until then, the migration Job sits provisioned-but-uninvoked,
which is the correct intermediate state.
