// Azure Monitor alert spine — one Action Group + three alert rules.
//
// WU7.3. Sits on top of the workspace-based App Insights from
// application-insights.bicep (locked-in plan item 12). The OTEL distro
// (WU7.0) drives AppRequests rows for the API and AppTraces rows for the
// worker; this module turns those signals into operational alerts.
//
//   (1) API 5xx ratio    — scheduledQueryRules over AppRequests.
//                          ResultCode >= 500 divided by total over 5 min.
//                          Threshold > 1%. Severity 2.
//   (2) API p95 latency  — scheduledQueryRules over AppRequests.
//                          percentile(DurationMs, 95) over 5 min.
//                          Threshold > 3000 ms. Severity 2.
//                          (Metric alerts cannot aggregate percentile on
//                          requests/duration; a log alert is the canonical
//                          shape for a P95 SLO.)
//   (3) Ingestion failures — scheduledQueryRules over AppTraces. Counts
//                            the worker's WARNING-level "schedule entry
//                            parked" log emitted at loop.py:212 when the
//                            consecutive-failure threshold is crossed and
//                            an ingestion_incident row is written.
//                            Threshold > 3 in 1 h. Severity 2.
//
// All three rules ship DISABLED (`enabled: false` default). Rationale:
// arming alerts against an unpopulated workspace produces spurious "no
// data" notifications on every evaluation cycle until the API and worker
// are actually deployed (WU6.3). Flip them on per-environment once data
// is flowing — the Manual enable section of the WU7.3 journal entry
// records the exact CLI invocation.
//
// Notification target: a single email receiver, default mthwsjc@gmail.com.
// Swap to Slack post-demo by passing `alertEmail: ''` and a Slack webhook
// URL through a new `webhookReceivers` parameter — one-line module diff
// (see Notification target section of the journal entry).

@description('Azure region for the action group. Action groups are Global; the alert rules inherit the location from the resource group via the resourceGroup() reference but are placed at "global" for the metric/log alert pattern.')
param location string

@description('Workload prefix used in every resource name.')
@minLength(3)
@maxLength(10)
param workloadPrefix string

@description('Environment short name. One of dev/stg/prd.')
@minLength(2)
@maxLength(3)
param environmentName string

@description('Application Insights resource ID — scope for the API alert queries.')
param appInsightsId string

@description('Log Analytics workspace resource ID — scope for the worker alert query.')
param logAnalyticsWorkspaceId string

@description('Container App role name reported by the API container (matches AppRoleName in AppRequests).')
param apiAppRoleName string = '${workloadPrefix}-${environmentName}-api'

@description('Container App role name reported by the worker (matches AppRoleName in AppTraces).')
param workerAppRoleName string = '${workloadPrefix}-${environmentName}-worker'

@description('Email address that receives alert notifications. Parameterised so a different recipient (or Slack-webhook receiver swap) needs no module edit.')
param alertEmail string = 'mthwsjc@gmail.com'

@description('Whether the three alert rules are armed at deploy time. Default false — flip per-environment in the parameters file or via `az monitor` once the API + worker are deployed and emitting data (see WU7.3 journal entry).')
param alertsEnabled bool = false

@description('Skip server-side KQL validation when creating the scheduledQueryRules. Required when the deployment runs against a fresh Log Analytics workspace where AppRequests / AppTraces schema has not yet been registered (chicken-and-egg: ACA needs to be wired to the workspace and emit data before the schema is queryable). Default true — rules ship disabled (alertsEnabled=false) anyway, so validation is meaningless until they are armed.')
param skipQueryValidation bool = true

@description('Tags applied to every resource.')
param tags object = {}

// Action Group shortName has a 12-char hard cap. workloadPrefix is bounded
// 3..10 and environmentName 2..3, so '${workloadPrefix}${environmentName}'
// could be up to 13 chars. Use a fixed-prefix derivation that the Bicep
// static analyzer can bound to ≤ 7 chars regardless of workloadPrefix:
// `hzn-` (4) + environmentName (max 3) = 7.
var groupShortName = 'hzn-${environmentName}'

