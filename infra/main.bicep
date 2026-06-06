// Horizons — root Bicep template.
//
// Composes the nine modules under infra/modules/ into a deployable shape:
//
//   network ──┬──> postgres-flex
//             │
//             └──> container-app-env ──┬──> container-app-api
//                                      └──> container-app-worker
//   keyvault, application-insights, storage stand alone.
//   front-door fronts storage $web for the SPA.
//
// Scope: resourceGroup. Run with:
//
//   az deployment group create \
//     --resource-group <rg> \
//     --template-file infra/main.bicep \
//     --parameters @infra/main.parameters.example.json
//
// `az deployment group what-if` is the gate referenced in the work-unit
// acceptance criterion. The example parameters file uses placeholder
// values (zeroed tenant/subscription IDs, generic admin login); real
// deployments pass --parameters with a per-environment file.

targetScope = 'resourceGroup'

@description('Azure region for all regional resources.')
param location string = resourceGroup().location

@description('Workload prefix used in every resource name.')
@minLength(3)
@maxLength(10)
param workloadPrefix string = 'horizons'

@description('Environment short name. One of dev/stg/prd.')
@allowed([
  'dev'
  'stg'
  'prd'
])
param environmentName string = 'dev'

@description('Tenant ID for Key Vault and PostgreSQL AAD admin.')
param tenantId string = subscription().tenantId

@description('PostgreSQL admin login.')
param postgresAdminLogin string = 'horizons_admin'

@description('PostgreSQL admin password. In real deployments this is sourced from Key Vault via a secure parameter file — never committed.')
@secure()
param postgresAdminPassword string

@description('OCI image reference for the API container.')
param apiImage string = 'ghcr.io/johnmathews/horizons-api:latest'

@description('OCI image reference for the ingestion-worker container.')
param workerImage string = 'ghcr.io/johnmathews/horizons-worker:latest'

@description('Tags applied to every resource. Workload + environment + cost-centre, minimally.')
param tags object = {
  workload: 'horizons'
  environment: 'dev'
  managedBy: 'bicep'
}

@description('Email address that receives Azure Monitor alert notifications (WU7.3). Single email receiver on the action group; swap to a Slack webhook by extending alerts.bicep with a webhookReceivers param.')
param alertEmail string = 'mthwsjc@gmail.com'

@description('Whether the WU7.3 alert rules are armed at deploy time. Default false — provisioned-disabled so they do not fire "no data" before WU6.3 deploys the API + worker. Flip per-environment after the first deploy emits data.')
param alertsEnabled bool = false

// ---------------------------------------------------------------------
// 1. Network — VNet + two delegated subnets + private DNS zone.
// ---------------------------------------------------------------------
module network 'modules/network.bicep' = {
  name: 'network'
  params: {
    location: location
    workloadPrefix: workloadPrefix
    environmentName: environmentName
    tags: tags
  }
}

// ---------------------------------------------------------------------
// 2. Observability — Log Analytics + workspace-based App Insights.
//    Must exist before the Container Apps env can wire up OTEL forwarding.
// ---------------------------------------------------------------------
module observability 'modules/application-insights.bicep' = {
  name: 'observability'
  params: {
    location: location
    workloadPrefix: workloadPrefix
    environmentName: environmentName
    tags: tags
  }
}

// ---------------------------------------------------------------------
// 3. Secrets — Key Vault.
// ---------------------------------------------------------------------
module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    location: location
    workloadPrefix: workloadPrefix
    environmentName: environmentName
    tenantId: tenantId
    tags: tags
  }
}

// ---------------------------------------------------------------------
// 4. Storage — originals container + $web for the SPA.
// ---------------------------------------------------------------------
module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    location: location
    workloadPrefix: workloadPrefix
    environmentName: environmentName
    tags: tags
  }
}

// ---------------------------------------------------------------------
// 5. PostgreSQL Flexible Server (VNet-integrated, PG 17).
// ---------------------------------------------------------------------
module postgres 'modules/postgres-flex.bicep' = {
  name: 'postgres'
  params: {
    location: location
    workloadPrefix: workloadPrefix
    environmentName: environmentName
    delegatedSubnetId: network.outputs.pgsqlSubnetId
    privateDnsZoneId: network.outputs.pgsqlDnsZoneId
    administratorLogin: postgresAdminLogin
    administratorPassword: postgresAdminPassword
    tags: tags
  }
}

// ---------------------------------------------------------------------
// 6. Container Apps environment — managed OTEL agent wired to App Insights.
// ---------------------------------------------------------------------
module containerEnv 'modules/container-app-env.bicep' = {
  name: 'container-app-env'
  params: {
    location: location
    workloadPrefix: workloadPrefix
    environmentName: environmentName
    infrastructureSubnetId: network.outputs.acaSubnetId
    tags: tags
  }
}

