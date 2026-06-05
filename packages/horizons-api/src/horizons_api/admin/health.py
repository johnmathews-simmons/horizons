"""``/v1/admin/health/{api,ingestion,db}`` — operator health endpoints.

Three GET routes, all admin-only:

- ``/v1/admin/health/api`` — request rate, p95 latency, error rate over
  the last 1h and 24h. Sourced from Log Analytics (Application Insights
  workspace tables) via :mod:`horizons_core.observability.health` with a
  60 s in-process TTL cache.

- ``/v1/admin/health/ingestion`` — current poll backlog
  (``document_poll_schedule`` rows whose ``next_poll_at`` is in the
  past) plus the last 24h of ``ingestion_incident`` rows. Pure DB read
  via the ``admin_bypass`` session yielded by the WU4.5 admin dep.

- ``/v1/admin/health/db`` — connection count from ``pg_stat_activity``
  and (if ``pg_stat_statements`` is installed) the top 5 slow queries.
  Replication lag returns ``null`` for now — the demo deployment has
  no replica.

Local-dev graceful degradation: every external-system read is wrapped
so a missing credential / unavailable workspace / missing extension
produces a structured ``data_source: "unavailable"`` payload rather
than a 500. The admin SPA can render a "degraded" badge per source
instead of erroring out.

Raw-SQL discipline: ``sqlalchemy.text()`` is banned outside
``core/db/session.py`` by ``tests/test_raw_sql_isolation.py``. Every
query in this module is built from :func:`sqlalchemy.table` /
:func:`sqlalchemy.column` constructs so the surface stays inside the
expression layer.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from horizons_core.core.auth import Principal
from horizons_core.observability.health import (
    HealthQueryUnavailable,
    HealthWindow,
    fetch_api_metrics,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    BigInteger,
    Float,
    Integer,
    String,
    column,
    func,
    select,
    table,
)
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from horizons_api.deps import admin_operator_session_for_request, require_admin_principal

router = APIRouter(prefix="/v1/admin/health", tags=["admin"])


# ---- table descriptors ---------------------------------------------------
#
# Lightweight ``sqlalchemy.table`` declarations rather than full ORM
# models — we only need ``SELECT`` over a handful of columns, and an
# ORM model would imply write-side mapping these routes don't perform.
# The names match the migrations exactly; the types are present so the
# expression-layer ``where(... < now())`` etc. type-check.

_document_poll_schedule = table(
    "document_poll_schedule",
    column("document_id"),
    column("next_poll_at"),
    column("last_polled_at"),
    column("failure_count", Integer),
)
_ingestion_incident = table(
    "ingestion_incident",
    column("id", BigInteger),
    column("document_id"),
    column("error_class", String),
    column("occurred_at"),
)
_pg_stat_activity = table("pg_stat_activity", column("pid", Integer))
_pg_extension = table("pg_extension", column("extname", String))
_pg_stat_statements = table(
    "pg_stat_statements",
    column("query", String),
    column("calls", BigInteger),
    column("mean_exec_time", Float),
    column("total_exec_time", Float),
)


# ---- wire models ---------------------------------------------------------


class ApiMetricsValues(BaseModel):
    """Rate / p95 / error_rate for a single window."""

    model_config = ConfigDict(frozen=True)

    rate_per_minute: float | None
    p95_ms: float | None
    error_rate: float | None


class ApiMetricsWindow(BaseModel):
    """One window slot in the API health response.

    ``data_source`` is ``"log_analytics"`` on the happy path or
    ``"unavailable"`` when the upstream call could not complete. The
    fields are mutually exclusive in practice — ``values`` is ``None``
    iff ``data_source == "unavailable"``.
    """

    model_config = ConfigDict(frozen=True)

    window: HealthWindow
    data_source: Literal["log_analytics", "unavailable"]
    reason: str | None
    values: ApiMetricsValues | None


class ApiHealthResponse(BaseModel):
    """Two windows + a stable ``source`` tag for the SPA badge."""

    model_config = ConfigDict(frozen=True)

    source: Literal["application_insights"]
    windows: list[ApiMetricsWindow]


class PollBacklogItem(BaseModel):
    """One overdue ``document_poll_schedule`` row."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    next_poll_at: str
    last_polled_at: str | None
    failure_count: int


class IngestionIncidentItem(BaseModel):
    """One row from the last-24h ``ingestion_incident`` window."""

    model_config = ConfigDict(frozen=True)

    id: int
    document_id: str
    error_class: str
    occurred_at: str


class IngestionHealthResponse(BaseModel):
    """Ingestion-side health surface.

    ``backlog`` is a *count* of overdue rows plus a sample of the
    oldest ones (cap = 20). ``recent_incidents`` is the rolling 24h
    incident log, newest first, capped at 50 rows.
    """

    model_config = ConfigDict(frozen=True)

    poll_backlog_count: int
    poll_backlog_sample: list[PollBacklogItem]
    recent_incidents: list[IngestionIncidentItem]


class SlowQueryItem(BaseModel):
    """One row from ``pg_stat_statements`` (when available)."""

    model_config = ConfigDict(frozen=True)

    query: str
    calls: int
    mean_exec_ms: float
    total_exec_ms: float


class DbHealthResponse(BaseModel):
    """Postgres operator surface.

    ``slow_queries`` is ``data_source: "pg_stat_statements"`` when the
    extension is installed, otherwise ``data_source: "unavailable"``
    with ``reason: "extension not installed"`` and ``values: null``.
    """

    model_config = ConfigDict(frozen=True)

    connection_count: int
    replication_lag_seconds: float | None
    slow_queries_source: Literal["pg_stat_statements", "unavailable"]
    slow_queries_reason: str | None
    slow_queries: list[SlowQueryItem] | None


