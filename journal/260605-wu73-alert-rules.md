# WU7.3 — Azure Monitor alert rules + Action Group

*Last revised: 2026-06-05.*
*Path: journal/260605-wu73-alert-rules.md.*

*Session 2026-06-05. Branch `wu7.3-alert-rules` → ff-merged to `main`.*

The third Track 7 unit (after WU7.0 / WU7.1 shipped the OTel + structlog
modules). Adds one Bicep module — `infra/modules/alerts.bicep` — wiring
an Action Group plus three alert rules into the existing
`infra/main.bicep` composition. All three rules ship **disabled**;
they're enabled per-environment after WU6.3 deploys the API + worker so
they don't fire "no data" notifications against an unpopulated
workspace.

## What shipped

```
infra/
├── modules/
│   └── alerts.bicep                    NEW — Action Group + 3 alerts.
├── main.bicep                          Two new params (alertEmail,
│                                       alertsEnabled), one new module
│                                       block, four new outputs.
└── main.parameters.example.json        Surface the two new params with
                                        their defaults.
```

The three alerts:

| # | Name (resource) | Signal | Threshold | Window / Eval | Severity |
|---|---|---|---|---|---|
| 1 | `<prefix>-<env>-alert-api-5xx` | `AppRequests` rows from the API container; ratio `failed/total` where `ResultCode >= 500` | > 1% (0.01) | 5 min / 1 min | 2 |
| 2 | `<prefix>-<env>-alert-api-p95` | `AppRequests.DurationMs` from the API container; `percentile(.., 95)` | > 3000 ms | 5 min / 1 min | 2 |
| 3 | `<prefix>-<env>-alert-ingestion-failures` | `AppTraces` from the worker; rows where `SeverityLevel >= 2` and `Message contains "schedule entry parked"` | count > 3 | 1 h / 15 min | 2 |

All three are `scheduledQueryRules@2023-03-15-preview` resources rather
than `metricAlerts`. Reasoning per alert:

- **(1) 5xx ratio.** Platform metric `requests/failed` divided by
  `requests/count` cannot be expressed as a single-metric criterion
  inside `metricAlerts`; arithmetic across metrics is a log-alert
  shape. The KQL `iff(total == 0, 0.0, todouble(failed)/todouble(total))`
  guard prevents division-by-zero when the window is genuinely idle.
- **(2) p95 latency.** The App Insights platform metric
  `requests/duration` only exposes `Average / Maximum / Minimum`
  aggregations to metric alerts. Percentile aggregations are a log
  query against `AppRequests.DurationMs`.
- **(3) Ingestion failures.** Counting the worker's WARNING-level
  `"schedule entry parked"` log line (emitted at `loop.py:212`,
  exactly once per `ingestion_incident` row written with
  `error_class='parked'`) is intrinsically log-shaped.

The Action Group is `Microsoft.Insights/actionGroups@2023-01-01` —
stable API, sufficient for a single email receiver. `useCommonAlertSchema:
true` so Common Alert Schema is the payload contract; any future
webhook / function receiver inherits the same shape.

## Architectural decisions reflected

- **Locked-in plan §12 (observability posture).** Workspace-based App
  Insights is the metric/log substrate; the OTel distro auto-emits
  `AppRequests` for the FastAPI auto-instrumentation (WU7.0) and
  routes stdlib logging through structlog's processor (WU7.1) so
  worker WARNING lines land in `AppTraces`. Alerts target those
  workspace tables directly.
- **All alerts DISABLED by default.** Provisioning armed alerts
  against a workspace with no data fires the "Insufficient data"
  notification path on every evaluation cycle, which a) trains the
  user to ignore the inbox, and b) doesn't add operational signal
  because the deployments aren't live yet. `alertsEnabled: false` is
  the safe default; flipping happens after WU6.3 deploys the API +
  worker (see "Manual enable" section).
- **Three deployable services (locked-in plan §4).** Alert (1) and
  (2) target the API container app; alert (3) targets the worker.
  The `apiAppRoleName` / `workerAppRoleName` params default to the
  same `<workloadPrefix>-<environmentName>-{api,worker}` naming the
  container-app modules use, but accept overrides so the alert rules
  can be re-pointed without retemplating.

