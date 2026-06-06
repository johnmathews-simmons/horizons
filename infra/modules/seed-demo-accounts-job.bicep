// Container Apps Job — one-shot demo-accounts seed runner.
//
// Mirror of `migration-job.bicep`. Runs
// `python /app/scripts/create_demo_accounts.py` against the deployed
// Postgres immediately after `migration-job` succeeds in deploy.yml.
// Per the triage doc's decision Q5: reproducible across resource
// groups, runs every deploy (the script is idempotent — re-runs rotate
// the stored hash to match the env-var passwords, never downgrade).
//
// Reuses the API image (same command-override pattern as the migration
// job). The script lands at `/app/scripts/create_demo_accounts.py` per
// the API Dockerfile's COPY step.

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

@description('OCI image reference. Reuses the API image with a command override; the script is copied into /app/scripts/ at build time.')
param image string = 'ghcr.io/johnmathews/horizons-api:latest'

@description('UAMI name; mounted on the job so the same DefaultAzureCredential path works as the API and worker.')
param userAssignedIdentityName string = 'horizons-github-oidc'

@description('Postgres FQDN.')
param postgresFqdn string

@description('Database name.')
param postgresDatabase string = 'horizons'

@description('Postgres user the seed script logs in as.')
param postgresUser string

@description('Postgres admin password. Secret-backed; never logged.')
@secure()
param postgresAdminPassword string

@description('Password for demo-uk@example.test. Secret-backed.')
@secure()
param demoUkPassword string

@description('Password for demo-eu@example.test. Secret-backed.')
@secure()
param demoEuPassword string

@description('Password for admin-demo@example.test. Secret-backed.')
@secure()
param demoAdminPassword string

@description('CPU cores per replica. The seed is a few short SQL writes; 0.25 is plenty.')
param cpu string = '0.25'

@description('Memory per replica.')
param memory string = '0.5Gi'

@description('Replica timeout in seconds.')
param replicaTimeout int = 300

@description('Tags applied to the job.')
param tags object = {}

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: userAssignedIdentityName
}

var jobName = '${workloadPrefix}-${environmentName}-seed-demo-accounts'

resource seed 'Microsoft.App/jobs@2024-10-02-preview' = {
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
      // The script is idempotent on success but a partial failure
      // mid-INSERT could leave a half-seeded row. Fail fast; the
      // operator investigates rather than retrying blindly.
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
          name: 'seed'
          image: image
          // Same sh-wrapper trick as migration-job.bicep: build
          // HORIZONS_DB_URL from the parts at container start. The
          // script accepts both +psycopg and +asyncpg URL forms per
          // its docstring; use +psycopg to match the migration job's
          // sync driver path.
          //
          // \${…} escapes Bicep's compile-time interpolation so the
          // literal ${…} lands in the JSON and shell expansion
          // happens at runtime.
          command: [
            'sh'
            '-c'
            'export HORIZONS_DB_URL="postgresql+psycopg://\${HORIZONS_DB_USER}:\${HORIZONS_DB_PASSWORD}@\${HORIZONS_DB_HOST}:5432/\${HORIZONS_DB_NAME}"; exec python /app/scripts/create_demo_accounts.py'
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

output jobId string = seed.id
output jobName string = seed.name
