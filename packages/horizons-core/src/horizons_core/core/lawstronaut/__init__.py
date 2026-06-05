"""Lawstronaut v2 HTTP client.

See ``client.md`` for the design overview. The public surface re-exports
the client, its credentials object, the response DTOs, and the error
taxonomy so callers (WU3.4's poll body, future admin tooling) need only
``from horizons_core.core.lawstronaut import LawstronautClient``.
"""

from __future__ import annotations

from horizons_core.core.lawstronaut.client import LawstronautClient
from horizons_core.core.lawstronaut.errors import (
    LawstronautAuthError,
    LawstronautClientError,
    LawstronautError,
    LawstronautTransientError,
)
from horizons_core.core.lawstronaut.models import (
    Credentials,
    Jurisdiction,
    MarkdownDocument,
    Portal,
)

__all__ = [
    "Credentials",
    "Jurisdiction",
    "LawstronautAuthError",
    "LawstronautClient",
    "LawstronautClientError",
    "LawstronautError",
    "LawstronautTransientError",
    "MarkdownDocument",
    "Portal",
]
