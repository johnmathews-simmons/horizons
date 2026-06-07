# WU6.2 — Dockerfiles + GHCR build-and-push workflow

*Last revised: 2026-06-05.*
*Path: journal/260605-wu62-dockerfiles-and-ghcr.md.*

*Session 2026-06-05. Same branch as WU6.0 (`worktree-wu6.0-6.2-infra-and-build`).*

Multi-stage uv Dockerfiles for the public API and the ingestion worker,
plus the `build-and-push.yml` workflow that ships them to GHCR. Both
images build clean against the worktree; the first **GHCR push** is the
external verification that closes the unit, and only fires once this
branch ff-merges to main.

## What shipped

```
.dockerignore                              Repo-wide build-context trim.
packages/horizons-api/Dockerfile           Multi-stage; python:3.13-slim
                                           → /opt/venv → uvicorn
                                           horizons_api.app:app on 8000.
packages/horizons-ingestion/Dockerfile     Multi-stage; same builder
                                           shape → `python -m
                                           horizons_ingestion` on
                                           /healthz:8080.
.github/workflows/build-and-push.yml       Matrix job: api + worker;
                                           push to main + manual
                                           dispatch; tags :sha-<short>
                                           + :latest; GHCR via
                                           GITHUB_TOKEN; GHA layer
                                           cache scoped per image.
```

## How the per-image install works

uv workspace packaging makes "install only horizons-api's deps" precise:
`uv sync --package horizons-api` walks the workspace graph rooted at
that package and installs just its dependency closure, including
`horizons-core` (its workspace-graph dependency) but **not**
`horizons-ingestion`. Same logic in reverse for the worker image. The
Dockerfile pattern is:

```dockerfile
# Stage 1: deps without the workspace members themselves (third-party
# only — best cache layer).
COPY pyproject.toml uv.lock ./
COPY packages/horizons-core/pyproject.toml packages/horizons-core/pyproject.toml
COPY packages/horizons-api/pyproject.toml packages/horizons-api/pyproject.toml
COPY packages/horizons-ingestion/pyproject.toml packages/horizons-ingestion/pyproject.toml
RUN uv sync --package horizons-api --no-dev --frozen --no-install-project

# Stage 2: source for the two packages this image needs; rebuild the
# venv to add the workspace members themselves.
COPY packages/horizons-core/src packages/horizons-core/src
COPY packages/horizons-api/src packages/horizons-api/src
RUN uv sync --package horizons-api --no-dev --frozen --no-editable

# Runtime: just the venv. --no-editable means the wheels are baked into
# /opt/venv and source isn't needed at runtime.
```

The four-pyproject COPY pattern is necessary because uv resolves the
workspace graph from all member `pyproject.toml`s, not just the leaf
package. Omitting any member's pyproject (even one we don't install)
makes uv refuse the sync with a "workspace member not found" error.

## Decisions inline that warrant a paper trail

- **uv binary via `FROM ghcr.io/astral-sh/uv:0.9 AS uv-bin` + `COPY
  --from=uv-bin`.** The naive `COPY --from=ghcr.io/astral-sh/uv:${UV_VERSION}`
  fails — Dockerfile's `--from` doesn't interpolate ARG values; the
  named-stage indirection is the standard workaround.
- **`--no-editable` install at the worker step.** Default for uv
  workspaces is editable, which would require the source tree at
  runtime. `--no-editable` bakes a real wheel into the venv so the
  runtime stage doesn't need `/app/packages/` at all — keeps the image
  small and prevents accidental source-tree drift between layers.
- **Non-root runtime user.** `horizons` uid 1001 / gid 1001 in both
  images. ACA doesn't mandate non-root but defence-in-depth makes it a
  cheap habit.
- **Image labels via `docker/metadata-action`.** OpenContainers source,
  revision, title, license. `org.opencontainers.image.licenses=Proprietary`
  matches CLAUDE.md's closed-source posture and the deferred LICENSE
  decision.
