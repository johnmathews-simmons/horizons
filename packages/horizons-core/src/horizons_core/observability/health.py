"""Log Analytics query helpers for the admin health surface (WU7.2).

The ``/v1/admin/health/api`` endpoint reads three signals from the
Application Insights workspace tables:

- ``rate``      — ``count(AppRequests)`` over the window.
- ``error_rate`` — ``count(AppRequests where success == false) / count``.
- ``p95_ms``    — ``percentile(AppRequests.DurationMs, 95)``.

All three come from the same KQL projection so a window evaluation is
one round trip. Two windows are surfaced (``1h`` and ``24h``) — both go
through the same call path; the only difference is the ``timespan`` the
SDK ships in the request.

## Caching

Results are cached for 60 seconds per ``(window, query_id)`` key using a
process-wide :class:`cachetools.TTLCache`. The admin endpoint is
low-traffic and the underlying Log Analytics call is multi-second; a
60 s TTL turns "every admin page load fans out to Azure" into "one
fan-out per minute". We deliberately do **not** lock around the cache:
the worst race is two coroutines fanning out the same query on a cold
key, which is acceptable for the admin surface.

## Graceful degradation in local dev

If ``HORIZONS_LOG_ANALYTICS_WORKSPACE_ID`` is unset, or the Azure
credential / client raises at first use, the call returns
:class:`HealthQueryUnavailable` rather than 500-ing. Routes surface that
as ``{"data_source": "unavailable", "reason": "...", "values": null}``
so the admin UI can render a "Log Analytics unreachable" badge instead
of an opaque server error. CLAUDE.md flags this so the user knows what
to expect when hitting the API locally without Azure credentials wired.

## Test seam

Tests inject a fake client via :func:`set_logs_query_client_for_tests`.
The fake is any object with ``query_workspace(workspace_id, query,
timespan)`` returning a result whose ``tables[0].rows`` matches the
real SDK shape. :func:`reset_logs_query_client_for_tests` restores the
default lazy factory.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from cachetools import TTLCache

if TYPE_CHECKING:
    from collections.abc import Iterable


_LOGGER = logging.getLogger(__name__)

HealthWindow = Literal["1h", "24h"]

_WINDOW_TO_TIMESPAN: Final[dict[HealthWindow, timedelta]] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
}

# Single KQL projection for the three API metrics. ``success`` is a
# boolean string in App Insights (``"True"`` / ``"False"``); ``iff`` on
# the parsed value gives us the failure count without a second query.
_API_METRICS_KQL: Final[str] = (
    "AppRequests"
    " | summarize total = count(),"
    " failed = countif(Success == false),"
    " p95_ms = percentile(DurationMs, 95)"
)
_API_METRICS_QUERY_ID: Final[str] = "api_metrics"

_WORKSPACE_ENV_VAR: Final[str] = "HORIZONS_LOG_ANALYTICS_WORKSPACE_ID"
_CACHE_MAXSIZE: Final[int] = 16
_CACHE_TTL_SECONDS: Final[float] = 60.0


class HealthQueryUnavailable(Exception):
    """Raised when the Log Analytics workspace is unreachable.

    Carries a short, human-meaningful ``reason`` that the admin
    endpoint surfaces verbatim to the SPA. The reason is safe to expose
    — it never includes secrets or request bodies, only the failure
    category (e.g. ``"workspace id not configured"``,
    ``"credential init failed"``, ``"query failed"``).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ApiHealthMetrics:
    """Three numbers for one window, plus the window label.

    ``rate`` is requests-per-minute over the window, ``error_rate`` is
    the 0..1 ratio of failed-to-total, and ``p95_ms`` is the 95th
    percentile request duration in milliseconds. Any field can be
    ``None`` if the workspace returned no rows for the window (idle
    deployment, fresh environment).
    """

    window: HealthWindow
    rate_per_minute: float | None
    error_rate: float | None
    p95_ms: float | None


class _LogsQueryClientProtocol(Protocol):
    """Subset of :class:`azure.monitor.query.LogsQueryClient` we use."""

    def query_workspace(
        self,
        workspace_id: str,
        query: str,
        *,
        timespan: timedelta,
    ) -> Any: ...


_client_override: _LogsQueryClientProtocol | None = None
_default_client: _LogsQueryClientProtocol | None = None
_cache: TTLCache[tuple[str, HealthWindow], ApiHealthMetrics] = TTLCache[
    tuple[str, HealthWindow], ApiHealthMetrics
](maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL_SECONDS)


