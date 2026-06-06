# syntax=docker/dockerfile:1.7
#
# Horizons — ingestion worker container image.
#
# Multi-stage uv build targeting python:3.13-slim. Installs only the
# horizons-ingestion package and its workspace-graph dependency
# horizons-core. ADR-0001 picked the long-running asyncio shape over an
# ACA Job; the CMD reflects that — `python -m horizons_ingestion` keeps
# the container alive, exits on SIGTERM after draining in-flight claims.
#
# Build context: the repository root. Build with:
#
#   docker build -f packages/horizons-ingestion/Dockerfile -t horizons-worker:local .

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.9

# Named stage for the uv binary — see the API Dockerfile for the same
# pattern; --from cannot interpolate ARGs inline.
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv-bin

# ---------- builder ----------
FROM python:${PYTHON_VERSION}-slim AS builder

COPY --from=uv-bin /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# 1. Workspace metadata for graph resolution.
COPY pyproject.toml uv.lock ./
COPY packages/horizons-core/pyproject.toml packages/horizons-core/pyproject.toml
COPY packages/horizons-api/pyproject.toml packages/horizons-api/pyproject.toml
COPY packages/horizons-ingestion/pyproject.toml packages/horizons-ingestion/pyproject.toml

# 2. Third-party deps for the worker.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package horizons-ingestion --no-dev --frozen --no-install-project

# 3. Source for the two packages the worker needs. Skip horizons-api.
COPY packages/horizons-core/src packages/horizons-core/src
COPY packages/horizons-ingestion/src packages/horizons-ingestion/src

# 4. Non-editable workspace install.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package horizons-ingestion --no-dev --frozen --no-editable

# ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

RUN groupadd --system --gid 1001 horizons \
 && useradd --system --uid 1001 --gid horizons --home-dir /app --shell /sbin/nologin horizons

COPY --from=builder /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HORIZONS_WORKER_HEALTH_PORT=8080

WORKDIR /app

# Reseed substrate. The operator-driven wipe+reseed path (run from a
# laptop via ``az containerapp exec``) reads the curated set, the fixture
# inventory, and the synthetic v2 markdown from disk, then re-provisions
# the demo accounts. Baked into the worker image so the operator doesn't
# need a separate one-shot Job. ~1 MB on disk; the runtime never touches
# these paths unless the operator runs ``scripts/reseed_corpus.py``.
# COPY-as-root then chown to the runtime user — files end up owned by
# ``horizons:horizons`` so the unprivileged process can read them.
COPY --chown=horizons:horizons scripts/seed_curated_set.py ./scripts/seed_curated_set.py
COPY --chown=horizons:horizons scripts/reseed_corpus.py ./scripts/reseed_corpus.py
COPY --chown=horizons:horizons packages/horizons-api/scripts/create_demo_accounts.py ./scripts/create_demo_accounts.py
COPY --chown=horizons:horizons data/curated_set.yaml ./data/curated_set.yaml
COPY --chown=horizons:horizons data/samples ./data/samples

USER horizons

# /healthz on the aiohttp probe surface (per ADR-0001). The port is also
# fixed in container-app-worker.bicep — keep these two values in sync.
EXPOSE 8080

# ADR-0001 entry: `python -m horizons_ingestion`. ctrl-C friendly locally,
# SIGTERM-drains in ACA, /healthz stays up while the claim loop ticks.
CMD ["python", "-m", "horizons_ingestion"]
