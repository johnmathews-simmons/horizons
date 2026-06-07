# Demo runbook (WU8.3)

*Last revised: 2026-06-06.*
*Path: docs/runbooks/demo.md.*

*Audience: operator running or supervising the public demo on
~2026-06-08. The showcase is public for 1–2 days, so every choice in
this runbook is shaped by that exposure window.*

Companion runbooks:

- [demo-accounts.md](./demo-accounts.md) — provisioning the three
  `@demo.example.com` accounts the demo uses.
- [deploy.md](./deploy.md) — `deploy.yml` mechanics, rollback, and
  the Front Door / ACA boundaries this runbook treats as a black box.
- [migrations.md](./migrations.md) — the expand-contract rule and the
  migration ACA Job referenced from the pre-demo checklist.

## 1. Pre-demo checklist

Run this list end-to-end at least 24 h before the demo opens. Each
item is independently verifiable; tick the box only when its check has
succeeded *against the deployed staging environment*, not against
localhost.

- [ ] **Azure provider registrations are green.** All required
      Resource Providers report `Registered`:
      ```bash
      for ns in Microsoft.App Microsoft.OperationalInsights \
                Microsoft.Insights Microsoft.Cdn Microsoft.Storage \
                Microsoft.DBforPostgreSQL Microsoft.ManagedIdentity \
                Microsoft.KeyVault; do
        az provider show --namespace "$ns" \
          --query "registrationState" -o tsv | xargs -I{} echo "$ns: {}"
      done
      ```
      Any `NotRegistered` blocks the next deploy.
- [ ] **Bicep `what-if` is clean against `horizons-nonprod`.** Run the
      drift check workflow's local equivalent (see
      [deploy.md](./deploy.md) → "Prerequisites that must exist"):
      ```bash
      az deployment group what-if \
        --resource-group horizons-nonprod \
        --template-file infra/main.bicep \
        --parameters @infra/main.parameters.staging.json
      ```
      Expected: `no changes` or only the image-tag deltas for the SHA
      that built the current deploy.
