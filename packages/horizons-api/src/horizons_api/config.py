"""Runtime configuration for the public API.

Read from environment variables on app start. Each setting maps to one
env var; there is no fallback file because the API is meant to be run
under a process supervisor (uvicorn in a container) that already
hydrates the environment.

The JWT keys are PEM bytes — production reads them from Azure Key Vault
via the IaC layer and injects them as env vars; local dev exports them
from ``.env`` (gitignored). The keys are deliberately required: an
``HORIZONS_JWT_PRIVATE_KEY_PEM=`` empty value is not allowed because
silent fallback to a weak default is a worse failure mode than a loud
startup error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

_ENV_PRIVATE_KEY: Final[str] = "HORIZONS_JWT_PRIVATE_KEY_PEM"
_ENV_PUBLIC_KEY: Final[str] = "HORIZONS_JWT_PUBLIC_KEY_PEM"
_ENV_ISSUER: Final[str] = "HORIZONS_JWT_ISSUER"
_ENV_AUDIENCE: Final[str] = "HORIZONS_JWT_AUDIENCE"
_ENV_CORS_ORIGINS: Final[str] = "HORIZONS_CORS_ORIGINS"


@dataclass(frozen=True, slots=True)
class ApiSettings:
    """Parsed view of the API's runtime environment."""

    jwt_private_key_pem: bytes
    jwt_public_key_pem: bytes
    jwt_issuer: str
    jwt_audience: str
    cors_origins: tuple[str, ...]


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable {name!r} is missing or empty")
    return value


def load_settings() -> ApiSettings:
    """Parse the process environment into an ``ApiSettings``.

    Called once at app construction. Re-importing or re-calling is fine
    but pointless — settings are static for a process's lifetime.
    """
    cors_raw = os.environ.get(_ENV_CORS_ORIGINS, "")
    cors_origins = tuple(o.strip() for o in cors_raw.split(",") if o.strip())
    return ApiSettings(
        jwt_private_key_pem=_require_env(_ENV_PRIVATE_KEY).encode("utf-8"),
        jwt_public_key_pem=_require_env(_ENV_PUBLIC_KEY).encode("utf-8"),
        jwt_issuer=_require_env(_ENV_ISSUER),
        jwt_audience=_require_env(_ENV_AUDIENCE),
        cors_origins=cors_origins,
    )
