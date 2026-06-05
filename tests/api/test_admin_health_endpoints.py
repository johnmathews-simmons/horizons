# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""WU7.2 integration tests — ``/v1/admin/health/{api,ingestion,db}``.

Per acceptance:

- Admin (role='admin') gets 200 on every endpoint.
- Non-admin (role='client') gets 403 with body ``"admin role required"``.
- Invalid query filters get 422.

The Log Analytics call in ``/api`` is replaced with a stub client
fixture; ingestion / db reads hit the real testcontainers Postgres.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

from tests.api.conftest import bearer, login, make_user

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sqlalchemy import Engine


# ---- Log Analytics stub -----------------------------------------------------


class _StubTable:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows


class _StubResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.tables = [_StubTable(rows)]


class _StubLogsQueryClient:
    """Test seam for :mod:`horizons_core.observability.health`."""

    def __init__(self, rows_by_window: dict[timedelta, list[tuple[Any, ...]]]) -> None:
        self._rows_by_window = rows_by_window
        self.calls: list[tuple[str, str, timedelta]] = []

    def query_workspace(
        self,
        workspace_id: str,
        query: str,
        *,
        timespan: timedelta,
    ) -> _StubResult:
        self.calls.append((workspace_id, query, timespan))
        return _StubResult(self._rows_by_window.get(timespan, []))


# ---- /v1/admin/health/api ---------------------------------------------------


@pytest.mark.integration
def test_admin_health_api_unavailable_when_no_workspace(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """The local-dev default: no workspace env → unavailable shape, not 500."""
    make_user(migrated_postgres_h, "admin_h_api1@example.com", role="admin")
    token = login(client, "admin_h_api1@example.com")
    response = client.get("/v1/admin/health/api", headers=bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "application_insights"
    windows = {w["window"]: w for w in body["windows"]}
    assert windows.keys() == {"1h", "24h"}
    for window in windows.values():
        assert window["data_source"] == "unavailable"
        assert window["reason"] == "workspace id not configured"
        assert window["values"] is None


@pytest.mark.integration
def test_admin_health_api_returns_metrics_when_workspace_present(
    client: TestClient,
    migrated_postgres_h: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the workspace env + a fake client, the route returns numbers."""
    from horizons_core.observability import health as health_mod

    make_user(migrated_postgres_h, "admin_h_api2@example.com", role="admin")
    monkeypatch.setenv("HORIZONS_LOG_ANALYTICS_WORKSPACE_ID", "fake-workspace-id")
    stub = _StubLogsQueryClient(
        rows_by_window={
            timedelta(hours=1): [(600, 12, 240.5)],
            timedelta(hours=24): [(7200, 36, 310.0)],
        }
    )
    health_mod.set_logs_query_client_for_tests(stub)
    try:
        token = login(client, "admin_h_api2@example.com")
        response = client.get("/v1/admin/health/api", headers=bearer(token))
    finally:
        health_mod.reset_logs_query_client_for_tests()

    assert response.status_code == 200, response.text
    body = response.json()
    windows = {w["window"]: w for w in body["windows"]}
    one_h = windows["1h"]
    assert one_h["data_source"] == "log_analytics"
    assert one_h["values"]["rate_per_minute"] == pytest.approx(10.0)  # 600 / 60
    assert one_h["values"]["p95_ms"] == pytest.approx(240.5)
    assert one_h["values"]["error_rate"] == pytest.approx(12 / 600)
    twenty_four = windows["24h"]
    assert twenty_four["values"]["rate_per_minute"] == pytest.approx(7200 / (24 * 60))
    assert twenty_four["values"]["error_rate"] == pytest.approx(36 / 7200)


@pytest.mark.integration
def test_admin_health_api_403_for_client(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    make_user(migrated_postgres_h, "client_h_api@example.com", role="client")
    token = login(client, "client_h_api@example.com")
    response = client.get("/v1/admin/health/api", headers=bearer(token))
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "admin role required"


# ---- /v1/admin/health/ingestion ---------------------------------------------


def _seed_document(engine: Engine, slug: str) -> str:
    with engine.begin() as conn:
        return str(
            conn.execute(
                text(
                    "INSERT INTO documents "
                    "(jurisdiction, sector, lawstronaut_document_id, title) "
                    "VALUES ('uk', 'banking', :s, 'Test Doc') RETURNING id"
                ),
                {"s": slug},
            ).scalar_one()
        )


def _seed_overdue_poll(engine: Engine, document_id: str, *, failure_count: int = 0) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO document_poll_schedule "
                "(document_id, cadence_interval, next_poll_at, failure_count) "
                "VALUES (:d, interval '1 day', now() - interval '1 hour', :f)"
            ),
            {"d": document_id, "f": failure_count},
        )


def _seed_recent_incident(engine: Engine, document_id: str, *, error_class: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ingestion_incident "
                "(document_id, error_class, payload) "
                "VALUES (:d, :e, '{}'::jsonb)"
            ),
            {"d": document_id, "e": error_class},
        )


@pytest.mark.integration
def test_admin_health_ingestion_returns_backlog_and_incidents(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    make_user(migrated_postgres_h, "admin_h_ing@example.com", role="admin")
    doc_a = _seed_document(migrated_postgres_h, "ing_doc_a")
    doc_b = _seed_document(migrated_postgres_h, "ing_doc_b")
    _seed_overdue_poll(migrated_postgres_h, doc_a, failure_count=2)
    _seed_overdue_poll(migrated_postgres_h, doc_b, failure_count=0)
    _seed_recent_incident(migrated_postgres_h, doc_a, error_class="upstream_5xx")

    token = login(client, "admin_h_ing@example.com")
    response = client.get("/v1/admin/health/ingestion", headers=bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["poll_backlog_count"] >= 2
    backlog_ids = {item["document_id"] for item in body["poll_backlog_sample"]}
    assert {doc_a, doc_b} <= backlog_ids
    incident_classes = {item["error_class"] for item in body["recent_incidents"]}
    assert "upstream_5xx" in incident_classes


@pytest.mark.integration
def test_admin_health_ingestion_403_for_client(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    make_user(migrated_postgres_h, "client_h_ing@example.com", role="client")
    token = login(client, "client_h_ing@example.com")
    response = client.get("/v1/admin/health/ingestion", headers=bearer(token))
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "admin role required"


# ---- /v1/admin/health/db ----------------------------------------------------


@pytest.mark.integration
def test_admin_health_db_returns_connection_count(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    make_user(migrated_postgres_h, "admin_h_db@example.com", role="admin")
    token = login(client, "admin_h_db@example.com")
    response = client.get("/v1/admin/health/db", headers=bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    # testcontainers Postgres always has at least the server's own
    # background workers plus our session — count > 0 is the loose
    # invariant.
    assert body["connection_count"] >= 1
    assert body["replication_lag_seconds"] is None
    # ``pg_stat_statements`` is NOT installed in the
    # testcontainers ``postgres:18-alpine`` image; the route reports
    # the unavailable shape.
    assert body["slow_queries_source"] == "unavailable"
    assert body["slow_queries_reason"] == "extension not installed"
    assert body["slow_queries"] is None


@pytest.mark.integration
def test_admin_health_db_403_for_client(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    make_user(migrated_postgres_h, "client_h_db@example.com", role="client")
    token = login(client, "client_h_db@example.com")
    response = client.get("/v1/admin/health/db", headers=bearer(token))
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "admin role required"