- [ ] **First end-to-end deploy via `deploy.yml` succeeded.**
      Cross-reference [deploy.md → "What a healthy run looks like"](./deploy.md#what-a-healthy-run-looks-like).
      Specifically: the new API revision serves 100 % traffic, the
      worker revision is `Succeeded`, the SPA bundle is in `$web`, and
      the Front Door purge step returned success.
      ```bash
      az containerapp ingress traffic show \
        --name horizons-dev-api \
        --resource-group horizons-nonprod \
        -o table
      curl -fsS "https://<api-fqdn>/healthz"
      curl -fsS "https://<api-fqdn>/openapi.json" | jq '.info.title'
      ```
- [ ] **Migration ACA Job ran successfully and Alembic is at head.**
      The `deploy.yml` pipeline drives this; verify after the fact:
      ```bash
      az containerapp job execution list \
        --name horizons-dev-migrate \
        --resource-group horizons-nonprod \
        --query "[0].{status:properties.status,started:properties.startTime}" \
        -o table
      ```
      Expected: `status: Succeeded`. If a manual rerun is needed, see
      [migrations.md](./migrations.md).
- [ ] **Curated set seeded against the deployed DB.** Synthetic v2
      documents are staged *and* their poll-schedule rows are parked
      past the demo window. The seed script does both atomically when
      called with `--stage-synthetic-v2`:
      ```bash
      HORIZONS_DB_URL='postgresql+psycopg://...prod-creds...' \
        uv run python scripts/seed_curated_set.py --stage-synthetic-v2
      ```
      The staging guard sets `document_poll_schedule.next_poll_at` to
      `2026-12-31 00:00:00+00` for every synthetic-v2 row so the
      worker tick cannot overwrite the headline diff during the show.
      See [Session P journal](../../journal/260605-fix-worker-staged-guard-and-env-validation.md).
- [ ] **Demo accounts provisioned.** The three `@demo.example.com`
      accounts (UK client, EU client, admin) exist with the
      operator-chosen passwords. Use the script and checklist in
      [demo-accounts.md](./demo-accounts.md#provisioning); the
      env-vars `HORIZONS_DEMO_{UK,EU,ADMIN}_PASSWORD` MUST be set —
      `--allow-dev-defaults` is never acceptable for the public host.
- [ ] **Playwright e2e smoke is green on `main`.** Find the most
      recent successful `e2e` workflow run on `main` and paste the
      URL into the operator's notes:
      ```bash
      gh run list --workflow=e2e.yml --branch=main \
        --limit 5 --json conclusion,createdAt,url \
        | jq -r '.[] | select(.conclusion=="success") | .url' \
        | head -1
      ```
      Most recent green run URL (operator fills in day-of): `__________`.
- [ ] **Front Door endpoint resolves to the SPA bundle.** Curl the
      apex; the response must be the Vite-built `index.html`, not a
      404 from the storage origin:
      ```bash
      DEMO_URL='https://<front-door-endpoint-hostname>'
      curl -fsS "$DEMO_URL/" | head -20 | grep -E '<div id="app">' \
        && echo "SPA bundle reachable"
      curl -fsS "$DEMO_URL/config.json" | jq '.api_base_url'
      ```
      A miss here usually means the post-first-deploy
      `az storage blob service-properties update --static-website`
      flip never ran — see [deploy.md → "Prerequisites…"](./deploy.md#prerequisites-that-must-exist-before-the-first-deploy).
- [ ] **Manual login as each demo account, end-to-end.** Open a fresh
      private window, navigate to `$DEMO_URL/login`, and complete the
      login → `/changes` round-trip for each of `demo-uk`, `demo-eu`,
      and `admin-demo`. This is the only check that exercises the
      cookie-shaped refresh flow against the public host. The curl
      sanity checks in [demo-accounts.md](./demo-accounts.md#curl-sanity-check-before-the-demo)
      cover the programmatic shape but not the browser shape.

## 2. Demo script

Numbered steps mirror the order to walk through live. Each step
lists the URL or action, the expected visible UI state, and an
optional spoken one-liner. Times below are typical wall-clock budgets
for an unhurried walk-through; total ≈ 7–9 minutes.

### a. Setup (≈ 60 s, do before the audience arrives)

1. **Open browser, hide dev tools.** Use a fresh profile or private
   window so no cached token leaks the previous run's state.
2. **Mirror screen / projector confirmed.** Confirm the audience can
   see the URL bar — the change of email between `demo-uk` and
   `demo-eu` is part of the story.
3. **Warm the API.** Hit `$DEMO_URL/healthz` once via the browser
   address bar (or curl in a side terminal) to pre-warm the ACA
   replica. ACA scales to zero when idle; the first cold request
   after idle can return 504. See [Recovery → API cold-start](#api-cold-start).
4. **Tabs ready, not open.** Keep `$DEMO_URL/login` as the only tab.
   Avoid having the e2e workflow run page or a previous demo tab
   visible — those leak internal naming the audience shouldn't see.

### b. UK client walk-through (≈ 2 min)

1. **Navigate to `$DEMO_URL/login`.** The login form is the only
   visible element; no nav, no chrome.
   *Say:* "This is the client-facing app. We'll log in as a
   UK-scoped customer first."
2. **Log in as `demo-uk@demo.example.com`.** Submit the password from the
   operator's notes. On success the SPA navigates to `/changes`.
3. **Browse `/changes`.** The list renders recent change events
   filtered to the UK + BANKING subscription scope. Each
   row carries: clause path, change mode (ADDED / REMOVED / MODIFIED
   / MOVED), relative timestamp, and a tiered confidence badge
   (red / amber / green).
   *Say:* "Each row is a clause-level change the watcher detected.
   The confidence badge is the alignment score; below 0.6 is hidden
   by default."
4. **Open a MODIFIED event from the curated set.** Click the row for
   the seeded UK v1 → v2 pair. The detail view renders the
   side-by-side clause diff with the title, jurisdiction, sector,
   confidence badge, and mode toggle in the header.
   *Say:* "This is the headline moment — a redline at the clause
   level, not at the document level. A new version of a regulation
   typically only touches a handful of clauses; this is which ones."
5. **Toggle side-by-side ↔ unified.** The mode toggle in the header
   flips between two-column (default) and inline `<ins>` / `<del>`
   spans.
6. **Toggle the MOVED filter back on the list view.** Return to
   `/changes`, flip the MOVED filter on, and point out that
   relocated clauses appear with `before → after` paths. Then turn
   it back off.
   *Say:* "MOVED events are hidden by default because they are
   noisier — a renumbered Part can produce dozens of them — but the
   filter is there when needed."

### c. EU client → subscription scoping (≈ 90 s)

1. **Log out via the top-right menu.** The SPA returns to `/login`.
   The HttpOnly refresh cookie is revoked server-side.
2. **Log in as `demo-eu@demo.example.com`.** Same password discipline as
   above.
3. **Browse `/changes`.** The list renders **disjoint** content from
   the UK view — same query path, different rows, because the
   EU + BANKING subscription scopes the corpus rows the
   client role can read.
   *Say:* "Same URL, same query, different account. Each client only
   ever sees the corpus rows their subscription covers. A UK-only
   client cannot see EU change events at all; this is enforced both
   at the repository layer and via a Postgres RLS scope policy."
4. **Click an EU row, then back.** Just to demonstrate the same diff
   substrate works on a different jurisdiction's content.

### d. Admin view + support view

1. Open a private window. Visit the SPA URL.
2. Log in with the admin credentials from
   `docs/runbooks/demo-accounts.md`. **Expected**: lands on
   `/admin/clients`, not `/`.
3. Walk the audience through the clients table. Mention the
   `Page 1 of N` indicator and the per-row Open button.
4. Open the UK demo client's detail page. Highlight the active
   subscription's scopes.
5. Add a new scope (e.g. `FR` + `banking`). The new row appears in
   the scopes table; the toast reads "Scope added".
6. Remove that scope. **Expected**: a confirmation modal listing
   matching documents from the discovery feed. Click Cancel — the
   modal closes and no API call fires.
7. Re-open Remove and confirm. **Expected**: success toast reads
   "Scope removed" (and "— N watchlists soft-hidden" if any).
8. Click "Enter support view". **Expected**: amber banner appears
   at the top of every page; tab title shows `[SUPPORT] Horizons`.
9. Navigate to `/changes` and `/watchlists` to show the banner
   persists across routes. Mention that the SPA is now rendering
   the **client's** view, with the client's scopes — same code,
   different bearer.
10. Click "Exit support view" in the banner. **Expected**: banner
    disappears, tab title returns to `Horizons`, lands on
    `/admin/clients`.
11. Open `/admin/audit` and filter `action=impersonation`. **Expected**:
    a row recording the impersonation event, with the admin's id,
    the target client's id, and the timestamp.

Recovery: if the SPA gets stuck in support view (e.g. a network blip
hid an exit toast), reload the page. The cookie-driven cold bootstrap
re-enters as the admin; the in-memory impersonation token is gone.

### e. Wrap (≈ 30 s)

1. **Log out.** Top-right menu → Logout. SPA returns to `/login`.
2. **Close the tab.** Leaving the browser on `/login` (not on an
   authenticated view) is the safe terminal state for an unattended
   public demo.

## 3. Recovery steps

Common failure modes during the demo, ordered roughly by likelihood.
Each entry is: **symptom** → **quick check** → **recovery**. Keep this
section open in a side window during the show.

### API cold-start

- **Symptom:** First request after a quiet period returns 504, or the
  login button spins for ~10–30 s before responding.
- **Why:** ACA scales to zero when idle. The first request after the
  scale-to-zero window pays the cold-start cost while a replica is
  created and the FastAPI app boots.
- **Quick check:**
  ```bash
  curl -fsS -w '%{http_code} %{time_total}s\n' \
    "$DEMO_URL/healthz" -o /dev/null
  ```
  If it returns `200 0.x` immediately, the replica is warm.
- **Recovery:**
  1. **Prevention:** hit `/healthz` once during the Setup phase
     (script step a.3) so the replica is warm before the audience
     sees anything.
  2. **Mid-demo bite:** quietly open `$DEMO_URL/login` in a second
     tab and let it bootstrap; the SPA's auth-store init hits the
     refresh endpoint, which warms the API. By the time you click
     back to the demo tab the replica is up.
  3. **Persistent:** if the cold-start cost is hurting repeatedly,
     scale-from-zero is the wrong default for a demo window. Apply
     the manual scale-up under [DB / API saturation](#db--api-saturation)
     for the same effect.

### Front Door cache stale after a redeploy

- **Symptom:** Audience sees a SPA build that doesn't match the
  current `main` — old copy, stale `config.json`, or pre-fix UI.
- **Why:** `deploy.yml` purges `/`, `/index.html`, and `/config.json`
  on every deploy, but a manual edit or a deploy that didn't reach
  the purge step leaves Front Door caching the old origin response.
- **Quick check:**
  ```bash
  curl -sI "$DEMO_URL/index.html" | grep -E '^(etag|last-modified|x-azure-ref)'
  ```
  Compare the etag to the `$web` blob's etag in the storage account.
- **Recovery:**
  ```bash
  az afd endpoint purge \
    --resource-group horizons-nonprod \
    --profile-name <front-door-profile-name> \
    --endpoint-name <front-door-endpoint-name> \
    --content-paths '/' '/index.html' '/config.json' \
    --domains <front-door-endpoint-hostname>
  ```
  Then hard-reload in the browser (Cmd-Shift-R). See
  [deploy.md → `deploy-spa`](./deploy.md#deploy-spa) for the purge
  semantics; Vite's hashed asset URLs do not need purging.

### Expired Lawstronaut token (worker can't poll)

- **Symptom:** The worker logs `401 Unauthorized` against
  `api.lawstronaut.com/v2` on every tick; no new change events
  appear in `/changes` for any subscription.
- **Demo impact:** **None during the show.** The curated set is
  already seeded against the deployed DB (checklist item 5) and the
  synthetic-v2 documents have their poll-schedule rows parked at
  `2026-12-31`. The headline diff renders from already-persisted
  rows; nothing in the demo path depends on a live worker poll.
- **Recovery:** Deferred to post-demo. Refresh the Lawstronaut
  credential via
  [`scripts/fetch_fixtures.py`](../../scripts/fetch_fixtures.py)'s
  login flow (see [docs/api/operational-notes.md](../api/operational-notes.md)),
  then redeploy the worker so the new secret is picked up.

### Admin demo account locked out

- **Symptom:** Login as `admin-demo@demo.example.com` returns 401, or
  the audit view shows so many entries from rehearsal that the live
  demo's entry is hard to spot.
- **Quick check:** Run the curl sanity check from
  [demo-accounts.md](./demo-accounts.md#curl-sanity-check-before-the-demo)
  against the admin account.
- **Recovery:** Re-run the demo-accounts script with `--reset`
  against the deployed DB, then re-provision:
  ```bash
  HORIZONS_DB_URL='postgresql+psycopg://...prod-creds...' \
  HORIZONS_DEMO_UK_PASSWORD="$DEMO_UK_PW" \
  HORIZONS_DEMO_EU_PASSWORD="$DEMO_EU_PW" \
  HORIZONS_DEMO_ADMIN_PASSWORD="$DEMO_ADMIN_PW" \
    uv run python packages/horizons-api/scripts/create_demo_accounts.py --reset
  ```
  This wipes only the `@demo.example.com` rows; corpus data and other
  client accounts are untouched. See
  [demo-accounts.md → "Reset between dry-runs"](./demo-accounts.md#reset-between-dry-runs).

### DB / API saturation

- **Symptom:** Login or `/changes` takes seconds, the API logs show
  connection-pool waits, or the ACA scale rule has pinned the
  replica count at the maximum.
- **Quick check:**
  ```bash
  az containerapp revision list \
    --name horizons-dev-api \
    --resource-group horizons-nonprod \
    --query "[?properties.active].{name:name, replicas:properties.replicas, weight:properties.trafficWeight}" \
    -o table
  ```
- **Recovery (stopgap):** force a minimum-replica floor so a fresh
  request never hits a cold replica during the show:
  ```bash
  az containerapp update \
    --name horizons-dev-api \
    --resource-group horizons-nonprod \
    --min-replicas 2
  ```
  Revert after the demo with `--min-replicas 0` so the staging cost
  drops back to scale-to-zero.

### Browser cache shows a stale SPA bundle

- **Symptom:** A reload picks up an older build than the one
  currently in `$web` — usually visible as missing fixes or stale
  copy that you know shipped.
- **Quick check:** Open dev tools (briefly, then close again) →
  Network → Disable cache → reload. If the issue clears with cache
  disabled, the browser had a stale copy.
- **Recovery:**
  1. **First:** hard reload (Cmd-Shift-R on macOS, Ctrl-F5 on
     Windows / Linux).
  2. **If persistent:** the Front Door cache is also stale — apply
     the purge from [Front Door cache stale after a redeploy](#front-door-cache-stale-after-a-redeploy)
     above.
  3. **If still persistent:** the browser profile may have a
     service-worker registration from a prior dev session. Use a
     fresh private window.

## 4. Public-exposure caveats

The demo is publicly accessible for 1–2 days. Treat the showcase as a
short-lived public deployment, not as an internal preview.

> All copy and sample data must be generic — no firm name, no client
> names, no real bank names.

(verbatim from [CLAUDE.md → "What this repo is"](../../CLAUDE.md))

Operational guidance derived from that constraint:

- **Pre-exposure grep.** Before opening the demo URL to the public,
  grep the deployed SPA bundle and seeded corpus for any firm name
  or bank name that might have leaked in via copy edits or fixture
  imports:
  ```bash
  curl -sS "$DEMO_URL/" -o /tmp/demo-index.html
  curl -sS "$DEMO_URL/config.json" -o /tmp/demo-config.json
  grep -iE '<known-firm-or-bank-pattern>' \
    /tmp/demo-index.html /tmp/demo-config.json
  ```
  Run the same grep over `data/curated_set.yaml` and the seeded
  document titles via the API (`/v1/changes?limit=100 | jq
  '.items[].document_title'`). Patterns to scan for are the
  operator's responsibility; the source-tree convention is "if a
  reviewer needs a list, the list itself belongs out-of-band".
- **Curated-set sanity vs. the demo memory.** Confirm the
  jurisdictions and sectors in `data/curated_set.yaml` match the
  generic-only framing in
  [`.claude/memory/project_horizons_demo_2026_06_08.md`](../../.claude/memory/project_horizons_demo_2026_06_08.md):
  public legal sources only, placeholder banks ("ExampleBank", "a
  Tier-1 international bank in London"), no internal commercial
  detail.
- **Named-entity deflection.** If a viewer asks about a specific
  company's regulatory situation — e.g. "would this have caught the
  recent thing at \<bank X\>?" — deflect to the generic capability:
  *"The system tracks N jurisdictions × M sectors at the clause
  level; for any document in scope, we'd surface the diff and the
  alignment confidence. We are not commenting on specific
  institutions in this showcase."* Do not engage with named-entity
  questions on stage. The deflection script is short on purpose; a
  long answer is harder to keep generic.
- **Claim discipline.** Lawstronaut refreshes near-weekly per
  source, not real-time. Phrase delivery as "near-weekly regulatory
  change detection" — never "real-time alerts" — even when asked
  leading questions. The demo memory anchors this; do not improvise.
- **Closing the window.** When the public exposure period ends:
  1. Rotate the three demo-account passwords (re-run the script
     with new env-vars; the no-downgrade guard from Session P will
     reject a bake-in fallback if the env-vars are forgotten — that
     refusal is correct, set the env-vars and retry).
  2. Take the Front Door endpoint offline if no further public
     access is wanted — `az afd endpoint update --enabled-state
     Disabled`. Re-enable for any later showcase.
