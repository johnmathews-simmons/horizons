// Key Vault — RBAC-enabled, soft-delete + purge-protection on.
// Hosts the Postgres admin password (until we cut over to passwordless),
// the JWT signing keypair, and any secrets the ACA revisions reference.

@description('Azure region for the Key Vault.')
param location string

@description('Workload prefix used in resource names.')
param workloadPrefix string

@description('Environment short name.')
param environmentName string

@description('Tenant ID the Key Vault is scoped to.')
param tenantId string

@description('Tags applied to the Key Vault.')
param tags object = {}

@description('SKU for the Key Vault. Standard suffices for the demo.')
@allowed([
  'standard'
  'premium'
])
param skuName string = 'standard'

var kvName = take('${workloadPrefix}-${environmentName}-kv-${uniqueString(resourceGroup().id)}', 24)

resource kv 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: kvName
  location: location
  tags: tags
  properties: {
    tenantId: tenantId
    sku: {
      family: 'A'
      name: skuName
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

output keyVaultId string = kv.id
output keyVaultName string = kv.name
output keyVaultUri string = kv.properties.vaultUri
