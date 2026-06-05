# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""WU7.4 integration tests — ``GET /v1/admin/audit``.

Per acceptance:

1. Admin issues a few admin write operations (POST + PATCH on the
   WU4.5 subscriptions surface); the audit endpoint returns those
   rows with the expected shape.
2. Filters (``since``, ``admin_id``, ``target_user_id``, ``action``,
   ``limit``) narrow the result set as documented.
3. Non-admin caller gets 403.
4. ``limit`` is clamped silently at 500; bad query types get 422.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tests.api.conftest import bearer, login, make_user

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sqlalchemy import Engine


def _create_subscription(client: TestClient, token: str, target_id: uuid.UUID) -> str:
    """POST a subscription and return its id. Generates one audit row."""
    response = client.post(
        "/v1/admin/subscriptions",
        headers=bearer(token),
        json={
            "user_id": str(target_id),
            "scopes": [{"jurisdiction": "uk", "sector": "banking"}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _patch_subscription(client: TestClient, token: str, subscription_id: str) -> None:
    """PATCH a subscription. Generates one audit row."""
    response = client.patch(
        f"/v1/admin/subscriptions/{subscription_id}",
        headers=bearer(token),
        json={"add_scopes": [{"jurisdiction": "uk", "sector": "fintech"}]},
    )
    assert response.status_code == 200, response.text


# ---- 1. Round-trip: write three admin ops → audit endpoint returns them ----


@pytest.mark.integration
def test_audit_endpoint_returns_rows_from_recent_admin_writes(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    admin_id = make_user(migrated_postgres_h, "admin_audit_rt@example.com", role="admin")
    target_a = make_user(migrated_postgres_h, "client_audit_a@example.com", role="client")
    target_b = make_user(migrated_postgres_h, "client_audit_b@example.com", role="client")

    admin_token = login(client, "admin_audit_rt@example.com")
    sub_a = _create_subscription(client, admin_token, target_a)
    sub_b = _create_subscription(client, admin_token, target_b)
    _patch_subscription(client, admin_token, sub_a)
    _ = sub_b  # consumed; the writes themselves are the test fixtures

    # GET /v1/admin/audit (this call also writes one audit row — the
    # admin dep audits on entry — so the read sees three writes plus
    # whatever audit rows the audit calls themselves contribute).
    response = client.get(
        "/v1/admin/audit",
        headers=bearer(admin_token),
        params={"admin_id": str(admin_id), "limit": 100},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert "since" in body
    assert body["limit"] == 100
    rows = body["rows"]
    # At least 3 from the seeded writes + 1 from this GET = 4. Be loose
    # because the audit endpoint is itself audited.
    assert body["count"] >= 4
    assert all(r["admin_id"] == str(admin_id) for r in rows)
    # All rows from the WU4.5 endpoints are operator-mode (target_user_id NULL).
    assert {r["mode"] for r in rows} == {"operator"}
    assert all(r["target_user_id"] is None for r in rows)
    # Wire shape sanity.
    sample = rows[0]
    assert set(sample.keys()) >= {
        "id",
        "admin_id",
        "target_user_id",
        "mode",
        "token_id",
        "reason",
        "granted_at",
    }


# ---- 2. Filters narrow the result set -------------------------------------


@pytest.mark.integration
def test_audit_filter_by_action_excludes_other_modes(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    admin_id = make_user(migrated_postgres_h, "admin_audit_act@example.com", role="admin")
    target = make_user(migrated_postgres_h, "client_audit_act@example.com", role="client")
    admin_token = login(client, "admin_audit_act@example.com")
    _create_subscription(client, admin_token, target)

    # Filter for impersonation rows; we haven't created any, so the
    # result must be empty (the GET itself writes an operator row
    # which the filter excludes).
    response = client.get(
        "/v1/admin/audit",
        headers=bearer(admin_token),
        params={"admin_id": str(admin_id), "action": "impersonation"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 0


@pytest.mark.integration
def test_audit_filter_by_since_excludes_old_rows(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    admin_id = make_user(migrated_postgres_h, "admin_audit_since@example.com", role="admin")
    target = make_user(migrated_postgres_h, "client_audit_since@example.com", role="client")
    admin_token = login(client, "admin_audit_since@example.com")
    _create_subscription(client, admin_token, target)

    far_future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    response = client.get(
        "/v1/admin/audit",
        headers=bearer(admin_token),
        params={"admin_id": str(admin_id), "since": far_future},
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 0


# ---- 3. Non-admin → 403 ---------------------------------------------------


@pytest.mark.integration
def test_audit_endpoint_403_for_client(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    make_user(migrated_postgres_h, "client_audit_403@example.com", role="client")
    token = login(client, "client_audit_403@example.com")
    response = client.get("/v1/admin/audit", headers=bearer(token))
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "admin role required"


# ---- 4. Filter validation + limit clamp ------------------------------------


@pytest.mark.integration
def test_audit_endpoint_422_on_invalid_uuid(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    make_user(migrated_postgres_h, "admin_audit_422@example.com", role="admin")
    token = login(client, "admin_audit_422@example.com")
    response = client.get(
        "/v1/admin/audit",
        headers=bearer(token),
        params={"admin_id": "not-a-uuid"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.integration
def test_audit_endpoint_422_on_invalid_action(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    make_user(migrated_postgres_h, "admin_audit_act422@example.com", role="admin")
    token = login(client, "admin_audit_act422@example.com")
    response = client.get(
        "/v1/admin/audit",
        headers=bearer(token),
        params={"action": "definitely-not-a-mode"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.integration
def test_audit_endpoint_clamps_limit_to_500(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    make_user(migrated_postgres_h, "admin_audit_clamp@example.com", role="admin")
    token = login(client, "admin_audit_clamp@example.com")
    response = client.get(
        "/v1/admin/audit",
        headers=bearer(token),
        params={"limit": 100_000},
    )
    assert response.status_code == 200, response.text
    assert response.json()["limit"] == 500
