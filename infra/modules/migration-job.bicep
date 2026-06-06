// Container Apps Job — one-shot Alembic migration runner.
//
// WU6.4. A `Microsoft.App/jobs` resource that runs `alembic upgrade
// head` against the same Postgres Flexible Server the API talks to. The
// job is `triggerType: Manual` — `deploy.yml` (WU6.3, future) is what
// kicks it off before any new API revision receives traffic. Nothing in
// this module starts the job; provisioning it here only stands up the
// shape so the future pipeline has something to invoke with
// `az containerapp job start --name <jobName> --resource-group <rg>`.
//
// Image strategy: reuse the existing `ghcr.io/johnmathews/horizons-api`
// image (built by `.github/workflows/build-and-push.yml`, WU6.2) with a
// `command` override pointing at the Alembic CLI rather than uvicorn.
// The API image already bakes in horizons-core (which owns the
// migration tree under `packages/horizons-core/migrations/`) plus the
// alembic.ini at the workspace root — see WU6.2's Dockerfile, which
// `uv sync --package horizons-api` resolves to the same dep closure
// Alembic needs. A dedicated migration image is therefore redundant.
//
// Identity: the same user-assigned managed identity provisioned by
// WU6.1 (`horizons-github-oidc`) federates GitHub Actions to Azure
// *and* will act as the database principal here. Bicep references it
// with the `existing` keyword so this module never owns the UAMI's
// lifecycle.
//
// Postgres authentication: the long-term posture per the locked-in
// plan (§5, §10) is passwordless — the AAD principal authenticates to
// Postgres directly, no admin-password secret in the connection
// string. Application-side, this means `env.py` fetches a Postgres
// access token via `DefaultAzureCredential`/`ManagedIdentityCredential`
// and uses it as the password component of the connection string. The
// UAMI must be registered as a Postgres AAD user with `LOGIN` +
// migration-tree privileges in a one-off DB-side step
// (`SELECT * FROM pgaadauth_create_principal(...)` + `GRANT ALL ON
// SCHEMA public TO "horizons-github-oidc"`).
//
// **Password fallback (demo).** Until the AAD-user provisioning step
// lands, the job accepts an optional `postgresAdminPassword` secure
// parameter and exposes it as a secret-backed `HORIZONS_DB_PASSWORD`
// env var. The Python connection-string assembly prefers the
// passwordless code path when no password env var is set. The
// fallback is a known follow-up flagged in
// `journal/260605-wu64-migration-aca-job.md`.

@description('Azure region.')
param location string

@description('Workload prefix used in resource names.')
@minLength(3)
@maxLength(10)
param workloadPrefix string

@description('Environment short name.')
@allowed([
  'dev'
  'stg'
  'prd'
])
param environmentName string

@description('Container Apps environment resource ID — the job runs in the same managed env as the API and worker.')
param environmentId string

@description('OCI image reference. Reuses the API image (WU6.2) with a command override; do NOT build a separate migration image. Pinning :latest at the module default is intentional for demo deployments — `deploy.yml` will override per-deploy with the just-built :sha-<short> tag so the migrations execute against the same code that the API revision will run.')
param image string = 'ghcr.io/johnmathews/horizons-api:latest'

@description('Name of the user-assigned managed identity provisioned out-of-band by WU6.1. The migration job authenticates to Postgres as this identity (passwordless) once the AAD-user step has been applied; until then the password fallback applies.')
param userAssignedIdentityName string = 'horizons-github-oidc'

@description('Fully-qualified domain name of the Postgres Flexible Server (e.g. horizons-dev-pgsql.postgres.database.azure.com). Passed in from main.bicep, sourced from the postgres module output.')
param postgresFqdn string

@description('Database name to migrate. Single-DB-per-env is the demo posture.')
param postgresDatabase string = 'horizons'

@description('Postgres user the migration job logs in as. For passwordless this is the UAMI name (registered as a PG AAD principal); for password fallback this is the admin login from postgres-flex.bicep.')
param postgresUser string