## Decisions inline that warrant a paper trail

- **Bicep `'''…'''` multi-line strings don't interpolate `${...}`.**
  First pass embedded KQL queries as triple-single-quote multi-line
  strings with `${apiAppRoleName}` inside. Bicep treats those as
  literal text — the lint surfaced as `no-unused-params` warnings on
  both role-name params. Fix: single-line KQL strings (pipe operators
  are whitespace-separated, so the query is still valid KQL on one
  physical line) declared as module-top `var`s and referenced by the
  resources. Worth flagging because the WU7.4 admin audit-log work
  may want similar query strings.
- **Action Group `groupShortName` is bounded at 12 chars.** A first
  pass derived it as `'${workloadPrefix}${environmentName}'` truncated
  with a length-check ternary. Bicep's static analyzer cannot prove
  the ternary's upper bound and emits `BCP335`. Replaced with a fixed
  `'hzn-${environmentName}'` (at most 7 chars given `environmentName`
  `@maxLength(3)`) — the workloadPrefix doesn't need to surface in the
  shortName because the alert payload already carries it via the
  full alert name.
- **`scheduledQueryRules` API version `2023-03-15-preview`.**
  Stable enough for production use and the schema that supports
  `metricMeasureColumn` on a projected scalar. The earlier stable
  `2021-08-01` works too but is one schema generation behind.
- **Scope of (1) and (2) is the App Insights component; scope of (3)
  is the Log Analytics workspace.** Both surfaces work for both
  queries (workspace-based AI stores its data in the LAW), but the
  alert payload's `targetResource` field is more meaningful when it
  matches the conceptual subject of the query — app-focused alerts
  point at App Insights; log-table-focused alerts at the workspace.

## Verification gate (local sweep)

```bash
uv run ruff check .             # All checks passed
uv run pyright                  # 0 errors, 22 warnings (pre-existing
                                # testcontainers stub-not-found)
uv run pytest -m "not integration"
                                # 319 passed, 4 skipped (fixture too
                                # small), 161 deselected
uv run pre-commit run \
  --files \
    infra/modules/alerts.bicep \
    infra/main.bicep \
    infra/main.parameters.example.json
                                # all hooks Passed
az bicep build --file infra/main.bicep
                                # exit 0; zero warnings
```

A `uv run pre-commit run --all-files` invocation surfaced a
ruff-format reflow in `packages/horizons-core/tests/observability/test_otel.py`
that is pre-existing on `main` (last touched in commit `ffba27f`,
WU7.0 + WU7.1). That file is out of scope for WU7.3 and was reverted
in this session — the format drift will get picked up by the next
session that touches the file or by a dedicated formatting sweep.
Not a regression introduced here; just a missed ruff-format pass
during the WU7.0 merge.

## External verification (user-only — flagged for post-merge)

The acceptance criterion for WU7.3 includes Azure-side validation.
The user runs these after merge with their own Azure credentials.

### 1. what-if against a non-prod RG

Confirms the four new resources (action group + three alert rules)
register as Create-only deltas with no surprise updates to existing
resources:

```bash
az deployment group what-if \
  --resource-group horizons-nonprod \
  --template-file infra/main.bicep \
  --parameters @infra/main.parameters.example.json \
  --parameters postgresAdminPassword='REPLACE-ME-EPHEMERAL'
```

Expected new resources in the output:

- `<workloadPrefix>-<env>-ag-email` (Microsoft.Insights/actionGroups)
- `<workloadPrefix>-<env>-alert-api-5xx` (Microsoft.Insights/scheduledQueryRules)
- `<workloadPrefix>-<env>-alert-api-p95` (Microsoft.Insights/scheduledQueryRules)
- `<workloadPrefix>-<env>-alert-ingestion-failures` (Microsoft.Insights/scheduledQueryRules)

### 2. Deploy

```bash
az deployment group create \
  --resource-group horizons-nonprod \
  --template-file infra/main.bicep \
  --parameters @infra/main.parameters.example.json \
  --parameters postgresAdminPassword='REPLACE-ME-EPHEMERAL'
```

