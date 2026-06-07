# 2026-06-05 — WU5.1: Runtime `/config.json`

*Last revised: 2026-06-05.*
*Path: journal/260605-wu51-runtime-config.md.*

Track-5 follow-on to WU5.0. Worktree `wu5.1-5.2-runtime-config-and-watchlists`
(landed together with WU5.2). Builds the boot step that fetches a runtime
config from `/config.json` before Vue mounts, then displaces the
hardcoded API base URL and the hardcoded confidence thresholds with reads
from that config. One bundle now deploys to every environment;
per-environment differences live entirely in the deployed `config.json`.

## What shipped

### Schema + runtime singleton

- `src/config/schema.ts` — Zod 4 schema. The shape:
  ```ts
  {
    apiBaseUrl: z.url(),
    tuningThresholds: {
      alignmentConfidence: {
        suppressBelow: number ∈ [0, 1],
        amberMin:      number ∈ [0, 1],
        greenMin:      number ∈ [0, 1],
      },
    },
    featureFlags: Record<string, boolean>,
  }
  ```
  `z.url()` (Zod 4) rejects non-URL strings; the `probability` helper
  pins thresholds to `[0, 1]`. Invalid shapes fail validation with a
  composed message that names each offending path.
- `src/config/runtime.ts` — module-level singleton + accessors.
  `getRuntimeConfig()` throws "has not been loaded" when called before
  bootstrap (fail-loud, not a silent default). `fetchAndValidateConfig()`
  fetches `/config.json` with `cache: 'no-store'`, parses, and runs
  `safeParse`. Every error path includes the URL in the message so the
  error screen's reason line points at the right artifact in a deployed
  env.

### Pinia store

- `src/stores/config.ts` exposes `useConfigStore()` with computed
  `apiBaseUrl`, `tuningThresholds`, and `featureFlags`. Each getter reads
  through `getRuntimeConfig()`, so reads before bootstrap throw the same
  helpful error as any other consumer.

### Bootstrap step

- `src/bootstrap.ts` is now the only `main.ts` entry point. The sequence:
  ```
  fetch /config.json
    ↓ schema.safeParse
  setRuntimeConfig(...)
  configureApiClient(apiBaseUrl)
  createApp(App).use(pinia).use(router).use(VueQueryPlugin).mount('#app')
  ```
  Done as a single `await` chain in `main.ts` — no "loading" Vue app
  that swaps to the real one, no spinner. The straightforward
  fetch-then-mount is exactly what the user asked for. If the fetch
  rejects or the parse fails, bootstrap rebuilds `#app` to render an
  error screen (via DOM APIs + `textContent`, never `innerHTML` of
  interpolated strings) and **does not call `app.mount()`**.
- The error screen displays the failure reason inline and points the
  reader at "/config.json missing from the deployment or its shape does
  not match the expected schema". This is intentional: silently falling
  back to dev defaults in a deployed environment hides config drift
  until a customer notices their dashboard is calling `localhost:8000`.

### Consumer migrations (`TODO(WU5.1)` round-up)

Grep was the source of truth for which call sites had to flip. The two
markers from WU5.0/WU5.3:

```
packages/horizons-webapp/src/api/client.ts:20:// TODO(WU5.1): replace with runtime /config.json lookup
packages/horizons-webapp/src/constants/confidence.ts:1:// TODO(WU5.1): replace with runtime /config.json `tuningThresholds`
```

Both were addressed:

1. **`src/api/client.ts`** — drop the `API_BASE_URL` constant. The
   axios instance is created without a `baseURL`; the new exported
   `configureApiClient(baseUrl)` is called once from bootstrap after
   the config validates. No fallback if it's never called — the first
   request fails against the page origin (`ECONNREFUSED` in dev,
   relative path 404 in deploy), which is the intentional fail-loud
   signal.
2. **`src/constants/confidence.ts`** — `confidenceTier(value)` and the
   new `suppressBelowThreshold()` both call `getRuntimeConfig()` at
   read time. Removes both hardcoded constants (`0.85`, `0.6`). Builds
   no longer bake the values into JS: `grep -E "0\.85|0\.6"` on
   `dist/assets/*.js` finds zero hits in the WU5.1 build.
