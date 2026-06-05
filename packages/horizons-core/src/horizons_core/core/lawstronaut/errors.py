"""Exception taxonomy for the Lawstronaut client.

The hierarchy is intentionally shallow:

- :class:`LawstronautError` — everything inherits this.
- :class:`LawstronautAuthError` — 401 / 403. Terminal. Credential rotation
  needed; retrying the same bearer cannot help.
- :class:`LawstronautClientError` — other 4xx (400, 404, 422, …). Terminal.
  Most likely a bug in the request shape; retrying cannot help either.
- :class:`LawstronautTransientError` — 5xx, 429, network timeouts. Retried
  by ``stamina`` inside the client; escapes only if attempts are
  exhausted.

WU3.4's poll body catches :class:`LawstronautError` and treats it as a
poll failure that bumps the schedule's ``failure_count``. Anything that
escapes is unexpected and surfaces as an `ingestion_incident` with
``error_class != 'parked'``.
"""

from __future__ import annotations


class LawstronautError(Exception):
    """Base class for all Lawstronaut-client errors."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LawstronautAuthError(LawstronautError):
    """401 or 403 from either the auth host or the API host."""


class LawstronautClientError(LawstronautError):
    """Permanent 4xx other than 401 / 403."""


class LawstronautTransientError(LawstronautError):
    """5xx, 429, or a network-level timeout. Retried by stamina."""