### 3. Drift-check workflow

The nightly drift-check workflow (WU6.6, 03:00 UTC) WILL show these
new resources on its next run if the user hasn't deployed yet —
expected, not a regression. Once deployed, drift-check returns to
green.

## Manual enable

The three alerts ship with `enabled: false`. After WU6.3 has deployed
the API + worker and `AppRequests` / `AppTraces` rows are flowing,
flip each alert to enabled. Two paths:

### Path A — flip the Bicep parameter (preferred; declarative)

Pass `alertsEnabled=true` on the next `az deployment group create`:

```bash
az deployment group create \
  --resource-group horizons-nonprod \
  --template-file infra/main.bicep \
  --parameters @infra/main.parameters.example.json \
  --parameters postgresAdminPassword='REPLACE-ME-EPHEMERAL' \
  --parameters alertsEnabled=true
```

This keeps the Bicep template as the source of truth. The drift-check
workflow stays green because the deployed state matches the
template's `alertsEnabled` value.

### Path B — flip individual rules via Azure CLI (ad-hoc; for testing)

If the user wants to arm one rule at a time during a load-test, the
per-rule update commands are:

```bash
# Replace <RG>, <WORKLOAD>, <ENV> with the deployed values
# (e.g. horizons-nonprod, horizons, dev).

# (1) API 5xx
az monitor scheduled-query update \
  --resource-group <RG> \
  --name <WORKLOAD>-<ENV>-alert-api-5xx \
  --disabled false

# (2) API p95 latency
az monitor scheduled-query update \
  --resource-group <RG> \
  --name <WORKLOAD>-<ENV>-alert-api-p95 \
  --disabled false

# (3) Ingestion failures
az monitor scheduled-query update \
  --resource-group <RG> \
  --name <WORKLOAD>-<ENV>-alert-ingestion-failures \
  --disabled false
```

Note: `az monitor scheduled-query` uses `--disabled <bool>` (negated)
rather than `--enabled`. To flip back off, pass `--disabled true`.

The next `az deployment group create` without `alertsEnabled=true`
will re-disable any rule flipped via Path B — Bicep is the source
of truth. Use Path B only for short-lived ad-hoc tests.

**When to do this:** after WU6.3 deploys the API + worker and at
least one revision has served real traffic / processed at least one
poll, so the workspace tables have data. Comfortably before the
2026-06-08 demo. The 5xx and p95 alerts arm safely with API traffic;
the ingestion-failures alert needs the worker running and at least
one poll cycle for `AppTraces` to populate (otherwise it sits in
"Insufficient data" until a tick fires).

## Notification target

Single email receiver, default `mthwsjc@gmail.com`. Parameterised
via the `alertEmail` param so a different recipient swaps in without
editing the module:

```bash
az deployment group create \
  ... \
  --parameters alertEmail=oncall@example.com
```

### Swap to Slack post-demo

For a Slack channel, extend `alerts.bicep` with a `slackWebhookUrl`
param and an additional `webhookReceivers` entry on the action
group:

```bicep
// Add this param near alertEmail:
@description('Slack-compatible webhook URL. Leave empty to use email-only routing.')
@secure()
param slackWebhookUrl string = ''

// And add to the Action Group's properties:
webhookReceivers: empty(slackWebhookUrl) ? [] : [
  {
    name: 'slack'
    serviceUri: slackWebhookUrl
    useCommonAlertSchema: true
  }
]
```

The Common Alert Schema payload format means Slack's incoming-webhook
JSON shape works for some channels; for production Slack routing the
canonical path is **Azure Monitor → Action Group webhook →
Logic App / Function → Slack chat.postMessage**. That extra hop is
out of scope for the demo (email is fine for a hand-watched run);
the Bicep shape above leaves the receiver slot ready when the user
wants to wire it.

## What's required for alert (3) to fire

The ingestion-failures alert depends on the worker being deployed,
running, and emitting `AppTraces` rows captured by App Insights.
Specifically:

1. **WU6.3 must deploy the worker container app.** Until then,
   `AppTraces` has no rows from the worker's role name and the
   alert sits in "Insufficient data".
