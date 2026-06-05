// Application Insights (workspace-based) + Log Analytics workspace.
//
// Per locked-in plan item 12, observability is the azure-monitor-opentelemetry
// distro feeding App Insights via the ACA managed OTEL agent. App Insights
// must be workspace-based; classic mode is deprecated.

@description('Azure region.')
param location string

@description('Workload prefix used in resource names.')
param workloadPrefix string

@description('Environment short name.')
param environmentName string

@description('Log Analytics workspace retention (days).')
param retentionInDays int = 30

@description('Daily ingestion cap in GB (cost guard for the demo).')
param dailyQuotaGb int = 1

@description('Tags applied to both resources.')
param tags object = {}

var workspaceName = '${workloadPrefix}-${environmentName}-law'
var appInsightsName = '${workloadPrefix}-${environmentName}-appi'

resource workspace 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    workspaceCapping: {
      dailyQuotaGb: dailyQuotaGb
    }
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

output workspaceId string = workspace.id
output workspaceName string = workspace.name
output workspaceCustomerId string = workspace.properties.customerId
output appInsightsId string = appInsights.id
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey
