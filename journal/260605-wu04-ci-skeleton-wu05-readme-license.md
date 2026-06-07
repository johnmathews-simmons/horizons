# 2026-06-05 — WU0.4 CI skeleton + WU0.5 README pointer + LICENSE decision

*Last revised: 2026-06-05.*
*Path: journal/260605-wu04-ci-skeleton-wu05-readme-license.md.*

Two scaffolding work units landed back-to-back via the engineering-team
flow. One commit each, each on its own worktree, fast-forwarded into
`main` and pushed.

## WU0.4 — CI workflows

Two GitHub Actions workflows, both triggered on `pull_request`, push to
`main`, and `workflow_dispatch`. Concurrency groups cancel in-progress
runs on PR pushes but let `main` runs complete (so status-check signals
stay deterministic for branch protection).

- `.github/workflows/ci.yml` — Python lane.
  - `astral-sh/setup-uv@v6` pinned to `0.9.27` (matches the dev machine).
  - `uv sync --frozen` so CI fails fast if `uv.lock` drifts from
    `pyproject.toml`.
  - `uv run ruff check .`
  - `uv run pyright` (strict, `reportMissingTypeStubs = "warning"`).
  - `uv run pre-commit run --all-files --show-diff-on-failure` — catches
    contributors who skipped `pre-commit install` locally.
  - `uv run pytest --cov --cov-report=xml --cov-report=term`, *including*
    the testcontainers-backed integration test (chose to pay the
    ~15–30s cold-start cost upfront rather than defer to a nightly,
    on the grounds that substrate regressions are exactly what
    integration tests are supposed to catch).
  - Uploads `coverage.xml` as a workflow artifact (no Codecov yet).

- `.github/workflows/webapp.yml` — Webapp lane.
  - `actions/setup-node@v4` with `node-version: "22"` and `cache: npm`
    keyed on `packages/horizons-webapp/package-lock.json`.
  - `npm ci` (deterministic install from the lockfile).
  - `npm run lint` (oxlint then eslint).
  - `npm run build` (vue-tsc + vite).
  - `npm run test:unit -- --run` (vitest, single-shot).
  - Uses `defaults.run.working-directory` so every step is scoped to
    the webapp without per-step `--prefix` plumbing.

### Decisions taken vs. left

Explicitly chose **two separate workflow files** (Q1=b) so the Python
and webapp lanes get independent badges, triggers, and status-check
names — the branch-protection rule has to require both
`Python CI / lint, typecheck, test` and `Webapp CI / lint, build, test`.

Known gap, deferred: `packages/horizons-webapp/package.json` defines
`lint:oxlint` and `lint:eslint` with `--fix`. In CI, auto-fixable
issues would be silently fixed and the lint step would pass — masking
the regression. The fix is a one-line addition of `lint:check` scripts
(no `--fix`); leaving for a later WU.

Known follow-up that the workflow can't do itself: branch protection on
`main` needs to be configured via the GitHub UI / API to require both
status checks before merge. Noted in the commit body so it doesn't get
lost.

## WU0.5 — README rewrite + LICENSE decision

Rewrote the root `README.md` from the initial-commit stub to a short
anchor-style README:

- One paragraph framing Horizons generically — no firm/client/bank
  names — since the repo goes briefly public during the demo
  (~2026-06-08, 1–2 days).
- Numbered pointers to docs 0–4 in order.
- Links to `data/samples/README.md` and `docs/api/README.md`.
- One sentence on layout (uv workspace + Vue webapp not-a-member).
- Points at the `Commands` section of `CLAUDE.md` for dev workflow
  rather than duplicating it.

Stale facts cleared from the old README: "No Python package, tests, or
CI yet" is no longer true after WU0.1–WU0.4.

### Licensing

Closed-source, all rights reserved. No `LICENSE` file. The demo period
does not confer any license to use, modify, or redistribute — viewers
can read but acquire no rights. A formal license decision is deferred
until after the demo. Recorded in a new `## Licensing` section near
the top of `CLAUDE.md` (between "What this repo is" and "Read first")
and summarised at the bottom of the README. Default copyright applies,
which is the most restrictive default and the right posture for a
pre-launch commercial codebase.

Also updated the "early scaffolding" paragraph in `CLAUDE.md` to note
that CI now runs the Python sweep and the webapp build on every PR.

## Local sweep at session end (all green on `main`)

    uv run pytest --cov                # 5 passed
    uv run ruff check .                # All checks passed
    uv run pyright                     # 0 errors, 1 warning (testcontainers stub)
    uv run pre-commit run --all-files  # all hooks passed
    cd packages/horizons-webapp
    npm run build                      # vue-tsc + vite OK
    npm run test:unit -- --run         # 3 passed

## Next session

1. Configure branch protection on `main` to require both CI status
   checks (one-shot GitHub UI / API action — not automatable from the
   workflow itself).
2. Decide whether to close the `npm run lint --fix` CI gap now or roll
   it into a later WU.
3. Move on to Track 1 (substantive code work — clause parser, DB
   schema, etc.). Stay generic in all copy.
