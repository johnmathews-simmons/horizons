#!/usr/bin/env bash
#
# scripts/reseed_aca.sh — wipe + re-seed the staging corpus from a laptop.
#
# Dispatches `scripts/reseed_corpus.py` inside the worker container via
# `az containerapp exec`. The worker image (built from the updated
# `packages/horizons-ingestion/Dockerfile`) carries the curated set, the
# fixture inventory, the synthetic-v2 markdown, and the two seed scripts;
# this wrapper only does the safety dance + remote-exec.
#
# Required env vars on the laptop (passed through to the container):
#   HORIZONS_DEMO_UK_PASSWORD
#   HORIZONS_DEMO_EU_PASSWORD
#   HORIZONS_DEMO_ADMIN_PASSWORD
#
# Optional env vars (default to the staging deployment):
#   RG=horizons-nonprod         resource group
#   WORKER=horizons-dev-worker  container app name
#
# HORIZONS_DB_URL is NOT passed from the laptop — the worker container
# already has it provisioned as a Container Apps secret.
#
# Safety:
#   1. Confirms the active Azure subscription
#   2. Lists the target container app and its active revision
#   3. Requires the operator to type the worker name back to proceed
#   4. The python script itself dry-runs unless --yes is passed
#
# Usage:
#   scripts/reseed_aca.sh              # dry-run inside the container
#   scripts/reseed_aca.sh --yes        # execute the wipe + reseed

set -euo pipefail

RG="${RG:-horizons-nonprod}"
WORKER="${WORKER:-horizons-dev-worker}"

YES_FLAG=""
for arg in "$@"; do
    case "$arg" in
        --yes) YES_FLAG="--yes" ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            echo "unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

# --- 1. Local env vars --------------------------------------------------------

missing=()
for var in HORIZONS_DEMO_UK_PASSWORD HORIZONS_DEMO_EU_PASSWORD HORIZONS_DEMO_ADMIN_PASSWORD; do
    if [[ -z "${!var:-}" ]]; then
        missing+=("$var")
    fi
done
if (( ${#missing[@]} > 0 )); then
    echo "refusing to run: the following env vars are not set on the laptop:" >&2
    printf '  - %s\n' "${missing[@]}" >&2
    echo "set them and re-run." >&2
    exit 1
fi

# --- 2. Azure CLI sanity ------------------------------------------------------

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
echo "worker app:         $WORKER"
echo

# Confirm the worker exists and grab its active revision (informational).
if ! az containerapp show --name "$WORKER" --resource-group "$RG" --query name -o tsv >/dev/null 2>&1; then
    echo "container app '$WORKER' not found in resource group '$RG'." >&2
    echo "set WORKER= and RG= env vars if the target is different." >&2
    exit 1
fi

ACTIVE_REVISION="$(az containerapp revision list \
    --name "$WORKER" --resource-group "$RG" \
    --query "[?properties.active].name | [0]" -o tsv 2>/dev/null || true)"
ACTIVE_IMAGE="$(az containerapp show \
    --name "$WORKER" --resource-group "$RG" \
    --query "properties.template.containers[0].image" -o tsv 2>/dev/null || true)"
echo "active revision:    ${ACTIVE_REVISION:-<unknown>}"
echo "active image:       ${ACTIVE_IMAGE:-<unknown>}"
echo

# --- 3. Typed confirmation ----------------------------------------------------

if [[ "$YES_FLAG" == "--yes" ]]; then
    cat <<EOF
ABOUT TO WIPE AND RE-SEED the corpus on the deployed worker's DB.

To confirm, type the worker app name back exactly:
EOF
    read -r typed
    if [[ "$typed" != "$WORKER" ]]; then
        echo "typed value '$typed' != '$WORKER'. Aborted." >&2
        exit 1
    fi
else
    echo "(dry-run mode — the script inside the container will only print the plan)"
fi

# --- 4. Build the exec command -----------------------------------------------

# Shell-quote each password so any special chars survive the round-trip into
# `az containerapp exec --command`, which itself runs a /bin/sh invocation.
q_uk="$(printf '%q' "$HORIZONS_DEMO_UK_PASSWORD")"
q_eu="$(printf '%q' "$HORIZONS_DEMO_EU_PASSWORD")"
q_admin="$(printf '%q' "$HORIZONS_DEMO_ADMIN_PASSWORD")"

# The container already has HORIZONS_DB_URL as a secret env var (per the
# worker's Bicep template); we only inject the demo passwords. Run from
# /app so the relative `scripts/...` paths resolve.
REMOTE_CMD="cd /app && \
HORIZONS_DEMO_UK_PASSWORD=${q_uk} \
HORIZONS_DEMO_EU_PASSWORD=${q_eu} \
HORIZONS_DEMO_ADMIN_PASSWORD=${q_admin} \
python scripts/reseed_corpus.py ${YES_FLAG}"

# --- 5. Dispatch --------------------------------------------------------------

echo
echo "dispatching reseed_corpus.py inside the worker container…"
echo

az containerapp exec \
    --name "$WORKER" \
    --resource-group "$RG" \
    --command "/bin/sh -c $(printf '%q' "$REMOTE_CMD")"