3. **`src/views/ChangesView.vue`** — the "show below-threshold"
   toggle now filters on `< suppressBelow` rather than
   `confidenceTier === 'low'`. Same observable behaviour at the dev
   defaults (`suppressBelow == amberMin == 0.6`), but the role of each
   knob is now explicit: `suppressBelow` controls the list filter,
   `amberMin` / `greenMin` control the badge colour bands.

Verified post-migration that grep for `TODO(WU5.1)` returns nothing
in the webapp tree.

### Dev `public/config.json`

Generic placeholder values, committed:

```json
{
  "apiBaseUrl": "http://localhost:8000",
  "tuningThresholds": {
    "alignmentConfidence": {
      "suppressBelow": 0.6,
      "amberMin": 0.6,
      "greenMin": 0.85
    }
  },
  "featureFlags": {}
}
```

Vite copies `public/` verbatim into `dist/` so `dist/config.json` ships
alongside `dist/index.html`. The deploy workflow then overwrites it
per-environment (see "Follow-up wire-up" below).

### Tests

20 new vitest cases across four files:

- `src/config/__tests__/schema.spec.ts` (8) — accepts the dev config;
  rejects non-URL `apiBaseUrl`, missing keys, out-of-range thresholds,
  non-boolean feature-flag values.
- `src/config/__tests__/runtime.spec.ts` (8) — `fetchAndValidateConfig`
  succeeds on 200+valid, throws on HTTP 5xx, network error, non-JSON
  body, schema mismatch; error messages name `/config.json`; the
  singleton throws before set and round-trips after.
- `src/__tests__/bootstrap.spec.ts` (5) — happy-path mount sets the
  apiClient baseURL and removes nothing-mounted state; HTTP failure
  renders the fail-loud error screen with the reason; schema mismatch
  same; **nothing renders until the config fetch resolves** (delayed
  msw handler — assert empty `#app` while pending, populated after
  `await`); missing root element returns failed.
- `src/stores/__tests__/config.spec.ts` (2) — Pinia store reflects
  runtime config; reading before set throws the helpful error.

### Test setup change

`src/test/setup.ts` now installs `DEFAULT_TEST_CONFIG` and calls
`configureApiClient('http://localhost:8000')` in `beforeEach`, then
`clearRuntimeConfig()` in `afterEach`. This keeps every pre-existing
spec running unchanged — the config-failure tests deliberately clear
the singleton in their own setup to exercise the un-configured path.

## Follow-up wire-up

The deploy pipeline must overwrite `public/config.json` with
environment-specific values *before* `npm run build` so the staging
bundle ships staging URLs. Apply the following step in
`.github/workflows/deploy.yml` inside the `deploy-spa` job, between
`Install webapp dependencies` and `Build SPA`:

```yaml
      - name: Generate runtime config.json for ${{ inputs.environment }}
        working-directory: packages/horizons-webapp
        env:
          API_BASE_URL: ${{ vars.API_BASE_URL }}
          SUPPRESS_BELOW: ${{ vars.CONFIDENCE_SUPPRESS_BELOW }}
          AMBER_MIN: ${{ vars.CONFIDENCE_AMBER_MIN }}
          GREEN_MIN: ${{ vars.CONFIDENCE_GREEN_MIN }}
        run: |
          cat > public/config.json <<EOF
          {
            "apiBaseUrl": "${API_BASE_URL}",
            "tuningThresholds": {
              "alignmentConfidence": {
                "suppressBelow": ${SUPPRESS_BELOW:-0.6},
                "amberMin": ${AMBER_MIN:-0.6},
                "greenMin": ${GREEN_MIN:-0.85}
              }
            },
            "featureFlags": {}
          }
          EOF
          cat public/config.json
```

The `vars` (not `secrets`) live on the GitHub environment (`staging`,
`production`). The defaults preserve the locked-in 0.6 / 0.85 numbers
if a variable hasn't been set yet — safe because suppression-on stays
the demo default. Add `API_BASE_URL` to the staging environment as a
prerequisite (the value is the public Front Door hostname for the API).

