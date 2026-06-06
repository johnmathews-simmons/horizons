#!/usr/bin/env bash
#
# scripts/reseed_aca.sh — wipe + re-seed the staging corpus from a laptop.
#
# Dispatches the `horizons-dev-reseed-corpus` Container Apps Job
# (defined in `infra/modules/reseed-corpus-job.bicep`) via
# `az containerapp job start`, polls until it exits, and surfaces the
# in-container logs. The Job reuses the worker image — which has the
# curated set, the fixture inventory, the synthetic-v2 markdown, and
# the three seed scripts baked into /app — and runs
# `python /app/scripts/reseed_corpus.py --yes`.
#
# Why a Job instead of `az containerapp exec`: the exec websocket path is
# fundamentally flaky against workers without HTTP ingress on the current
# Azure-CLI extension (1.3.0b4). The Job pattern is what the existing
# migration + demo-accounts seed already use, so this mirrors that.
#
# All Postgres + demo-password secrets are wired onto the Job at deploy
# time (main.bicep → reseed-corpus-job.bicep); the laptop does NOT need
# HORIZONS_DB_URL or the demo passwords.
#
# Optional env vars:
#   RG=horizons-nonprod                     resource group
#   JOB=horizons-dev-reseed-corpus          job name
#   POLL_INTERVAL=5                         seconds between status polls
#   POLL_TIMEOUT=900                        give-up wall-clock seconds
#
# Safety:
#   1. Confirms the active Azure subscription, resource group, job name.
#   2. Shows the worker's active revision + image tag so you can confirm
#      the Job will pick up the same baked-in scripts you expect.
#   3. Requires the operator to type the job name back to proceed.
#   4. Without --yes, the script does a dry-run (prints the plan; never
#      triggers the Job).
#
# Usage:
#   scripts/reseed_aca.sh              # dry-run
#   scripts/reseed_aca.sh --yes        # execute the wipe + reseed Job

set -euo pipefail

RG="${RG:-horizons-nonprod}"
JOB="${JOB:-horizons-dev-reseed-corpus}"
WORKER="${WORKER:-horizons-dev-worker}"
POLL_INTERVAL="${POLL_INTERVAL:-5}"
POLL_TIMEOUT="${POLL_TIMEOUT:-900}"

YES_FLAG=""
for arg in "$@"; do
    case "$arg" in
        --yes) YES_FLAG="--yes" ;;
        -h|--help)
            sed -n '2,35p' "$0"
            exit 0
            ;;
        *)
            echo "unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

# --- 1. Azure CLI sanity ------------------------------------------------------

if ! command -v az >/dev/null 2>&1; then
    echo "az CLI not on PATH. Install it: https://aka.ms/install-az-cli" >&2
    exit 1
fi

if ! az account show >/dev/null 2>&1; then
    echo "az CLI not logged in. Run: az login" >&2
    exit 1
fi

SUB_NAME="$(az account show --query name -o tsv)"
SUB_ID="$(az account show --query id -o tsv)"
echo "azure subscription: $SUB_NAME ($SUB_ID)"
echo "resource group:     $RG"
echo "reseed job:         $JOB"
echo "worker app:         $WORKER (for image reference)"
echo

# --- 2. Confirm the job exists -----------------------------------------------

if ! az containerapp job show --name "$JOB" --resource-group "$RG" --query name -o tsv >/dev/null 2>&1; then
    echo "Container Apps Job '$JOB' not found in resource group '$RG'." >&2
    echo "If you just pushed the Bicep change, wait for deploy.yml to finish." >&2
    echo "Set JOB= / RG= env vars if the target name is different." >&2
    exit 1
fi

# --- 3. Show what's currently deployed on the worker -------------------------

ACTIVE_IMAGE="$(az containerapp show \
    --name "$WORKER" --resource-group "$RG" \
    --query "properties.template.containers[0].image" -o tsv 2>/dev/null || true)"
