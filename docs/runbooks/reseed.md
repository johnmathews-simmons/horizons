# Reseed runbook

*Audience: operator wiping + re-seeding the staging corpus from a
laptop. Built for the WU8.1 demo, kept around because the
`documents` table's append-only trigger makes "edit YAML and re-seed"
a no-op against an already-populated DB.*

Companion runbooks:

- [demo.md](./demo.md) — the public-showcase walk-through this reseed
  serves.
- [demo-accounts.md](./demo-accounts.md) — the three demo accounts the
  reseed re-provisions on its last step.
- [seeding.md](./seeding.md) — the curated-set YAML schema +
  synthetic-v2 staging path that the reseed re-runs.
- [migrations.md](./migrations.md) — the related migration ACA Job; the
  reseed Job uses the same dispatch pattern.

## What it does

`scripts/reseed_aca.sh` (laptop) dispatches the
`horizons-dev-reseed-corpus` Container Apps Job
(`infra/modules/reseed-corpus-job.bicep`, wired up in
`infra/main.bicep` section 7e). The Job reuses the worker image — which
bakes in `data/curated_set.yaml`, the fixture inventory, the
synthetic-v2 markdown, and the three seed scripts — and runs
`python /app/scripts/reseed_corpus.py --yes`. That orchestrator does,
in one transaction-per-stage:

1. **Pre-flight** — checks the four required env vars, refuses if the
   `users` row count exceeds `--max-existing-users` (default 50; cheap
   guard against accidentally pointing at a production-scale DB).
2. **Wipe** — `DELETE` from `change_events`, `clauses`,
   `document_versions`, `document_poll_schedule`, `documents` in
   FK-safe order, in a single transaction.
3. **Re-seed corpus** — `python /app/scripts/seed_curated_set.py
   --stage-synthetic-v2`, which inserts the curated documents +
   schedules and stages every synthetic-v2 pair (parses both v1 and
   v2 markdown, runs the alignment pipeline, inserts `clauses` and
   `change_events`).
4. **Reset demo accounts** — `python /app/scripts/create_demo_accounts.py
   --reset`, which deletes the three `@demo.example.com` users +
   their subscriptions/watchlists/scopes and recreates them with the
   env-var passwords.
5. **Post-flight** — prints `before` and `after` row counts; refuses
   to claim success if any expected corpus table is empty or the user
   count is < 3.

## Why a Job and not `az containerapp exec`

`az containerapp exec` (`containerapp` CLI extension `1.3.0b4` as of
2026-06-06) fails with `ClusterExecFailure: Cannot attach to a
container that is not running` against the worker, regardless of the
`--command` content. The container is healthy by every other signal.
Likely interaction between the beta exec websocket and workers
without HTTP ingress. The Job pattern is what the existing migration
and demo-accounts seed already use, so the reseed mirrors that.

## Prerequisites

Before the first run:

- The `5f1d473` deploy (or later) must be on staging. That commit
  shipped the parser disambiguation fix; without it, the
  FR-31702142 fixture fails synthetic-v2 staging on
  `clauses_unique_path_per_version`.
- The reseed Job (`horizons-dev-reseed-corpus`) must exist. It is
  provisioned by `infra/main.bicep` on every deploy; the first deploy
  after `324962a` created it.
- The worker image used by the Job must contain `/app/scripts/` and
  `/app/data/`. Verified by `4b2f3df` (`.dockerignore` exclusions)
  + the worker Dockerfile COPY lines.
- `az login` against the subscription containing `horizons-nonprod`,
  with permission to call `az containerapp job start`.
- These env vars set on the laptop. The script aborts before any Job
  dispatch otherwise:

```bash
export HORIZONS_DEMO_UK_PASSWORD='<long random string>'
export HORIZONS_DEMO_EU_PASSWORD='<long random string>'
export HORIZONS_DEMO_ADMIN_PASSWORD='<long random string>'
```

Note: `HORIZONS_DB_URL` is **not** passed from the laptop. The Job
already has it as a Container Apps secret, bound at deploy time from
the Postgres parts in the staging environment's GitHub secrets.

## Day-to-day

```bash
# 1. Dry-run — prints the worker + Job image tags so you can confirm
# the new build is what the Job will run. Triggers nothing.
./scripts/reseed_aca.sh

# 2. Execute — same safety checks, plus a typed-back confirmation
# (you type the job name to proceed).
./scripts/reseed_aca.sh --yes
```

The dry-run does **not** call `reseed_corpus.py`; it exits before
dispatch. Use it to sanity-check the Job is up to date before
committing to a wipe.

## What a healthy `--yes` run looks like

```
azure subscription: <name> (<sub-id>)
resource group:     horizons-nonprod
reseed job:         horizons-dev-reseed-corpus
worker app:         horizons-dev-worker (for image reference)

worker active image: ghcr.io/johnmathews/horizons-worker:sha-<latest>
job image:           ghcr.io/johnmathews/horizons-worker:sha-<latest>

ABOUT TO WIPE AND RE-SEED the corpus on the deployed worker's DB by
starting Container Apps Job: horizons-dev-reseed-corpus.
…
To confirm, type the job name back exactly:
horizons-dev-reseed-corpus

starting job execution…
execution name:     horizons-dev-reseed-corpus-<exec-id>

[  0s] status: Running
[  5s] status: Running
…
[ 35s] status: Succeeded

Job succeeded.

Recent log lines from the execution:
  [before]
    users                    3
    documents                10
    document_versions        10
    clauses                  ~2000
    change_events            ~150
  …
  OK — wipe + reseed completed.
```

