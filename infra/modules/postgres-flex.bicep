// Azure Database for PostgreSQL Flexible Server.
//
// PG 17 per the locked-in plan decisions (item 5). VNet-integrated via
// the pgsql subnet from network.bicep. Demo sizing — Burstable B1ms,
// 32 GB storage. Real prod cuts over to General Purpose + HA before any
// paying customer touches the box.

@description('Azure region for the server.')
param location string

@description('Workload prefix used in resource names.')
param workloadPrefix string

@description('Environment short name.')
param environmentName string

@description('Delegated subnet ID for VNet integration (Microsoft.DBforPostgreSQL/flexibleServers).')
param delegatedSubnetId string

@description('Private DNS zone ID for privatelink.postgres.database.azure.com.')
param privateDnsZoneId string

@description('Administrator login name. Override per-environment.')
param administratorLogin string = 'horizons_admin'

@description('Administrator password. In real deployments this comes from Key Vault or an OIDC-minted federated credential — never a hard-coded literal.')
@secure()
param administratorPassword string

@description('Postgres major version. PG 18 per the locked-in plan.')
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
param tags object = {}

var serverName = '${workloadPrefix}-${environmentName}-pgsql'

resource pgsql 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: skuTier
  }
  properties: {
    version: postgresVersion
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    storage: {
      storageSizeGB: storageSizeGB
      autoGrow: 'Enabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    network: {
      delegatedSubnetResourceId: delegatedSubnetId
      privateDnsZoneArmResourceId: privateDnsZoneId
      publicNetworkAccess: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Enabled'
      tenantId: tenant().tenantId
    }
  }
}

output serverId string = pgsql.id
output serverName string = pgsql.name
output serverFqdn string = pgsql.properties.fullyQualifiedDomainName
