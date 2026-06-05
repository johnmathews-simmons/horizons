# WU6.6 — Drift check workflow

*Session 2026-06-05. Branch `worktree-wu6.4-6.5-6.6-migrations-docs-drift` (Session D).*

A nightly GitHub Actions workflow that runs `az deployment group
what-if` against the live infra RG and opens a GitHub Issue when the
what-if change set is non-empty. Catches console-side / `az`-CLI
drift within 24h.

## What shipped

```
.github/workflows/drift-check.yml    Schedule (03:00 UTC) +
                                     workflow_dispatch.
                                     OIDC via azure/login@v2,
                                     `staging` environment.
                                     RG: horizons-nonprod.
                                     Issue label: infra-drift.
```

`python3 -c "import yaml; yaml.safe_load(...)"` → OK on the YAML. No
`actionlint` available locally; pre-commit covers yamllint-style
checks and CI will surface schema-level diagnostics on first run.

## Architectural decisions reflected

1. **OIDC, not a stored secret.** Per locked-in plan §10 and the
   posture established by WU6.1, every Azure-touching workflow
   authenticates with `azure/login@v2` against the existing UAMI
   (`horizons-github-oidc`). Reads `vars.AZURE_CLIENT_ID`,
   `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` — the same three repo
   variables WU6.1 provisioned. No new secret introduced.
2. **`staging` environment, not `production`.** WU6.1 created
   federated credentials for both `staging` and `production` GitHub
   environments, but only the `staging` cred is bound to the
   `horizons-nonprod` RG. No `horizons-prod` RG exists yet (it's
   post-demo work). When a prod RG lands, the `environment:` block
   flips to `production` and the `AZURE_RESOURCE_GROUP` env var
   flips to the prod name. The workflow header calls this out so
   the future flip is one-PR-of-trivial-diff.
3. **`gh issue create` with label `infra-drift`, not Slack.** The
   plan's WU6.6 acceptance criterion lists Slack OR a GH issue.
   No Slack webhook is configured in this repo; the GH issue path is
   the operationally cheapest option, surfaces in the same dashboard
   as PR review, and is mute-able / archivable per-issue.
4. **`--no-pretty-print -o json` for what-if.** The default what-if
   output is a human-readable change report; the workflow needs
   structured JSON so a `jq` filter can decide whether the change
   set is non-empty. `--no-pretty-print` is the documented opt-in to
   the machine-readable shape — `-o json` alone doesn't suppress the
   pretty-print preamble.
5. **`Ignore` and `NoChange` filtered out before counting.** Azure's
   what-if returns these for resources that exist and match. Only
   `Create`/`Modify`/`Delete`/`Deploy`/`Unsupported` count as actual
   drift; counting `NoChange` rows would mean every nightly run
   filed an issue.
6. **Defense-in-depth against workflow injection.** The
   PostToolUse:Write hook flagged the workflow on the first save.
   The data being interpolated (what-if JSON, run number, run URL)
   comes from Azure ARM or `github.*` context, none of which is
   attacker-controlled in this workflow's trigger set
   (schedule + workflow_dispatch with no inputs). The defensive
   refactor moves every `${{ ... }}` expression that lands inside a
   `run:` block into an `env:` declaration on the step, and the
   shell body references `$VAR` — the GitHub-recommended pattern
   that stays correct even if a future trigger (e.g. an
   `issue_comment`) introduces attacker-controlled data.

## Decisions inline that warrant a paper trail

