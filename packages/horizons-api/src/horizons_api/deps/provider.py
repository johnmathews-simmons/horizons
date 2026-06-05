"""Process-wide ``TokenProvider`` dependency.

A lazy module-level singleton: build the ``LocalJwtProvider`` once
from ``ApiSettings`` on first call. Tests substitute a different
provider via ``app.dependency_overrides[get_token_provider] = ...``
rather than monkey-patching the env.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from horizons_core.core.auth import LocalJwtProvider

from horizons_api.config import load_settings

if TYPE_CHECKING:
    from horizons_core.core.auth import TokenProvider


_provider: LocalJwtProvider | None = None


def get_token_provider() -> TokenProvider:
    """Return the process-wide ``TokenProvider``.

    First call constructs a ``LocalJwtProvider`` from environment-
    derived settings; subsequent calls return the same instance.
    """
    global _provider
    if _provider is None:
        settings = load_settings()
        _provider = LocalJwtProvider(
            private_key=settings.jwt_private_key_pem,
            public_key=settings.jwt_public_key_pem,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    return _provider


def reset_provider_for_tests() -> None:
    """Drop the cached provider so the next call rebuilds from env.

    Used only by integration tests that mutate the env between cases.
    Production code never calls this.
    """
    global _provider
    _provider = None
