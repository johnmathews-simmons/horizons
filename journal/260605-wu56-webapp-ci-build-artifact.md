# WU5.6 — Webapp CI build + lint

**Branch:** `wu5.6-webapp-ci-job`
**Plan ref:** WU5.6 — *Webapp CI build + lint. Acceptance: `.github/workflows/ci.yml` includes a webapp job that runs `pnpm install && pnpm lint && pnpm test && pnpm build`. Output goes to `webapp/dist/`.*

## Outcome

Mostly confirmation. The existing `.github/workflows/webapp.yml` already meets
the spirit of WU5.6 — every PR + push to main runs the full webapp sweep
(`npm ci`, `npm run lint:check`, `npm run build`, `npm run test:unit -- --run`)
on Ubuntu 24.04 / Node 22, with `actions/setup-node@v4`'s built-in npm cache
keyed on `packages/horizons-webapp/package-lock.json`. The single missing
piece was the dist artefact, which this work unit adds.

The plan's `pnpm` wording predates the npm decision in CLAUDE.md ("Commands"
section); npm is the actual choice. No code uses pnpm.

## What changed

`.github/workflows/webapp.yml` — added one step at the tail of the `webapp`
job:

```yaml
- name: Upload dist artifact
  uses: actions/upload-artifact@v4
  with:
    name: webapp-dist
    path: packages/horizons-webapp/dist
    if-no-files-found: error
    retention-days: 14
```

No other file touched.

## Final webapp job step list

1. `actions/checkout@v4`
2. `actions/setup-node@v4` (node-version: 22; `cache: npm`; cache-dependency-path: `packages/horizons-webapp/package-lock.json`)
3. `npm ci`
4. `npm run lint:check` (oxlint + eslint, no `--fix`)
5. `npm run build` (vue-tsc + vite — type-check is a build sub-step)
6. `npm run test:unit -- --run` (vitest)
7. `actions/upload-artifact@v4` → `webapp-dist` ← *new*

Working directory throughout: `packages/horizons-webapp` (set via `defaults.run.working-directory` at the job level).

## Cache configuration

Unchanged from prior state:

- `actions/setup-node@v4` with `cache: npm` and `cache-dependency-path: packages/horizons-webapp/package-lock.json`.
- Cache key derives from the lockfile hash; restore key falls back to the OS + Node version.
- Cache is on the GitHub-hosted runner cache, not `node_modules` directly — `npm ci` is still invoked, but it hits the local tarball cache instead of the registry.

## Notes on choices

- **Step order: build before test.** Kept as-is. `npm run build` invokes `run-p type-check build-only`, so it includes `vue-tsc --build`. Running it before vitest fails fast on type errors before the slower test step. The user-prompt's stated order (test then build) is acceptable to the WU acceptance criteria, but the existing arrangement is sensible; not reordered.
- **`if-no-files-found: error`** — `npm run build` is the gate; if a regression silently produces no `dist/`, the artefact upload now surfaces it as a CI failure rather than a missing artifact you only notice downstream.
- **`retention-days: 14`** — matches the Python coverage artefact in `ci.yml` for consistency.
- **No `if: always()`** — only upload on green test runs. Downstream consumers (`deploy.yml`, future SPA-deploy jobs) shouldn't pick up a build whose tests failed.

## Verification

- `uv run pre-commit run --all-files` — all green (`check yaml` includes the modified workflow).
- `actionlint` is not installed on this machine; relied on pre-commit's `check yaml` plus GitHub's own workflow validator (runs on push).

## What's next

WU5.6 acceptance: met. Stop after this unit per session brief. Downstream
consumers of `webapp-dist`:

- WU6.3 (`deploy.yml`) plans to upload `webapp/dist/` to the `$web` blob container and purge Front Door cache. Once that workflow exists, it can `actions/download-artifact@v4` `webapp-dist` from a triggering `Webapp CI` run instead of rebuilding.