- **Concurrency group `drift-check-staging`, `cancel-in-progress:
  false`.** A manual `workflow_dispatch` fired while the scheduled
  run is mid-flight would otherwise double-create the drift issue
  (or step on each other's `gh label create`). Sequential is safer
  than parallel here.
- **Throwaway `postgresAdminPassword='drift-check-ephemeral'` for
  the what-if.** The example parameters file marks the password as a
  Key Vault reference; what-if won't resolve KV refs without an
  extra `--parameters-key-vault-reference` flag we haven't wired.
  The value never reaches Azure (what-if is read-only), so a
  throwaway literal is fine — and clearer than threading a fake KV
  reference through the workflow.
- **`set -euo pipefail` in every multi-line `run:`.** Strict mode
  catches a `jq` parse failure or an `az` non-zero exit instead of
  silently passing.
- **Label colour `B60205` (red).** Surfaced in the issue list so
  drift entries are visually distinct from regular issues. Cosmetic
  but cheap.

## Things deliberately deferred

- **Auto-correction.** The plan explicitly says "No
  auto-correction." The workflow only detects and reports; remediation
  is operator work (port the drift back to Bicep, or revert via
  `az deployment group create`). Auto-correction at demo scale would
  loop in unexpected ways and is the wrong default.
- **Drift trend metric.** A future enhancement: append the
  drift-count and timestamp to a CSV in an Azure Storage table or a
  GH repo file, then chart it. Skipped — one nightly issue is the
  signal; trend tracking is overengineering until we have history
  worth charting.
- **Prod RG support.** Skipped until `horizons-prod` exists.
  Documented in the workflow header so the flip is obvious.
- **PR-time what-if preview.** WU6.1 deliberately left the
  `pull_request` federated credential off the UAMI. Adding what-if
  as a PR check would be valuable, but it'd require either the new
  cred or running what-if against a separate "sandbox" RG. Deferred.
- **Failure path notification.** If the workflow itself errors
  (e.g. `az login` fails because the UAMI was deleted), GitHub
  sends the run-failure email to the repo owner. No additional
  alerting wired.

## Verification gate

End-of-session Python sweep + Bicep build (covering WU6.4 + WU6.5 +
WU6.6 together):

```bash
uv run ruff check . && uv run pyright && uv run pytest -m "not integration" && uv run pre-commit run --all-files
az bicep build --file infra/main.bicep
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/drift-check.yml'))"
```

Results are captured at the bottom of this entry once the gate runs.

## External verification (user-only — flagged for post-merge)

The first scheduled run won't fire until the next 03:00 UTC after
merge. To verify the workflow shape immediately:

```bash
# After ff-merging this branch to main:
gh workflow run drift-check.yml --ref main
# Watch the run:
gh run watch  # or: gh run list --workflow=drift-check.yml
```

Expected first-run behaviour against `horizons-nonprod` (assuming
the RG is empty post-WU6.0/WU6.4 — no actual deployment has been
applied):

- `az deployment group what-if` returns a change set with
  `Create` entries for every resource in `infra/main.bicep`
  (because the RG has nothing in it yet).
- The detect step counts those Creates as drift (the RG is
  drifted *away from* the declared state).
- An issue is opened titled `Infra drift detected: N change(s) in
  horizons-nonprod` listing the missing resources.

This is the **correct first-run behaviour** — it confirms the
workflow is wired right. Close the issue, run the actual
`az deployment group create` to converge the RG to the declared
state, and the next scheduled run will report zero drift and stay
silent.

If the first manual run fails before reaching the detect step,
likely culprits:

- `azure/login@v2` failure → the UAMI's `staging` federated
  credential subject doesn't match `repo:johnmathews/horizons:environment:staging`.
  Check `az identity federated-credential list -g horizons-nonprod
  --identity-name horizons-github-oidc`.
- `az bicep install` failure → the runner image's `az` is too old.
  Pin a newer ubuntu image or install `az` explicitly.
- `az deployment group what-if` failure with `ResourceGroupNotFound`
  → `horizons-nonprod` was deleted; recreate or update
  `AZURE_RESOURCE_GROUP` to the current RG name.

## Workflow injection follow-up

The PostToolUse:Write security guidance hook fired on the first
save of `drift-check.yml`. The original draft templated `${{
github.run_number }}`, `${{ steps.detect.outputs.summary }}`, etc.
directly inside a `cat <<EOF ... EOF` heredoc in a `run:` block.
None of those values are attacker-controlled in the current trigger
set (schedule + workflow_dispatch with no inputs), but the safer
pattern is `env: NAME: ${{ expr }}` followed by `$NAME` in the
shell body. The refactor in this commit moves every templated
expression into a step-level `env:` block. Pattern carried over to
any future Azure-touching workflow.

## Next pickup

- **Track 6 remainder.** WU6.3 (`deploy.yml`) is the only Track 6
  unit left; depends on WU4.4 shipping a callable API and on
  WU6.4's migration Job (now landed). Pre-WU6.3 housekeeping: add a
  required-reviewer rule on the `production` GH Environment (flagged
  in the WU6.1 journal — still open).
- **Track 7.** WU7.0 (OTel) is the natural next track once Track 4
  has endpoints; independent of this WU.
