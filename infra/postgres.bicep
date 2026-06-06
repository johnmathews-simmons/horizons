// Horizons — Postgres-only Bicep template.
//
// Split out from main.bicep so the routine app-deploy pipeline does not
// re-assert the Flexible Server resource on every push. Re-asserting the
// server (in particular the @secure() administratorLoginPassword) caused
// repeated `ServerIsBusy` failures on the routine deploy: the RP held a
// control-plane lock for several minutes after each write, and the next
// deploy collided with that lock immediately. Stateful resources should
// be deployed once and referenced — `main.bicep` now reads the FQDN via
// an `existing` lookup.
//
// Deploy with:
//
//   az deployment group create \
//     --resource-group horizons-nonprod \
//     --template-file infra/postgres.bicep \
//     --parameters @infra/postgres.parameters.example.json \
//     --parameters administratorPassword="$POSTGRES_ADMIN_PASSWORD"
//
// Or via the `Deploy Postgres` GitHub Actions workflow (manual only).
//
// Assumes the VNet + delegated subnet + private DNS zone already exist —
// `main.bicep` creates them via modules/network.bicep, so deploy main
// first against a fresh resource group.

targetScope = 'resourceGroup'

@description('Azure region.')
param location string = resourceGroup().location

@description('Workload prefix used in resource names.')
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

@description('Administrator login.')
param administratorLogin string = 'horizons_admin'

@description('Administrator password. Sourced from CI via --parameters; never committed.')
@secure()
param administratorPassword string

@description('Postgres major version.')
@allowed([
  '15'
  '16'
  '17'
  '18'
])
param postgresVersion string = '18'

@description('Compute tier.')
@allowed([
  'Burstable'
  'GeneralPurpose'
  'MemoryOptimized'
])
param skuTier string = 'Burstable'

@description('Compute SKU name (must match the tier).')
param skuName string = 'Standard_B1ms'

@description('Storage size in GB.')
param storageSizeGB int = 32

@description('Tags applied to the server.')
param tags object = {
  workload: 'horizons'
  environment: 'dev'
  managedBy: 'bicep'
}

// VNet + DNS zone are owned by main.bicep; this template references them
// rather than re-asserting them.
var vnetName = '${workloadPrefix}-${environmentName}-vnet'
var pgsqlSubnetName = 'snet-pgsql'

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: vnetName
}

resource pgsqlDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' existing = {
  name: 'privatelink.postgres.database.azure.com'
}

module postgres 'modules/postgres-flex.bicep' = {
  name: 'postgres'
  params: {
    location: location
    workloadPrefix: workloadPrefix
    environmentName: environmentName
    delegatedSubnetId: '${vnet.id}/subnets/${pgsqlSubnetName}'
    privateDnsZoneId: pgsqlDnsZone.id
    administratorLogin: administratorLogin
    administratorPassword: administratorPassword
    postgresVersion: postgresVersion
    skuTier: skuTier
    skuName: skuName
    storageSizeGB: storageSizeGB
    tags: tags
  }
}

output serverFqdn string = postgres.outputs.serverFqdn
output serverName string = postgres.outputs.serverName