# ---- helpers -------------------------------------------------------------


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _api_window(window: HealthWindow) -> ApiMetricsWindow:
    try:
        metrics = fetch_api_metrics(window)
    except HealthQueryUnavailable as exc:
        return ApiMetricsWindow(
            window=window,
            data_source="unavailable",
            reason=exc.reason,
            values=None,
        )
    return ApiMetricsWindow(
        window=window,
        data_source="log_analytics",
        reason=None,
        values=ApiMetricsValues(
            rate_per_minute=metrics.rate_per_minute,
            p95_ms=metrics.p95_ms,
            error_rate=metrics.error_rate,
        ),
    )


# ---- routes --------------------------------------------------------------


@router.get("/api", response_model=ApiHealthResponse)
async def api_health(
    response: Response,
    _admin: Annotated[Principal, Depends(require_admin_principal)],
) -> ApiHealthResponse:
    """Request rate / p95 / error rate over 1h and 24h."""
    _no_store(response)
    return ApiHealthResponse(
        source="application_insights",
        windows=[_api_window("1h"), _api_window("24h")],
    )


_BACKLOG_PREDICATE = _document_poll_schedule.c.next_poll_at < func.now()
_BACKLOG_COUNT_STMT = (
    select(func.count()).select_from(_document_poll_schedule).where(_BACKLOG_PREDICATE)
)
_BACKLOG_SAMPLE_STMT = (
    select(
        _document_poll_schedule.c.document_id,
        _document_poll_schedule.c.next_poll_at,
        _document_poll_schedule.c.last_polled_at,
        _document_poll_schedule.c.failure_count,
    )
    .where(_BACKLOG_PREDICATE)
    .order_by(_document_poll_schedule.c.next_poll_at)
    .limit(20)
)
_INCIDENTS_STMT = (
    select(
        _ingestion_incident.c.id,
        _ingestion_incident.c.document_id,
        _ingestion_incident.c.error_class,
        _ingestion_incident.c.occurred_at,
    )
    .where(_ingestion_incident.c.occurred_at >= func.now() - timedelta(hours=24))
    .order_by(_ingestion_incident.c.occurred_at.desc())
    .limit(50)
)


@router.get("/ingestion", response_model=IngestionHealthResponse)
async def ingestion_health(
    response: Response,
    _admin: Annotated[Principal, Depends(require_admin_principal)],
    session: Annotated[AsyncSession, Depends(admin_operator_session_for_request)],
) -> IngestionHealthResponse:
    """Overdue polls + recent incidents."""
    _no_store(response)

    backlog_count = (await session.execute(_BACKLOG_COUNT_STMT)).scalar_one()
    backlog_rows = (await session.execute(_BACKLOG_SAMPLE_STMT)).all()
    incident_rows = (await session.execute(_INCIDENTS_STMT)).all()

    return IngestionHealthResponse(
        poll_backlog_count=int(backlog_count),
        poll_backlog_sample=[
            PollBacklogItem(
                document_id=str(row[0]),
                next_poll_at=row[1].isoformat(),
                last_polled_at=row[2].isoformat() if row[2] is not None else None,
                failure_count=int(row[3]),
            )
            for row in backlog_rows
        ],
        recent_incidents=[
            IngestionIncidentItem(
                id=int(row[0]),
                document_id=str(row[1]),
                error_class=row[2],
                occurred_at=row[3].isoformat(),
            )
            for row in incident_rows
        ],
    )


_CONN_COUNT_STMT = select(func.count()).select_from(_pg_stat_activity)
_PG_STAT_STATEMENTS_PROBE_STMT = (
    select(func.count())
    .select_from(_pg_extension)
    .where(_pg_extension.c.extname == "pg_stat_statements")
)
_SLOW_QUERIES_STMT = (
    select(
        _pg_stat_statements.c.query,
        _pg_stat_statements.c.calls,
        _pg_stat_statements.c.mean_exec_time,
        _pg_stat_statements.c.total_exec_time,
    )
    .order_by(_pg_stat_statements.c.mean_exec_time.desc())
    .limit(5)
)


async def _slow_queries(
    session: AsyncSession,
) -> tuple[
    Literal["pg_stat_statements", "unavailable"],
    str | None,
    list[SlowQueryItem] | None,
]:
    """Return the top-5 slow queries when the extension is installed."""
    extension_count = (await session.execute(_PG_STAT_STATEMENTS_PROBE_STMT)).scalar_one()
    if not extension_count:
        return "unavailable", "extension not installed", None
    try:
        rows: list[Any] = list((await session.execute(_SLOW_QUERIES_STMT)).all())
    except (ProgrammingError, DBAPIError) as exc:
        # Extension present but the role lacks SELECT on the view, or
        # an older pg_stat_statements signature is installed.
        return "unavailable", f"query failed: {type(exc).__name__}", None
    return (
        "pg_stat_statements",
        None,
        [
            SlowQueryItem(
                query=str(row[0]),
                calls=int(row[1]),
                mean_exec_ms=float(row[2]),
                total_exec_ms=float(row[3]),
            )
            for row in rows
        ],
    )


@router.get("/db", response_model=DbHealthResponse)
async def db_health(
    response: Response,
    _admin: Annotated[Principal, Depends(require_admin_principal)],
    session: Annotated[AsyncSession, Depends(admin_operator_session_for_request)],
) -> DbHealthResponse:
    """Connection count + replication lag + slow queries."""
    _no_store(response)
    connection_count = int((await session.execute(_CONN_COUNT_STMT)).scalar_one())
    source, reason, slow_queries = await _slow_queries(session)
    return DbHealthResponse(
        connection_count=connection_count,
        replication_lag_seconds=None,
        slow_queries_source=source,
        slow_queries_reason=reason,
        slow_queries=slow_queries,
    )