2. **The worker must call `setup_structlog()` at startup** so its
   stdlib `logging.warning("schedule entry parked: ...")` line at
   `loop.py:212` routes through the structlog processor and lands
   as a JSON line on stdout (which the OTel managed agent forwards
   to App Insights as `AppTraces`). WU7.0 / WU7.1 added the
   modules; the worker's `__main__.py` wire-up to call them lands
   in the follow-up commit flagged at the bottom of the WU7.0 /
   WU7.1 journals.
3. **The ACA managed OTEL agent must be bound to the workspace.**
   This is the `az containerapp env update --logs-destination …`
   one-off documented in `infra/README.md` (locked-in plan §12,
   ADR-equivalent decision). Without it, stdout logs go to ACA's
   own log stream but not to App Insights, so `AppTraces` stays
   empty.

If alert (3) is armed before any of the above is true, it sits in
"Insufficient data" and notifies on the platform's "InsightAlerts.no
data" path. That's exactly what `alertsEnabled: false` defers — once
all three preconditions are met, flip the alert on.

## Decisions deliberately deferred

- **Action Group SMS / push receivers.** Email is sufficient for the
  hand-watched demo. Adding SMS requires the user's phone in cleartext
  config (no Key Vault binding for ActionGroup secrets); push requires
  the Azure mobile app's per-user enrolment. Both are post-demo
  if/when the operator on-call shape stabilises.
- **Dynamic-threshold (machine-learning) alerts.** The 5xx and p95
  alerts use static thresholds. Azure Monitor's dynamic thresholds
  ("Trigger when the metric deviates from the learned baseline")
  are higher-signal once a few weeks of traffic exist. Pre-demo
  there is no baseline to learn from; revisit after the demo period
  if alert volume becomes noisy.
- **Per-environment alert tuning.** The same thresholds apply across
  `dev` / `stg` / `prd` because they're all on the same Bicep
  parameter file. If `dev` needs noisier thresholds (more permissive)
  and `prd` needs tighter (less permissive), the params for
  `alert5xxRatioThreshold`, `alertP95LatencyMs`, etc. could be
  introduced as a follow-up — out of scope for this unit.
- **Auto-mitigation tuning.** All three alerts use the default
  `autoMitigate: true`. That means the alert auto-closes after one
  evaluation window with the criteria no longer met. For 5xx and
  p95 (windowSize PT5M) that's fast; for ingestion failures
  (windowSize PT1H) the closure lags by up to an hour. If demo
  measurements show flapping at that cadence, the fix is a separate
  alert per environment with shorter windows.

## Decisions consistent with prior work units

The journal entries for WU6.0 (Bicep skeletons), WU6.2 (Dockerfiles
+ GHCR), WU7.0 (OTel module), and WU7.1 (structlog module) all
defer external verification (Azure what-if, GHCR push, App Insights
binding) to the user. WU7.3 follows the same posture: the local
sweep + `az bicep build` are the in-session gates; the deployment
verification is the user's post-merge step.

## Next pickup

WU7.3 closes a Track 7 thread but does not exhaust it. Open:

| WU | Title | Notes |
| --- | --- | --- |
| 4.4 → wire-up | App.py wire-up | Apply WU7.0 / WU7.1 snippet once WU4.4 merges. |
| 7.2 | Admin `/health/*` endpoints | Depends on WU4.5 + WU7.0. Queries Log Analytics via UAMI with a 60s cache. |
| 7.4 | Admin audit log surface | Depends on WU1.9 + WU4.5. Reads append-only audit table; same scheduledQueryRules shape may be useful for "audit anomalies" follow-up alerts. |

This session does not pick up any of those — the locked plan calls
for stopping after WU7.3.

The alerts module itself has a clear follow-up shape if more alert
classes accumulate (e.g. blob-sweep orphan-count, schedule-claim
latency): one `scheduledQueryRules` resource per signal, all bound
to the same Action Group. The module's current shape (one resource
block per alert, no array-based iteration) scales cleanly to a few
more without restructure; if the list grows past about a dozen, an
array-driven `for` loop with a per-entry param object would be the
refactor.
