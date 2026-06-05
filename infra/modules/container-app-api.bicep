// Container App — public REST API (FastAPI / uvicorn).
//
// Per docs/4. services.md, this is the single HTTP surface every client
// talks to. External ingress on :8000 → port 8000 inside the container.
// Multiple revisions per locked-in plan item 10 — `activeRevisionsMode:
// Multiple` enables revision-based blue/green rollback via traffic shift.

@description('Azure region.')
param location string

@description('Workload prefix used in resource names.')
param workloadPrefix string

@description('Environment short name.')
param environmentName string

@description('Container Apps environment resource ID.')
param environmentId string

@description('OCI image reference, e.g. ghcr.io/johnmathews/horizons-api:sha-abc1234.')
param image string

@description('Container target port (uvicorn default).')
param targetPort int = 8000

@description('CPU cores per replica.')
param cpu string = '0.5'

@description('Memory per replica, e.g. "1.0Gi".')
param memory string = '1.0Gi'

@description('Minimum replica count.')
param minReplicas int = 1

@description('Maximum replica count.')
param maxReplicas int = 3

@description('Application Insights connection string for OTEL.')
@secure()
param appInsightsConnectionString string

@description('Tags applied to the container app.')
param tags object = {}

// Registry auth — the demo pulls from ghcr.io with public images, so the
// Container App needs no registry config. For a private registry, the
// secret value and `registries:` block are added by a post-deploy
// `az containerapp secret set …` + `az containerapp registry set …` step
// (documented in infra/README.md and again in deploy.yml at WU6.3).

var appName = '${workloadPrefix}-${environmentName}-api'

resource api 'Microsoft.App/containerApps@2024-10-02-preview' = {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Multiple'
      ingress: {
        external: true
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
        // `traffic` is intentionally NOT declared here. WU6.3's deploy.yml
        // manages traffic imperatively via `az containerapp ingress traffic
        // set` so it can stand up a new revision at 0 % weight, smoke-test
        // it, and only then shift to 100 % (with the previous revision held
        // at 0 % as the rollback target). Declaring a traffic block would
        // either (a) re-pin `latestRevision: true` on every Bicep deploy —
        // bypassing the smoke gate — or (b) drift to whichever named
        // revision the template happens to know about. ARM incremental
        // mode preserves the live traffic state when the property is
        // absent; on the very first deploy ACA defaults the traffic config
        // to `latestRevision: true, weight: 100` (the platform default),
        // which is the correct bootstrap behaviour. Subsequent deploys
        // leave traffic untouched.
      }
      secrets: []
      registries: []
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsightsConnectionString
            }
            {
              name: 'HORIZONS_ENV'
              value: environmentName
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: targetPort
              }
              initialDelaySeconds: 10
              periodSeconds: 10
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/healthz'
                port: targetPort
              }
              initialDelaySeconds: 5
              periodSeconds: 5
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

output appId string = api.id
output appName string = api.name
output fqdn string = api.properties.configuration.ingress.fqdn
output principalId string = api.identity.principalId