// ---------------------------------------------------------------------
// 7a. Public REST API container app.
// ---------------------------------------------------------------------
module containerApi 'modules/container-app-api.bicep' = {
  name: 'container-app-api'
  params: {
    location: location
    workloadPrefix: workloadPrefix
    environmentName: environmentName
    environmentId: containerEnv.outputs.environmentId
    image: apiImage
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
    tags: tags
  }
}

// ---------------------------------------------------------------------
// 7b. Ingestion-worker container app (ADR-0001: long-running, 1 replica).
//     Must NEVER share a Container App with the API — docs/4. services.md
//     §"API responsiveness is non-negotiable".
// ---------------------------------------------------------------------
module containerWorker 'modules/container-app-worker.bicep' = {
  name: 'container-app-worker'
  params: {
    location: location
    workloadPrefix: workloadPrefix
    environmentName: environmentName
    environmentId: containerEnv.outputs.environmentId
    image: workerImage
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
    tags: tags
  }
}

// ---------------------------------------------------------------------
// 7c. Migration ACA Job — one-shot `alembic upgrade head` runner.
//     WU6.4. Same managed environment as the API/worker. Triggered
//     manually by `deploy.yml` (WU6.3) immediately before a traffic
//     shift; does NOT run on schedule. Reuses the API image with a
//     command override — see infra/modules/migration-job.bicep.
// ---------------------------------------------------------------------
module migrationJob 'modules/migration-job.bicep' = {
  name: 'migration-job'
  params: {
    location: location
    workloadPrefix: workloadPrefix
    environmentName: environmentName
    environmentId: containerEnv.outputs.environmentId
    image: apiImage
    postgresFqdn: postgres.outputs.serverFqdn
    postgresUser: postgresAdminLogin
    // Password is the demo fallback — see migration-job.bicep header.
    // Once the UAMI is registered as a Postgres AAD principal, this
    // becomes the empty string and the connection runs passwordless.
    postgresAdminPassword: postgresAdminPassword
    tags: tags
  }
}

// ---------------------------------------------------------------------
// 8. Front Door Standard — fronts the SPA on storage $web.
//    The originHostName is the storage web endpoint with the scheme
//    stripped; substring(8) drops `https://`.
// ---------------------------------------------------------------------
module frontDoor 'modules/front-door.bicep' = {
  name: 'front-door'
  params: {
    workloadPrefix: workloadPrefix
    environmentName: environmentName
    originHostName: replace(replace(storage.outputs.webEndpoint, 'https://', ''), '/', '')
    tags: tags
  }
}

// ---------------------------------------------------------------------
// 9. Alerts — Action Group + 5xx / p95 / ingestion-failure alert rules.
//    WU7.3. All three alerts ship DISABLED (alertsEnabled=false) so
//    they don't fire "no data" notifications before WU6.3 deploys the
//    API + worker. Flip per-environment with
//    `--parameters alertsEnabled=true` or via `az monitor scheduled-query
//    update` (see infra/README.md and the WU7.3 journal entry).
// ---------------------------------------------------------------------
module alerts 'modules/alerts.bicep' = {
  name: 'alerts'
  params: {
    location: location
    workloadPrefix: workloadPrefix
    environmentName: environmentName
    appInsightsId: observability.outputs.appInsightsId
    logAnalyticsWorkspaceId: observability.outputs.workspaceId
    apiAppRoleName: containerApi.outputs.appName
    workerAppRoleName: containerWorker.outputs.appName
    alertEmail: alertEmail
    alertsEnabled: alertsEnabled
    tags: tags
  }
}

// ---------------------------------------------------------------------
// Outputs surfaced for the deploy.yml pipeline (WU6.3, out of scope here).
// ---------------------------------------------------------------------
output keyVaultName string = keyVault.outputs.keyVaultName
output storageAccountName string = storage.outputs.storageAccountName
output postgresFqdn string = postgres.outputs.serverFqdn
output containerEnvName string = containerEnv.outputs.environmentName
output apiContainerAppName string = containerApi.outputs.appName
output apiFqdn string = containerApi.outputs.fqdn
output workerContainerAppName string = containerWorker.outputs.appName
output migrationJobName string = migrationJob.outputs.jobName
output frontDoorHostName string = frontDoor.outputs.endpointHostName
output appInsightsConnectionString string = observability.outputs.appInsightsConnectionString
output alertActionGroupName string = alerts.outputs.actionGroupName
output alertApi5xxName string = alerts.outputs.alertApi5xxName
output alertApiP95Name string = alerts.outputs.alertApiP95Name
output alertIngestionFailuresName string = alerts.outputs.alertIngestionFailuresName