JOB_IMAGE="$(az containerapp job show \
    --name "$JOB" --resource-group "$RG" \
    --query "properties.template.containers[0].image" -o tsv 2>/dev/null || true)"
echo "worker active image: ${ACTIVE_IMAGE:-<unknown>}"
echo "job image:           ${JOB_IMAGE:-<unknown>}"
if [[ -n "$ACTIVE_IMAGE" && -n "$JOB_IMAGE" && "$ACTIVE_IMAGE" != "$JOB_IMAGE" ]]; then
    echo
    echo "NOTE: worker and job point at different image tags. The Job uses"
    echo "      its own image — check that '$JOB_IMAGE' is the one you expect"
    echo "      (it must contain /app/scripts/reseed_corpus.py and /app/data/)."
fi
echo

# --- 4. Dry-run early exit ----------------------------------------------------

if [[ "$YES_FLAG" != "--yes" ]]; then
    cat <<EOF
dry-run mode — no Job triggered.

To execute the wipe + reseed:

    $0 --yes

You'll be asked to type the job name ($JOB) back as confirmation.
The Job runs python /app/scripts/reseed_corpus.py --yes inside its
own replica; logs stream to stdout via the polling loop and to
'az containerapp job execution show' / 'az containerapp job logs show'.
EOF
    exit 0
fi

# --- 5. Typed confirmation ----------------------------------------------------

cat <<EOF
ABOUT TO WIPE AND RE-SEED the corpus on the deployed worker's DB by
starting Container Apps Job: $JOB.

The Job runs reseed_corpus.py --yes, which:
  - deletes from change_events, clauses, document_versions,
    document_poll_schedule, documents (transactionally)
  - re-runs seed_curated_set.py --stage-synthetic-v2
  - re-runs create_demo_accounts.py --reset

To confirm, type the job name back exactly:
EOF
read -r typed
if [[ "$typed" != "$JOB" ]]; then
    echo "typed value '$typed' != '$JOB'. Aborted." >&2
    exit 1
fi

# --- 6. Start the Job ---------------------------------------------------------

echo
echo "starting job execution…"
EXEC="$(az containerapp job start --name "$JOB" --resource-group "$RG" --query name -o tsv)"
echo "execution name:     $EXEC"
echo

# --- 7. Poll until terminal --------------------------------------------------

elapsed=0
while (( elapsed < POLL_TIMEOUT )); do
    STATUS="$(az containerapp job execution show \
        --name "$JOB" \
        --resource-group "$RG" \
        --job-execution-name "$EXEC" \
        --query "properties.status" -o tsv 2>/dev/null || echo "Unknown")"
    printf '[%3ds] status: %s\n' "$elapsed" "$STATUS"
    case "$STATUS" in
        Succeeded)
            echo
            echo "Job succeeded."
            echo
            echo "Recent log lines from the execution:"
            az containerapp job logs show \
                --name "$JOB" \
                --resource-group "$RG" \
                --container reseed \
                --tail 50 \
                2>/dev/null || echo "(logs not available — try 'az containerapp job logs show -n $JOB -g $RG --container reseed' manually)"
            exit 0
            ;;
        Failed|Stopped|Degraded)
            echo
            echo "Job ended non-success: $STATUS" >&2
            echo "Execution details:"
            az containerapp job execution show \
                --name "$JOB" \
                --resource-group "$RG" \
                --job-execution-name "$EXEC" \
                --query "properties" -o json
            echo
            echo "Recent log lines:"
            az containerapp job logs show \
                --name "$JOB" \
                --resource-group "$RG" \
                --container reseed \
                --tail 100 \
                2>/dev/null || echo "(logs not available)"
            exit 1
            ;;
    esac
    sleep "$POLL_INTERVAL"
    elapsed=$(( elapsed + POLL_INTERVAL ))
done

echo
echo "Timed out waiting for job (>${POLL_TIMEOUT}s); last status: $STATUS" >&2
echo "Tail logs with:" >&2
echo "  az containerapp job logs show -n $JOB -g $RG --container reseed --tail 200" >&2
exit 1
