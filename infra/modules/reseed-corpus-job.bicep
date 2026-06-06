// Container Apps Job — operator-driven corpus wipe + re-seed.
//
// Mirror of `seed-demo-accounts-job.bicep`. Reuses the **worker** image
// (not the API image) because that's where the curated set, the
// fixture inventory, the synthetic-v2 markdown, and the seed scripts
// are baked in (see packages/horizons-ingestion/Dockerfile).
//
// Manual trigger only — never fires from deploy.yml. The operator runs
// `scripts/reseed_aca.sh` from a laptop, which calls
// `az containerapp job start` against this job. The script's local
// safety dance (typed-back confirmation, env-var check) gates the
// dispatch; the in-container `reseed_corpus.py` then performs its own
// pre-flight (user-count guard) before the destructive wipe.
//
// The job runs with `--yes` because explicit dispatch IS the confirmation
// — by the time the job starts, the operator has already typed the
// container app name back. A dry-run from the laptop is handled by NOT
// triggering the job; the local script prints the plan instead.

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

@description('Container Apps environment resource ID.')
param environmentId string

@description('OCI image reference. Reuses the worker image — the API image lacks the curated set, fixtures, synthetic-v2 markdown, and seed_curated_set.py / reseed_corpus.py that this job needs.')
param image string = 'ghcr.io/johnmathews/horizons-worker:latest'

@description('UAMI name; mounted on the job so the same DefaultAzureCredential path works as the API and worker.')
param userAssignedIdentityName string = 'horizons-github-oidc'

@description('Postgres FQDN.')
param postgresFqdn string

@description('Database name.')
param postgresDatabase string = 'horizons'

@description('Postgres user the reseed script logs in as.')
param postgresUser string

@description('Postgres admin password. Secret-backed; never logged.')
@secure()
param postgresAdminPassword string

@description('Password for demo-uk@demo.example.com. Secret-backed.')
@secure()
param demoUkPassword string

@description('Password for demo-eu@demo.example.com. Secret-backed.')
@secure()
param demoEuPassword string

@description('Password for admin-demo@demo.example.com. Secret-backed.')
@secure()
param demoAdminPassword string

@description('CPU cores per replica. The reseed parses markdown + runs the alignment pipeline + writes ~few-hundred rows; 0.5 covers the alignment burst.')
param cpu string = '0.5'

@description('Memory per replica.')
param memory string = '1Gi'

@description('Replica timeout in seconds. Wipe + curated_set.yaml seed + synthetic-v2 staging + demo-accounts reset; budget 10 minutes.')
param replicaTimeout int = 600

@description('Tags applied to the job.')
param tags object = {}

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: userAssignedIdentityName
}

var jobName = '${workloadPrefix}-${environmentName}-reseed-corpus'

resource reseed 'Microsoft.App/jobs@2024-10-02-preview' = {
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
      triggerType: 'Manual'
      replicaTimeout: replicaTimeout
      // Don't retry — a half-completed wipe + reseed should be
      // investigated by the operator, not blindly retried. Same
      // posture as seed-demo-accounts-job.bicep.
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      secrets: [
        {
          name: 'pg-password'
          value: postgresAdminPassword
        }
        {
          name: 'demo-uk-password'
          value: demoUkPassword
        }
        {
          name: 'demo-eu-password'
          value: demoEuPassword
        }
        {
          name: 'demo-admin-password'
          value: demoAdminPassword
        }
      ]
      registries: []
    }
    template: {
      containers: [
        {
          name: 'reseed'
          image: image
          // Same sh -c trick as migration-job + seed-demo-accounts-job:
          // build HORIZONS_DB_URL from the parts at container start.
          // reseed_corpus.py accepts both +psycopg and +asyncpg URL
          // forms per its docstring; use +psycopg to match the other
          // jobs' sync driver path.
          //
          // \${…} escapes Bicep's compile-time interpolation so the
          // literal ${…} lands in the JSON and shell expansion happens
          // at runtime.
          command: [
            'sh'
            '-c'
            'export HORIZONS_DB_URL="postgresql+psycopg://\${HORIZONS_DB_USER}:\${HORIZONS_DB_PASSWORD}@\${HORIZONS_DB_HOST}:5432/\${HORIZONS_DB_NAME}"; exec python /app/scripts/reseed_corpus.py --yes'
          ]
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: [
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
            {
              name: 'HORIZONS_DB_PASSWORD'
              secretRef: 'pg-password'
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: uami.properties.clientId
            }
            {
              name: 'HORIZONS_DEMO_UK_PASSWORD'
              secretRef: 'demo-uk-password'
            }
            {
              name: 'HORIZONS_DEMO_EU_PASSWORD'
              secretRef: 'demo-eu-password'
            }
            {
              name: 'HORIZONS_DEMO_ADMIN_PASSWORD'
              secretRef: 'demo-admin-password'
            }
          ]
        }
      ]
    }
  }
}

output jobId string = reseed.id
output jobName string = reseed.name