@description('Password fallback for the demo. Leave empty to use passwordless (preferred). When non-empty the value is stored as a Container Apps Job secret and surfaced as the `HORIZONS_DB_PASSWORD` env var; the app code chooses the password path when this env var is set.')
@secure()
param postgresAdminPassword string = ''

@description('CPU cores per replica. Migration jobs are I/O bound; 0.5 is plenty for the demo dataset.')
param cpu string = '0.5'

@description('Memory per replica.')
param memory string = '1.0Gi'

@description('Replica timeout in seconds. Locked at 600 by the WU6.4 acceptance criterion — long enough for a many-step alembic chain, short enough to fail a stuck job inside the deploy.yml budget.')
param replicaTimeout int = 600

@description('Tags applied to the job.')
param tags object = {}

// The UAMI is provisioned out-of-band (WU6.1). Referencing it with
// `existing` means this module never tries to create / update / delete
// it — only mounts it on the job.
resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: userAssignedIdentityName
}

var jobName = '${workloadPrefix}-${environmentName}-migrate'
var hasPasswordFallback = !empty(postgresAdminPassword)

resource migrate 'Microsoft.App/jobs@2024-10-02-preview' = {
  name: jobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uami.id}': {}
    }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      // Manual: deploy.yml (WU6.3) starts the job via
      //   `az containerapp job start --name <jobName> --resource-group <rg>`
      // immediately before the API revision update. We deliberately do
      // NOT wire `scheduleTriggerConfig` or `eventTriggerConfig` —
      // running migrations on a timer would race with deploys.
      triggerType: 'Manual'
      replicaTimeout: replicaTimeout
      // No retries — Alembic is idempotent in the success case but
      // mid-migration retries on a partially-applied step are
      // strictly worse than failing fast and letting the operator
      // investigate. `deploy.yml` aborts the API revision update on failure.
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      // Secret materialises only when the password fallback is used.
      // Empty-array case is normal for the passwordless target state.
      secrets: hasPasswordFallback ? [
        {
          name: 'postgres-password'
          value: postgresAdminPassword
        }
      ] : []
      registries: []
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: image
          // Override uvicorn → alembic. The runtime image puts the
          // alembic console script at /opt/venv/bin/alembic (on PATH);
          // alembic.ini lives at /app/alembic.ini after the Dockerfile's
          // COPY (B2). `uv` itself is NOT in the runtime image — the
          // builder stage owns it.
          //
          // The migration env.py reads HORIZONS_DB_URL; the job's
          // env block only carries the individual HORIZONS_DB_* parts.
          // Assemble the URL at runtime via a sh wrapper. The `\${…}`
          // escape keeps Bicep from interpolating at compile time so
          // the literal `${VAR}` lands in the JSON; shell expansion
          // happens in the container where the env vars are populated.
          command: [
            'sh'
            '-c'
            'export HORIZONS_DB_URL="postgresql+psycopg://\${HORIZONS_DB_USER}:\${HORIZONS_DB_PASSWORD}@\${HORIZONS_DB_HOST}:5432/\${HORIZONS_DB_NAME}"; exec alembic upgrade head'
          ]
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: concat([
            {
              name: 'HORIZONS_ENV'
              value: environmentName
            }
            {
              name: 'HORIZONS_DB_HOST'
              value: postgresFqdn
            }
            {
              name: 'HORIZONS_DB_NAME'
              value: postgresDatabase
            }
            {
              name: 'HORIZONS_DB_USER'
              value: postgresUser
            }
            // The UAMI's client ID is what `DefaultAzureCredential`
            // pins onto when more than one identity is mounted on the
            // workload. Surfacing it via env var avoids any ambiguity
            // when the runtime later mounts a system-assigned
            // identity in addition.
            {
              name: 'AZURE_CLIENT_ID'
              value: uami.properties.clientId
            }
          ], hasPasswordFallback ? [
            {
              name: 'HORIZONS_DB_PASSWORD'
              secretRef: 'postgres-password'
            }
          ] : [])
        }
      ]
    }
  }
}

output jobId string = migrate.id
output jobName string = migrate.name
output principalId string = uami.properties.principalId