var actionGroupName = '${workloadPrefix}-${environmentName}-ag-email'
var alertApi5xxName = '${workloadPrefix}-${environmentName}-alert-api-5xx'
var alertApiP95Name = '${workloadPrefix}-${environmentName}-alert-api-p95'
var alertIngestionFailuresName = '${workloadPrefix}-${environmentName}-alert-ingestion-failures'

// KQL queries — single-line because Bicep multi-line strings ('''…''')
// do not perform `${}` interpolation. Pipe operators are whitespace-
// separated which is valid KQL syntax on a single physical line.
// `union isfuzzy=true` makes the table reference tolerate "table not yet
// registered in the workspace schema". Workspace-based App Insights only
// registers AppRequests / AppTraces once the API and worker have actually
// emitted a record; on a fresh-workspace deploy that hasn't happened yet,
// so the RP's pre-flight schema-resolution check rejects the rule before
// `skipQueryValidation` is consulted.
//
// Fuzzy union requires at least ONE operand that resolves at validation
// time, so we pair the real table with an empty `datatable(...)` sentinel
// declaring the columns each query references. The sentinel contributes
// zero rows; once the real table exists, its rows flow through and the
// alert behaves identically to the single-table form.
var api5xxQuery = 'union isfuzzy=true (datatable(AppRoleName:string, ResultCode:string)[]), AppRequests | where AppRoleName == "${apiAppRoleName}" | summarize total = count(), failed = countif(toint(ResultCode) >= 500) | extend errorRatio = iff(total == 0, 0.0, todouble(failed) / todouble(total)) | project errorRatio'

var apiP95Query = 'union isfuzzy=true (datatable(AppRoleName:string, DurationMs:real)[]), AppRequests | where AppRoleName == "${apiAppRoleName}" | summarize p95Ms = percentile(DurationMs, 95) | project p95Ms'

var ingestionFailuresQuery = 'union isfuzzy=true (datatable(AppRoleName:string, SeverityLevel:int, Message:string)[]), AppTraces | where AppRoleName == "${workerAppRoleName}" | where SeverityLevel >= 2 | where Message contains "schedule entry parked"'

