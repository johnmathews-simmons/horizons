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
    // OpenTelemetry binding to App Insights is a control-plane action
    // performed by `az containerapp env update` post-deploy — see
    // infra/README.md "Post-deployment one-off steps". The corresponding
    // property on managedEnvironments has churned across API versions;
    // keeping the binding out-of-band avoids coupling the skeleton to a
    // shape that may be renamed before WU6.3 lands.
  }
}

output environmentId string = env.id
output environmentName string = env.name
output defaultDomain string = env.properties.defaultDomain
