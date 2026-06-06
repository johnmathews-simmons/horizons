// Container App — ingestion worker (long-running asyncio).
//
// ADR-0001 picked the long-running container shape over an ACA Job. The
// consequences spelled out in that ADR drive every choice in this module:
//
//   * minReplicas = maxReplicas = 1 — one always-on replica.
//   * Scale rules disabled (no HTTP traffic, no events to scale on).
//   * Internal `/healthz` over a small aiohttp surface for the liveness
//     probe.
//   * No external ingress — the worker is reachable only by the ACA
//     control plane (probe + revision lifecycle) and its database.
//
// Per docs/RFC-4 services.md, the worker MUST be a separate container app
// from the API — never co-located, so an ingestion burst cannot starve
// the API of CPU or connections.

@description('Azure region.')
param location string

@description('Workload prefix used in resource names.')
param workloadPrefix string

@description('Environment short name.')
param environmentName string

@description('Container Apps environment resource ID.')
param environmentId string

@description('OCI image reference, e.g. ghcr.io/johnmathews/horizons-worker:sha-abc1234.')
param image string

@description('Internal port the /healthz aiohttp surface listens on.')
param healthPort int = 8080

@description('CPU cores per replica.')
param cpu string = '0.5'

@description('Memory per replica, e.g. "1.0Gi".')
param memory string = '1.0Gi'

@description('Application Insights connection string for OTEL.')
@secure()
param appInsightsConnectionString string

@description('Postgres server FQDN.')
param postgresFqdn string

@description('Postgres user.')
param postgresUser string = 'horizons_admin'

@description('Database name.')
param postgresDatabase string = 'horizons'

@description('Postgres admin password. Combined into HORIZONS_DB_URL as a Container App secret.')
@secure()
param postgresAdminPassword string

@description('Blob storage account URL (https://<acct>.blob.core.windows.net). The worker writes original document blobs into a container under this account.')
param blobAccountUrl string

@description('Tags applied to the container app.')
param tags object = {}

// Registry auth omitted for the same reason as the API module — public
// ghcr.io images need no credentials. See container-app-api.bicep and
// infra/README.md.

var appName = '${workloadPrefix}-${environmentName}-worker'

resource worker 'Microsoft.App/containerApps@2024-10-02-preview' = {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Multiple'
      // Internal ingress is required for ACA to route the liveness probe.
      // `external: false` keeps the worker un-routable from outside the
      // environment. This is the conventional way to expose /healthz on
      // a long-running worker — see ADR-0001 "Consequences" #3.
      ingress: {
        external: false
        targetPort: healthPort
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      // Short-circuit secret architecture (decision Q1, triage doc).
      // Post-demo: move behind Key Vault references.
      secrets: [
        {
          name: 'db-url'
          // horizons-core/db/session.py expects the asyncpg driver.
          // ACA secrets channel keeps the @secure password sealed.
          #disable-next-line use-secure-value-for-secure-inputs
          value: 'postgresql+asyncpg://${postgresUser}:${postgresAdminPassword}@${postgresFqdn}:5432/${postgresDatabase}'
        }
      ]
      registries: []
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: image
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsightsConnectionString
            }
            {
              name: 'HORIZONS_ENV'
              value: environmentName
            }
            {
              // Bicep param historically named `HORIZONS_WORKER_HEALTH_PORT`
              // but the worker code reads `HORIZONS_INGESTION_HEALTHZ_PORT`
              // (config.py). Align so a non-default port would actually take
              // effect; today's 8080 default keeps both names a no-op.
              name: 'HORIZONS_INGESTION_HEALTHZ_PORT'
              value: string(healthPort)
            }
            {
              name: 'HORIZONS_DB_URL'
              secretRef: 'db-url'
            }
            {
              name: 'HORIZONS_INGESTION_BLOB_ACCOUNT_URL'
              value: blobAccountUrl
            }
            // Lawstronaut credentials. The worker reads these at startup
            // and KeyError-exits if absent, even in static-dataset mode
            // (Q3, triage doc) where nothing will be claimed. Dummy
            // values let the worker boot and idle on its loop. Swap to
            // real ACA secrets when ingestion goes live post-demo.
            {
              name: 'LAWSTRONAUT_EMAIL'
              value: 'demo-noop@horizons.invalid'
            }
            {
              name: 'LAWSTRONAUT_PASSWORD'
              value: 'demo-noop'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: healthPort
              }
              initialDelaySeconds: 15
              periodSeconds: 15
              failureThreshold: 3
            }
          ]
        }
      ]
      // ADR-0001: long-running, exactly one replica.
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

output appId string = worker.id
output appName string = worker.name
output principalId string = worker.identity.principalId