// -------------------------------------------------------------------
// Action Group — single email receiver.
// -------------------------------------------------------------------
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: actionGroupName
  // Action groups live at the Global location regardless of the RG's region.
  location: 'Global'
  tags: tags
  properties: {
    groupShortName: groupShortName
    enabled: true
    emailReceivers: [
      {
        name: 'primary'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
    smsReceivers: []
    webhookReceivers: []
    azureAppPushReceivers: []
    voiceReceivers: []
    armRoleReceivers: []
    azureFunctionReceivers: []
    logicAppReceivers: []
    eventHubReceivers: []
    itsmReceivers: []
    automationRunbookReceivers: []
  }
}

// -------------------------------------------------------------------
// (1) API 5xx rate > 1% over 5 min.
//
// Scope = the workspace-based App Insights component. The query runs
// against AppRequests (workspace-based AI surfaces requests there).
// projection: errorRatio as a single-row metric measure column.
// -------------------------------------------------------------------
resource alertApi5xx 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: alertApi5xxName
  location: location
  tags: tags
  properties: {
    displayName: alertApi5xxName
    description: 'API 5xx error ratio > 1% over 5 min (AppRequests / requests/failed over requests/count). Disabled by default; arm per-environment after WU6.3 deploys the API.'
    severity: 2
    enabled: alertsEnabled
    skipQueryValidation: skipQueryValidation
    scopes: [
      appInsightsId
    ]
    targetResourceTypes: [
      'microsoft.insights/components'
    ]
    // PT5M (not PT1M): 1-minute evaluation is only allowed for queries
    // against the platform's "known table" fast-path. The fuzzy-union
    // sentinel wrapper (see KQL section above) disqualifies these queries
    // from that fast-path, so PT1M is rejected with QueryNotContainKnownTable.
    // PT5M matches windowSize, so we were oversampling at PT1M anyway.
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    autoMitigate: true
    criteria: {
      allOf: [
        {
          query: api5xxQuery
          timeAggregation: 'Average'
          metricMeasureColumn: 'errorRatio'
          operator: 'GreaterThan'
          threshold: json('0.01')
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

// -------------------------------------------------------------------
// (2) API p95 latency > 3 s over 5 min.
//
// Scope = the App Insights component for the same reason as (1).
// AppRequests.DurationMs is in milliseconds; percentile(.., 95) is the
// 95th-percentile aggregation expressed in KQL because the platform
// metric `requests/duration` does not expose percentile aggregations to
// metric alerts.
// -------------------------------------------------------------------
resource alertApiP95 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: alertApiP95Name
  location: location
  tags: tags
  properties: {
    displayName: alertApiP95Name
    description: 'API p95 latency > 3000 ms over 5 min (AppRequests percentile(DurationMs, 95)). Disabled by default; arm per-environment after WU6.3 deploys the API.'
    severity: 2
    enabled: alertsEnabled
    skipQueryValidation: skipQueryValidation
    scopes: [
      appInsightsId
    ]
    targetResourceTypes: [
      'microsoft.insights/components'
    ]
    // PT5M (not PT1M): 1-minute evaluation is only allowed for queries
    // against the platform's "known table" fast-path. The fuzzy-union
    // sentinel wrapper (see KQL section above) disqualifies these queries
    // from that fast-path, so PT1M is rejected with QueryNotContainKnownTable.
    // PT5M matches windowSize, so we were oversampling at PT1M anyway.
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    autoMitigate: true
    criteria: {
      allOf: [
        {
          query: apiP95Query
          timeAggregation: 'Average'
          metricMeasureColumn: 'p95Ms'
          operator: 'GreaterThan'
          threshold: 3000
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

// -------------------------------------------------------------------
// (3) Ingestion failures > 3 in 1 h.
//
// Scope = the Log Analytics workspace. The worker uses stdlib logging
// (loop.py); WU7.1's structlog setup routes the stdlib root logger
// through structlog's ProcessorFormatter so the worker's WARNING line
// at loop.py:212 ("schedule entry parked: document_id=%s
// failure_count=%d error=%s") lands as an AppTraces row with
// SeverityLevel >= Warning (>=2 in the AppTraces schema).
//
// One log line emits per parked schedule entry — i.e. once per
// ingestion_incident write with error_class='parked'. Counting those
// over a rolling hour with a threshold of 3 catches sustained ingestion
// degradation without firing on a single bad poll.
//
// `contains` is used over `has` because the log line is a stdlib
// %-formatted string with the phrase embedded between
// document_id / failure_count / error tokens; `has` requires whole-word
// token matches and would miss the phrase boundary.
// -------------------------------------------------------------------
resource alertIngestionFailures 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: alertIngestionFailuresName
  location: location
  tags: tags
  properties: {
    displayName: alertIngestionFailuresName
    description: 'Ingestion failures > 3 in 1 h (AppTraces "schedule entry parked" warnings emitted by the worker on threshold-cross). Disabled by default; arm per-environment after WU6.3 deploys the worker. Requires the worker to be running so AppTraces is populated.'
    severity: 2
    enabled: alertsEnabled
    skipQueryValidation: skipQueryValidation
    scopes: [
      logAnalyticsWorkspaceId
    ]
    targetResourceTypes: [
      'microsoft.operationalinsights/workspaces'
    ]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT1H'
    autoMitigate: true
    criteria: {
      allOf: [
        {
          query: ingestionFailuresQuery
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 3
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

output actionGroupId string = actionGroup.id
output actionGroupName string = actionGroup.name
output alertApi5xxName string = alertApi5xx.name
output alertApiP95Name string = alertApiP95.name
output alertIngestionFailuresName string = alertIngestionFailures.name
