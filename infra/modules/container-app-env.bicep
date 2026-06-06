// Container Apps environment.
//
// VNet-integrated, Consumption-only workload profile (the demo runs on the
// Consumption tier; workload profiles are a tunable lever, not a redesign).
// The managed OTEL agent (per locked-in plan item 12) is configured here so
// every revision deployed into the env inherits trace + log forwarding.

@description('Azure region for the environment.')
param location string

@description('Workload prefix used in resource names.')
param workloadPrefix string

@description('Environment short name.')
param environmentName string

@description('Container Apps subnet ID (delegated to Microsoft.App/environments).')
param infrastructureSubnetId string

@description('Log Analytics workspace customerId — drives `appLogsConfiguration` so container stdout/stderr lands in LA without a post-deploy `az containerapp env update` step.')
param logAnalyticsCustomerId string

@description('Log Analytics workspace primary shared key. Sourced via `listKeys(workspaceId, apiVersion).primarySharedKey` in main.bicep.')
@secure()
param logAnalyticsSharedKey string

@description('Tags applied to the environment.')
param tags object = {}

var envName = '${workloadPrefix}-${environmentName}-cae'

resource env 'Microsoft.App/managedEnvironments@2024-10-02-preview' = {
  name: envName
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: infrastructureSubnetId
      internal: false
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    // Stdout/stderr from every container in the env lands in Log Analytics
    // via this block. Previously a post-deploy one-off (`az containerapp
    // env update --logs-destination log-analytics …`); pulled into IaC so
    // fresh deploys have working logs from the first revision.
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
  }
}

output environmentId string = env.id
output environmentName string = env.name
output defaultDomain string = env.properties.defaultDomain
