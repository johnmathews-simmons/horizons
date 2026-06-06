// Storage account for the Horizons demo.
//
// Two roles:
//   * 'originals' container — content-addressed legal markdown
//     (originals/<sha256>.md) per docs/4. services.md.
//   * '$web' static-website container — the Vue SPA bundle, fronted by
//     Azure Front Door per locked-in plan item 11.

@description('Azure region for the storage account.')
param location string

@description('Workload prefix used in resource names.')
@minLength(3)
@maxLength(10)
param workloadPrefix string

@description('Environment short name.')
@minLength(2)
@maxLength(4)
param environmentName string

@description('Storage SKU.')
@allowed([
  'Standard_LRS'
  'Standard_ZRS'
  'Standard_GRS'
  'Standard_RAGRS'
])
param skuName string = 'Standard_LRS'

@description('Object ID of the UAMI that uploads the SPA bundle to $web. Granted `Storage Blob Data Contributor` so `az storage blob upload-batch --auth-mode login` from deploy.yml can write. Without it the upload step 403s.')
param spaUploaderPrincipalId string

@description('Tags applied to the storage account.')
param tags object = {}

// Storage account names: 3–24 chars, lowercase + digits only. Compose
// from `@minLength`'d parameters so the static analyzer can prove length
// bounds: prefix(3-10) + env(2-4) + literal "st"(2) + uniq(6) = 13–22.
var storageName = toLower('${workloadPrefix}${environmentName}st${take(uniqueString(resourceGroup().id), 6)}')

resource storage 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: storageName
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' = {
  parent: storage
  name: 'default'
  properties: {
    isVersioningEnabled: false
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource originalsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: blobService
  name: 'originals'
  properties: {
    publicAccess: 'None'
  }
}

// Static-website ($web) container. Enabling the static website feature
// (which creates $web automatically) is a control-plane action not exposed
// in the StorageV2 schema, so we declare $web explicitly. The
// post-deployment step `az storage blob service-properties update
// --static-website` flips the feature on; that command is documented in
// infra/README.md.
resource webContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: blobService
  name: '$web'
  properties: {
    publicAccess: 'None'
  }
}

// Storage Blob Data Contributor — RBAC data-plane role required for
// `az storage blob upload-batch --auth-mode login`. Granting at the
// storage-account scope so any container under it (originals + $web)
// is writable by the UAMI. ID per
// https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles
var blobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
resource spaUploaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, spaUploaderPrincipalId, blobDataContributorRoleId)
  properties: {
    principalId: spaUploaderPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobDataContributorRoleId)
  }
}

output storageAccountId string = storage.id
output storageAccountName string = storage.name
output blobEndpoint string = storage.properties.primaryEndpoints.blob
output webEndpoint string = storage.properties.primaryEndpoints.web
output originalsContainerName string = originalsContainer.name