After this step, the existing `Build SPA` step bundles the
freshly-generated `public/config.json` into `dist/config.json`. The
existing `Upload SPA to $web` step uploads it. The existing Front-Door
purge already explicitly purges `/config.json` (per WU6.3), so the
deploy round-trip is closed.

**Not applied in this session** — flagged for a follow-up so this
worktree stays scoped to packages/horizons-webapp and journal/.

## Decisions worth keeping

1. **No fallback on config failure.** Silent fallback to dev defaults
   in production is exactly the kind of bug nobody notices until a
   customer reports it. Failing loud at boot makes config drift
   immediately visible to the operator.
2. **Singleton + thin Pinia wrapper, not a Pinia-managed fetch.** The
   apiClient must read `apiBaseUrl` before Pinia exists (it's
   constructed at module load). A module-level singleton with
   `getRuntimeConfig()` is the simplest seam; the Pinia store is a
   reactive view onto it for components that prefer the store API.
3. **One `await` in `main.ts`, not a "loading" Vue app that swaps.**
   Per the prompt's explicit instruction: the straightforward
   fetch-then-mount is the right shape.
4. **The threshold consumer migration grep-matches the `TODO(WU5.1)`
   markers WU5.0 and WU5.3 left behind.** Verified post-flip that no
   `TODO(WU5.1)` markers remain in the webapp tree.
5. **`z.url()`** (Zod 4) rather than `z.string().url()`. Zod 4
   deprecated the chained `.url()` in favour of the standalone form.

## New shadcn-style UI components

None added in WU5.1 itself. (WU5.2 lands Dialog / Table / Toast.)

## Verification gate

```bash
# From the worktree:
uv run ruff check .                          # All checks passed!
uv run pyright                               # 0 errors, 25 warnings (pre-existing)
uv run pytest -m "not integration" -q        # 323 passed, 4 skipped (pre-existing)
uv run pre-commit run --all-files            # every hook Passed

cd packages/horizons-webapp
npm run lint:check                           # oxlint + eslint clean
npm run test:unit -- --run                   # 134 passed across 17 files
npm run build                                # vue-tsc + vite, 0 TS errors
```

Build emits the pre-existing `INVALID_ANNOTATION` warnings from
`@vueuse/core/dist/index.js` (upstream noise — see WU5.0 journal).

**Leak check** (the user-instructed grep):

```bash
grep -rln "localhost:8000" dist/        # → dist/config.json (only)
grep -rln "0\.85\|0\.6" dist/assets/*.js # → no hits
```

## Manual verification (for the user)

In-session tests cover the unit surface. The end-to-end check:

1. Start the API on `localhost:8000` (`uv run uvicorn horizons_api.app:app
   --reload --port 8000`).
2. Start the webapp: `cd packages/horizons-webapp && npm run dev`.
3. Open `http://localhost:5173/`. DevTools → Network: confirm a 200 on
   `GET /config.json` *before* any `/v1/*` call. Confirm the
   subsequent API calls use `http://localhost:8000` as the base URL.
4. **Simulate a config failure**: temporarily move `public/config.json`
   aside, refresh. Expected: the error screen appears with the reason
   "fetch /config.json returned HTTP 404"; no `/v1/me`, `/v1/discovery`,
   etc. requests fire.
5. **Simulate a schema mismatch**: edit `public/config.json` to
   `{"apiBaseUrl": "not-a-url"}`, refresh. Expected: error screen reads
   "schema validation: apiBaseUrl: Invalid URL; …".
6. **Confirm thresholds are data-driven**: edit `public/config.json`
   to set `greenMin: 0.5`; refresh `/changes`; confirm a 0.7-confidence
   row now renders with the green badge. Restore the default afterwards.

## Track 5 status (after WU5.1)

| WU | Status |
| --- | --- |
| WU5.0 | shipped |
| **WU5.1** | **shipped (runtime /config.json + bootstrap + threshold migration)** |
| WU5.2 | shipped this session — see `260605-wu52-watchlists-view.md` |
| WU5.3 | shipped |
| WU5.4 | next — admin views (depends on WU4.5, WU5.2) |
| WU5.5 | shipped |
| WU5.6 | shipped |