Wall-clock from `start` to `Succeeded` is typically 30-60s.

## Verification

After a successful run, verify the demo accounts see what they should
through the public API. From a laptop with `$DEMO_URL` pointing at the
deployed Front Door endpoint:

```bash
# UK client: should see the GB-relabelled-as-UK synthetic v2 events.
ACCESS=$(curl -sS -X POST "$DEMO_URL/v1/auth/login" \
  -H "content-type: application/json" \
  -d '{"email":"demo-uk@demo.example.com","password":"'"$HORIZONS_DEMO_UK_PASSWORD"'"}' \
  | jq -r .access_token)
curl -sS -H "authorization: Bearer $ACCESS" \
  "$DEMO_URL/v1/changes?limit=5" | jq '.items | length'
# Expect > 0. All rows should have jurisdiction=UK, sector=BANKING.

# EU client: FR ACPR doc, relabelled.
ACCESS=$(curl -sS -X POST "$DEMO_URL/v1/auth/login" \
  -H "content-type: application/json" \
  -d '{"email":"demo-eu@demo.example.com","password":"'"$HORIZONS_DEMO_EU_PASSWORD"'"}' \
  | jq -r .access_token)
curl -sS -H "authorization: Bearer $ACCESS" \
  "$DEMO_URL/v1/changes?limit=5" | jq '.items | length'
# Expect > 0. All rows should have jurisdiction=EU, sector=BANKING.
```

If either returns `0`, walk back through `before`/`after` counts in
the Job logs (`az containerapp job logs show -n horizons-dev-reseed-corpus
-g horizons-nonprod --container reseed --tail 200`) — partial staging
will show a thin `after` snapshot.

## Failure modes

### `FATAL: remaining connection slots are reserved for roles with the SUPERUSER attribute`

Postgres is out of non-superuser connection slots. Root cause: the
worker and API are in `revisionMode: Multiple` and `deploy.yml` never
deactivates the previous revision. After many deploys, dozens of
active revisions each hold a SQLAlchemy pool open. Workaround:

```bash
KEEP=horizons-dev-worker--sha-<latest>
az containerapp revision list --name horizons-dev-worker -g horizons-nonprod \
  --query "[?properties.active && name != '$KEEP'].name" -o tsv | \
  while read r; do
    az containerapp revision deactivate \
      --name horizons-dev-worker -g horizons-nonprod --revision "$r"
  done
# Same loop for horizons-dev-api with that app's latest revision.
```

Wait ~30s for connections to drain, then re-run the reseed. Tracked as
a post-demo follow-up (the captured prompt for a fresh session lives in
the operator's notes).

### `duplicate key value violates unique constraint "clauses_unique_path_per_version"`

A fixture has duplicate sibling slugs that the parser is collapsing
into the same `clause_path`. As of `5f1d473`, the parser
disambiguates these (`_disambiguate_segment` in
`packages/horizons-core/src/horizons_core/core/alignment/parser.py`). If
this shows up again, the deployed Job image is older than `5f1d473` —
check `az containerapp job show -n horizons-dev-reseed-corpus -g
horizons-nonprod --query "properties.template.containers[0].image"`.

### Dry-run shows mismatched worker / Job images

```
NOTE: worker and job point at different image tags.
```

The Job's image is set by Bicep at deploy time; the worker's image
flips on every deploy. Brief mismatch during an in-flight deploy is
normal — wait for the deploy to finish. Persistent mismatch means
Bicep didn't pick up the latest SHA — check `deploy.yml`'s
`workerImage` parameter resolution.

### Pre-flight refuses on `users` count

The `--max-existing-users` guard fired. Default 50. If you're running
against a real client-populated environment by accident, **stop**.
If you genuinely have a staging DB with >50 users (test accounts piled
up?), edit `scripts/reseed_corpus.py` or call the Job's underlying
script with `--max-existing-users <higher>` and dispatch through a
manual `az containerapp job start` — but verify the target first.

## Public-exposure caveats

- The Job's command is hard-coded with `--yes` in Bicep. The local
  wrapper's typed-back confirmation is the only operator-facing gate;
  anyone with `az containerapp job start` permission on the staging
  resource group can wipe the corpus without going through the
  wrapper. Treat job-start RBAC accordingly.
- The reseed wipes only the corpus + demo-account dependants. It does
  **not** touch non-demo users, the audit log, or any client-specific
  state outside the `@demo.example.com` rows. Safe to run repeatedly
  during demo rehearsal.
- Post-demo, deactivate the Job (`az containerapp job stop` if a
  rogue execution lands, or remove from Bicep + redeploy) once it's no
  longer needed. Leaving it around past the demo isn't dangerous (RBAC
  + the pre-flight guard cover it) but it's an attack surface that
  serves no purpose.
