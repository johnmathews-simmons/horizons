# syntax=docker/dockerfile:1.7
#
# Horizons — public REST API container image.
#
# Multi-stage uv build targeting python:3.13-slim. Installs only the
# horizons-api package and its workspace-graph dependency horizons-core;
# horizons-ingestion is deliberately NOT installed (it belongs in the
# worker image). docs/4. services.md requires API and worker to ship as
# separate images so an ingestion burst can't starve the API of CPU.
#
# Build context: the repository root. Build with:
#
#   docker build -f packages/horizons-api/Dockerfile -t horizons-api:local .

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.9

# Named stage for the uv binary — Dockerfile spec requires --from to be a
# literal stage name or a literal image ref, so we alias the image once
# here and COPY from the alias below.
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv-bin

# ---------- builder ----------
FROM python:${PYTHON_VERSION}-slim AS builder

# Pull the uv binary from the official image — avoids a `pip install uv`
# round-trip and gets us a known-good version.
COPY --from=uv-bin /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# 1. Workspace metadata first — the lockfile + every member's pyproject.toml
#    must be present so uv can resolve the workspace graph. Copying these
#    before the source maximises layer-cache hits when only source changes.
COPY pyproject.toml uv.lock ./
COPY packages/horizons-core/pyproject.toml packages/horizons-core/pyproject.toml
COPY packages/horizons-api/pyproject.toml packages/horizons-api/pyproject.toml
COPY packages/horizons-ingestion/pyproject.toml packages/horizons-ingestion/pyproject.toml

# 2. Install third-party deps only — workspace members come next once
#    their source is on disk. --no-install-project skips the members at
#    this stage.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package horizons-api --no-dev --frozen --no-install-project

# 3. Copy source for the two packages the API needs. Skip horizons-ingestion
#    source; it would only inflate the image.
COPY packages/horizons-core/src packages/horizons-core/src
COPY packages/horizons-api/src packages/horizons-api/src

# 4. Build + install the workspace members non-editably. The resulting venv
#    is self-contained — runtime doesn't need /app/packages at all.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package horizons-api --no-dev --frozen --no-editable

# ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

# Non-root runtime user. uid/gid 1001 is the convention for first
# unreserved system user on Debian-derived images.
RUN groupadd --system --gid 1001 horizons \
 && useradd --system --uid 1001 --gid horizons --home-dir /app --shell /sbin/nologin horizons

# Tini-equivalent process-group cleanup: rely on python's signal handling
# rather than adding a dependency. uvicorn handles SIGTERM cleanly.

COPY --from=builder /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app
USER horizons

EXPOSE 8000

# WU4.1 contract: horizons-api exposes the FastAPI instance as
# `horizons_api.app:app`. Until WU4.1 lands, the symbol doesn't exist and
# the container will exit at startup — that's a feature, not a bug
# (build-and-push verifies the IMAGE is well-formed; runtime contract is
# WU4.1's gate).
CMD ["uvicorn", "horizons_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
