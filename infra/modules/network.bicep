// Virtual network + subnets for the Horizons demo deployment.
//
// Two delegated subnets:
//   * pgsql  — Azure DB for PostgreSQL Flexible Server (delegation:
//              Microsoft.DBforPostgreSQL/flexibleServers).
//   * aca    — Container Apps environment (delegation:
//              Microsoft.App/environments).
//
// Address space defaults are deliberately generic and non-overlapping
// with anything obvious. Override via parameters in a real deployment.

@description('Azure region the VNet is deployed to.')
param location string

@description('Workload prefix used in resource names (e.g. "horizons").')
param workloadPrefix string

@description('Environment short name (e.g. "dev", "stg", "prd").')
param environmentName string

@description('CIDR for the VNet itself.')
param vnetAddressPrefix string = '10.20.0.0/16'

@description('CIDR for the PostgreSQL Flexible Server delegated subnet.')
param pgsqlSubnetPrefix string = '10.20.0.0/24'

@description('CIDR for the Container Apps environment subnet.')
param acaSubnetPrefix string = '10.20.4.0/23'

@description('Tags applied to every resource in this module.')
param tags object = {}

var vnetName = '${workloadPrefix}-${environmentName}-vnet'
var pgsqlSubnetName = 'snet-pgsql'
var acaSubnetName = 'snet-aca'

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    subnets: [
      {
        name: pgsqlSubnetName
        properties: {
          addressPrefixes: [pgsqlSubnetPrefix]
          delegations: [
            {
              name: 'pgsql-delegation'
              properties: {
                serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
              }
            }
          ]
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: acaSubnetName
        properties: {
          addressPrefixes: [acaSubnetPrefix]
          delegations: [
            {
              name: 'aca-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

resource pgsqlDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.postgres.database.azure.com'
  location: 'global'
  tags: tags
}

resource pgsqlDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: pgsqlDnsZone
  name: '${vnetName}-link'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
output pgsqlSubnetId string = '${vnet.id}/subnets/${pgsqlSubnetName}'
output acaSubnetId string = '${vnet.id}/subnets/${acaSubnetName}'
output pgsqlDnsZoneId string = pgsqlDnsZone.id
