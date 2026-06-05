// Azure Front Door Standard — fronts the SPA static site on storage $web.
//
// Per locked-in plan item 11: Front Door Standard, NOT Azure CDN (managed
// certs on Azure CDN expired April 2026). The origin is the storage
// account's web endpoint; Front Door handles TLS + caching + the apex /
// subdomain bind.

@description('Workload prefix used in resource names.')
param workloadPrefix string

@description('Environment short name.')
param environmentName string

@description('Hostname (without scheme) of the storage account web endpoint, e.g. horizonsdevst....z6.web.core.windows.net.')
param originHostName string

@description('Tags applied to Front Door resources.')
param tags object = {}

var profileName = '${workloadPrefix}-${environmentName}-fd'
var endpointName = '${workloadPrefix}-${environmentName}'
var originGroupName = 'spa-origin-group'
var originName = 'spa-storage-origin'
var routeName = 'spa-route'

resource profile 'Microsoft.Cdn/profiles@2024-09-01' = {
  name: profileName
  location: 'Global'
  tags: tags
  sku: {
    name: 'Standard_AzureFrontDoor'
  }
}

resource endpoint 'Microsoft.Cdn/profiles/afdEndpoints@2024-09-01' = {
  parent: profile
  name: endpointName
  location: 'Global'
  tags: tags
  properties: {
    enabledState: 'Enabled'
  }
}

resource originGroup 'Microsoft.Cdn/profiles/originGroups@2024-09-01' = {
  parent: profile
  name: originGroupName
  properties: {
    loadBalancingSettings: {
      sampleSize: 4
      successfulSamplesRequired: 3
      additionalLatencyInMilliseconds: 50
    }
    healthProbeSettings: {
      probePath: '/'
      probeRequestType: 'HEAD'
      probeProtocol: 'Https'
      probeIntervalInSeconds: 100
    }
    sessionAffinityState: 'Disabled'
  }
}

resource origin 'Microsoft.Cdn/profiles/originGroups/origins@2024-09-01' = {
  parent: originGroup
  name: originName
  properties: {
    hostName: originHostName
    httpPort: 80
    httpsPort: 443
    originHostHeader: originHostName
    priority: 1
    weight: 1000
    enabledState: 'Enabled'
    enforceCertificateNameCheck: true
  }
}

resource route 'Microsoft.Cdn/profiles/afdEndpoints/routes@2024-09-01' = {
  parent: endpoint
  name: routeName
  properties: {
    originGroup: {
      id: originGroup.id
    }
    supportedProtocols: ['Http', 'Https']
    patternsToMatch: ['/*']
    forwardingProtocol: 'HttpsOnly'
    linkToDefaultDomain: 'Enabled'
    httpsRedirect: 'Enabled'
  }
  dependsOn: [
    origin
  ]
}

output profileId string = profile.id
output endpointHostName string = endpoint.properties.hostName
