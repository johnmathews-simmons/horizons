# WU6.1 — OIDC federation (GitHub Actions → Azure)

*Last revised: 2026-06-05.*
*Path: journal/260605-wu61-oidc-federation.md.*

*Session 2026-06-05. No code branch — Azure portal + CLI provisioning + one-off
verification workflow that was deleted after the gate passed.*

The piece that lets the CI/CD pipeline (WU6.3 + WU6.4) authenticate to Azure
without storing client secrets. Short-lived OIDC tokens issued by GitHub are
exchanged for Azure AD access tokens via a federated credential trust
relationship on a user-assigned managed identity (UAMI).

Verification: a throwaway workflow ran `az account show` and `az group list`
under federation — both green. Workflow file deleted after.

## What shipped

Nothing in the application tree. The deliverables are Azure resources and
GitHub repo configuration:

```
Azure (resource group horizons-nonprod, westeurope):
└── userAssignedIdentities/horizons-github-oidc
    ├── clientId       6140faf5-16fd-4183-b061-f855832a8f29
    ├── principalId    89919d91-c412-40af-b0e9-4c2e97594c52
    ├── tenantId       1da7933f-cc3f-4e63-8af7-f30a01b8af3d
    └── federatedIdentityCredentials/
        ├── github-staging      → repo:johnmathews/horizons:environment:staging
        └── github-production   → repo:johnmathews/horizons:environment:production

Azure RBAC:
└── Contributor on /subscriptions/76669a70-…/resourceGroups/horizons-nonprod
    for principalId 89919d91-c412-40af-b0e9-4c2e97594c52

GitHub repo configuration (johnmathews/horizons):
├── Environments: staging, production
└── Repository variables:
    ├── AZURE_CLIENT_ID         = 6140faf5-16fd-4183-b061-f855832a8f29
    ├── AZURE_TENANT_ID         = 1da7933f-cc3f-4e63-8af7-f30a01b8af3d
    └── AZURE_SUBSCRIPTION_ID   = 76669a70-f46f-496b-a54e-3e61ab3eeb66
```

## Architectural decisions reflected

1. **OIDC federation, not a service-principal client secret.** Locked-in plan
   §10: "OIDC federation to Azure (no client secrets)." The only secret-shaped
   thing in this system is the short-lived JWT GitHub mints per workflow run,
   and it never touches disk on either side.
2. **A single UAMI for both environments.** Two federated credentials hang off
   the same identity — one matching the `staging` GitHub Environment subject,
   one matching `production`. Cheaper than two identities and one less moving
   part for the demo. The split-by-environment posture lives in GitHub
   Environments (which gate the workflow), not in separate Azure principals.
3. **Repository variables, not secrets, for the three IDs.** `AZURE_CLIENT_ID`,
   `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID` are identifiers, not
   credentials. Without the federated-credential trust relationship they
   identify nothing actionable. Storing them as variables makes them visible
   in workflow logs, which helps debugging.
4. **Contributor on the resource group.** Locked-in plan §10 doesn't specify
   the RBAC role; Contributor is the smallest stock role that lets ACA Job
   runs (WU6.4), revision deploys (WU6.3), and the Front Door config tweaks
   (WU5.1's runtime config / cache purge) all work without per-action role
   tuning. Tightening to a tailored role definition is post-demo work, flagged
   below.

## Decisions inline that warrant a paper trail

- **No production reviewers configured yet.** The `production` GitHub
  Environment was created with no required-reviewers rule, which means a
  deploy to production fires without manual approval. This is fine right now
  because WU6.3 hasn't shipped and nothing yet pushes to production — but
  before WU6.3 lands, add at least one required reviewer (`johnmathews`) to
  the `production` environment so a deploy can't fire unattended.
- **No `pull_request` federated credential.** A common third subject pattern
  is `repo:johnmathews/horizons:pull_request`, which would let CI run
  Azure-touching steps on PR branches (e.g. `az deployment what-if` as a PR
  gate). Deliberately not added — keeps the demo posture conservative (only
  merged-to-main code can touch Azure). Add if PR-time infra previewing
  becomes worth the complexity.
- **No environment-scoped variables.** All three `AZURE_*` IDs are
  repository-level variables, identical across staging and production.
  Per-environment variables would be the right shape only if we had separate
  subscriptions / tenants per environment, which we don't.
- **Federated credential subject is environment-typed, not branch-typed.** A
  branch-scoped subject (`repo:johnmathews/horizons:ref:refs/heads/main`)
  would also work and gates by branch instead of by GitHub Environment.
  Environment-scoped is more flexible (lets us add required reviewers,
  per-env secrets, deployment history) and matches how `azure/login@v2`
  examples document the pattern.

## Verification gate

The test workflow lived at `.github/workflows/test-oidc.yml` for a single run
and was deleted after passing:

```yaml
name: Test OIDC
on: workflow_dispatch
permissions:
  id-token: write
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    environment: staging
    steps:
      - uses: azure/login@v2
        with:
          client-id: ${{ vars.AZURE_CLIENT_ID }}
          tenant-id: ${{ vars.AZURE_TENANT_ID }}
          subscription-id: ${{ vars.AZURE_SUBSCRIPTION_ID }}
      - run: az account show
      - run: az group list -o table
```

Run: <https://github.com/johnmathews/horizons/actions/runs/27025898769> → green.
`az group list` showed `horizons-nonprod` plus the existing system RGs.

The `permissions: id-token: write` block is non-negotiable — without it the
runner has no OIDC token to exchange and `azure/login` fails with "Could not
find any credentials" before any Azure call happens.

## Things deliberately deferred

- **Tighter RBAC.** Contributor on the RG is broader than needed. Post-demo,
  carve out: `Container Apps Contributor` on the ACA env, `Storage Blob Data
  Contributor` on the SPA container, `Key Vault Secrets User` on the vault,
  `Reader` everywhere else. Per-resource role assignments via Bicep would also
  be cleaner than the imperative `az role assignment` used here.
- **Separate UAMI per environment.** Two identities — one Contributor on a
  staging-only RG, one Contributor on a production-only RG — would block a
  staging-workflow leak from touching production. Worth doing once a separate
  `horizons-prod` RG exists.
- **Production environment reviewers.** Before WU6.3 ships, add at least one
  required reviewer to the `production` Environment.
- **Branch-protection coupling.** GitHub Environments can require deployments
  to come from specific branches; we haven't constrained the `production`
  environment to `main`-only. Cheap fix to add when WU6.3 lands.

## Next pickup

- **WU6.4 (migration ACA Job)** is now fully unblocked — depends on WU3.1
  (schema, ✅) and WU6.0 (Bicep, ✅). Ready to start when an agent or session
  is available.
- **WU6.3 (deploy.yml)** is blocked only on WU4.4 (a callable API to
  smoke-test against). When WU4.4 lands, WU6.3 has everything it needs.
- **Pre-WU6.3 housekeeping:** add a required-reviewer rule on the `production`
  GitHub Environment and (optionally) constrain it to the `main` branch.