- **GHA layer cache scoped per matrix entry (`scope=${{ matrix.image }}`).**
  After the `uv sync --package …` step the two images' layer trees
  diverge; sharing a single cache scope causes thrashing. Per-image
  scope keeps each cache narrow and effective.

## Verification

Local docker builds:

```bash
docker build -f packages/horizons-api/Dockerfile -t horizons-api:local .
# → exit 0; final image sha256:78401e42…
docker build -f packages/horizons-ingestion/Dockerfile -t horizons-worker:local .
# → exit 0; final image sha256:ed81bbc2…
```

Workflow YAML syntax: `python3 -c "import yaml; yaml.safe_load(open(...))"` → OK.
No `actionlint` available locally; surface as a follow-up if the GHA
schema check matters more than the YAML parse check.

Python sweep (no Python touched but the gate is the gate):

- `ruff check .` — All checks passed
- `pyright` — 0 errors, 15 pre-existing warnings (`reportMissingTypeStubs` for testcontainers.postgres)
- `pytest -m "not integration"` — 232 passed, 4 skipped, 102 deselected
- `pre-commit run --all-files` — every hook Passed

## External verification (user-only — must be flagged)

The first successful run of `.github/workflows/build-and-push.yml`
against `main` is what verifies WU6.2. It needs:

1. The branch ff-merged into `main` (so the workflow's `push: branches:
   [main]` trigger fires).
2. The repo's GHCR namespace to accept the first push. GitHub creates
   the package container automatically on first push, but the package
   visibility (public vs private) defaults to private. For the demo's
   public images, after the first push the user must:
   - Visit `https://github.com/users/johnmathews/packages/container/horizons-api/settings`
   - Visit `https://github.com/users/johnmathews/packages/container/horizons-worker/settings`
   - Flip both to "Public" so the ACA pull works without registry credentials.
3. The `packages: write` permission on `GITHUB_TOKEN` for the
   workflow. The workflow YAML declares it; no user action needed
   unless org policy overrides repo defaults.

What to look for in the first run:

- The matrix's `build (horizons-api)` and `build (horizons-worker)` jobs
  both turn green.
- Two new packages appear under
  `https://github.com/johnmathews?tab=packages` with the `:sha-<short>`
  and `:latest` tags.
- Subsequent main pushes update `:latest` and add fresh `:sha-<short>`
  tags; cache hits visible in the build logs cut steady-state CI to
  about a minute per image.

If the first push fails with `denied: installation not allowed to
Create organization package`, the namespace owner's package-creation
policy is blocking the workflow's auto-create; the user needs to
either pre-create the empty package or relax the policy.

## Decisions deliberately deferred

- **SBOM generation (`anchore/sbom-action` or `docker/build-push-action`'s
  `sbom: true`).** The plan calls it "optional but documented." Not
  added yet — a follow-up that adds a sub-minute step is cheaper than
  expanding the workflow now without a concrete consumer.
- **Image signing (cosign / `sigstore`).** Not needed for the demo.
  The `id-token: write` permission is already declared so adding a
  signing step later is a workflow-only change with no permission
  re-grant.
- **Multi-arch (linux/arm64).** Single-arch (linux/amd64, GHA default)
  is fine for ACA Consumption. If the user ever runs the images
  locally on Apple Silicon, that's via Docker Desktop's emulation;
  switching the workflow to `linux/amd64,linux/arm64` is a one-line
  change to `build-push-action`.
- **ACR migration (locked-in plan deferral §10).** GHCR for the demo;
  ACR post-demo if managed-identity pulls become friction.

## Next pickup

With WU6.0 + WU6.2 landed, the Track 6 critical path waits on:

- **WU6.1 — OIDC federation** (user-only Azure-side setup).
- **WU6.3 — `deploy.yml`** (depends on WU6.1, WU6.2, WU6.0, and WU4.4
  shipping endpoints worth deploying).
- **WU6.4 — migration ACA Job** (depends on WU3.1 schema + WU6.0 infra).

This session does **not** pick any of those up. Stop after WU6.2.