def set_logs_query_client_for_tests(client: _LogsQueryClientProtocol) -> None:
    """Install a stub client for the duration of a test.

    Bypasses the lazy default factory so tests don't need Azure
    credentials or the real SDK. Pair with
    :func:`reset_logs_query_client_for_tests` in a fixture teardown.
    Also clears the TTL cache so previously-cached real results don't
    bleed into a test run.
    """
    global _client_override  # noqa: PLW0603 — process-wide test seam
    _client_override = client
    _cache.clear()


def reset_logs_query_client_for_tests() -> None:
    """Restore the default lazy factory and clear the cache."""
    global _client_override, _default_client  # noqa: PLW0603
    _client_override = None
    _default_client = None
    _cache.clear()


def _get_client() -> _LogsQueryClientProtocol:
    """Return the active client; build the real one on first use.

    The Azure SDK imports are deferred to keep test runs that override
    the client free of an ``azure-identity`` credential lookup at
    import time.
    """
    global _default_client  # noqa: PLW0603
    if _client_override is not None:
        return _client_override
    if _default_client is not None:
        return _default_client

    try:
        from azure.identity import DefaultAzureCredential
        from azure.monitor.query import LogsQueryClient
    except ImportError as exc:
        raise HealthQueryUnavailable(f"azure sdk import failed: {exc}") from exc

    try:
        credential = DefaultAzureCredential()
        _default_client = LogsQueryClient(credential)
    except Exception as exc:  # noqa: BLE001 — any cred init issue is "unavailable"
        raise HealthQueryUnavailable(f"credential init failed: {exc}") from exc
    return _default_client


def _workspace_id_or_raise() -> str:
    workspace_id = os.environ.get(_WORKSPACE_ENV_VAR)
    if not workspace_id:
        raise HealthQueryUnavailable("workspace id not configured")
    return workspace_id


def _extract_row(result: Any) -> tuple[int | None, int | None, float | None]:
    """Pull ``(total, failed, p95_ms)`` from an SDK query result.

    The SDK exposes a ``tables`` list with the column / row shape KQL
    returned. Our query produces exactly one row with three columns;
    we coerce defensively because ``AppRequests`` can be empty for a
    window (returns 0 rows or 0/null values depending on Azure-side
    behaviour).
    """
    tables = getattr(result, "tables", None)
    if not tables:
        return None, None, None
    table = tables[0]
    rows: Iterable[Any] = getattr(table, "rows", []) or []
    rows_list = list(rows)
    if not rows_list:
        return None, None, None
    row = rows_list[0]
    total = int(row[0]) if row[0] is not None else None
    failed = int(row[1]) if row[1] is not None else None
    p95 = float(row[2]) if row[2] is not None else None
    return total, failed, p95


def fetch_api_metrics(window: HealthWindow) -> ApiHealthMetrics:
    """Return rate / p95 / error_rate for ``window``.

    Cached for 60 s per window. Raises
    :class:`HealthQueryUnavailable` if the workspace id is unset, the
    Azure SDK cannot import, the credential cannot initialise, or the
    query call raises — the caller surfaces that as the
    "unavailable" response shape.
    """
    cached = _cache.get((_API_METRICS_QUERY_ID, window))
    if cached is not None:
        return cached

    workspace_id = _workspace_id_or_raise()
    client = _get_client()
    timespan = _WINDOW_TO_TIMESPAN[window]

    try:
        result = client.query_workspace(
            workspace_id,
            _API_METRICS_KQL,
            timespan=timespan,
        )
    except HealthQueryUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — any SDK error is "unavailable"
        _LOGGER.warning("log analytics query failed", exc_info=exc)
        raise HealthQueryUnavailable(f"query failed: {type(exc).__name__}") from exc

    total, failed, p95_ms = _extract_row(result)

    window_minutes = timespan.total_seconds() / 60.0
    rate_per_minute: float | None = (
        total / window_minutes if total is not None and window_minutes else None
    )
    error_rate: float | None = None if total is None or total == 0 else (failed or 0) / total

    metrics = ApiHealthMetrics(
        window=window,
        rate_per_minute=rate_per_minute,
        error_rate=error_rate,
        p95_ms=p95_ms,
    )
    _cache[(_API_METRICS_QUERY_ID, window)] = metrics
    return metrics


__all__ = [
    "ApiHealthMetrics",
    "HealthQueryUnavailable",
    "HealthWindow",
    "fetch_api_metrics",
    "reset_logs_query_client_for_tests",
    "set_logs_query_client_for_tests",
]
