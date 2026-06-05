# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""WU4.7 integration tests — ``GET /v1/admin/clients``.

Adversary class addressed: "an admin enumerating client identifiers
without leaving an audit trail." The defence is the dependency stack:
the route depends on ``admin_operator_session_for_request``, which
writes one ``admin_access_log`` row (``mode='operator'``,
``target_user_id NULL``) before the route body runs. The first test
below pins the audit-row write as a behaviour, not just a side effect.

Other behaviours pinned:

- Only ``role='client'`` users are listed (admins excluded).
- Stable ordering on ``(created_at ASC, id ASC)``.
- Offset / limit paging; ``limit`` silently clamped at 200.
- Non-admin → 403; missing bearer → 401.
- ``Cache-Control: private, no-store`` echoed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from tests.api.conftest import bearer, login, make_user

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sqlalchemy import Engine


# ---- Adversary 1: every fetch writes a list_clients audit row -------------


@pytest.mark.integration
def test_clients_list_writes_one_operator_audit_row_per_request(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """The defence against unaudited enumeration: every fetch is logged.

    Two consecutive list calls produce exactly two new audit rows
    attributable to this admin. We compare the row count delta because
    other parallel tests may have written audit rows; absolute counts
    against a shared session-scoped DB would be flaky.
    """
    admin_id = make_user(
        migrated_postgres_h, "admin_list_clients@example.com", role="admin"
    )
    make_user(migrated_postgres_h, "audit_target_a@example.com", role="client")

    admin_token = login(client, "admin_list_clients@example.com")

    with migrated_postgres_h.begin() as conn:
        before = conn.execute(
            text(
                "SELECT COUNT(*) FROM admin_access_log "
                "WHERE admin_id = :a AND mode = 'operator'"
            ),
            {"a": str(admin_id)},
        ).scalar_one()

    # First fetch.
    r1 = client.get("/v1/admin/clients", headers=bearer(admin_token))
    assert r1.status_code == 200, r1.text
    # Second fetch.
    r2 = client.get("/v1/admin/clients", headers=bearer(admin_token))
    assert r2.status_code == 200, r2.text

    with migrated_postgres_h.begin() as conn:
        after = conn.execute(
            text(
                "SELECT COUNT(*) FROM admin_access_log "
                "WHERE admin_id = :a AND mode = 'operator'"
            ),
            {"a": str(admin_id)},
        ).scalar_one()

    assert after - before == 2, (
        f"expected exactly 2 new operator audit rows from 2 list calls, got {after - before}"
    )


# ---- Listing semantics ----------------------------------------------------


@pytest.mark.integration
def test_clients_list_excludes_admin_role(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """Admins must never appear in the clients list.

    Surfacing them would let an operator click the wrong row and try
    to impersonate a fellow admin — the impersonate endpoint refuses
    that, but the list is the right place to omit it.
    """
    make_user(
        migrated_postgres_h, "admin_excludes_admins@example.com", role="admin"
    )
    other_admin = make_user(
        migrated_postgres_h, "other_admin_excluded@example.com", role="admin"
    )
    client_email = "client_visible_in_list@example.com"
    make_user(migrated_postgres_h, client_email, role="client")

    admin_token = login(client, "admin_excludes_admins@example.com")
    response = client.get("/v1/admin/clients", headers=bearer(admin_token))

    assert response.status_code == 200, response.text
    body = response.json()
    emails = {row["email"] for row in body["rows"]}
    assert client_email in emails
    assert "other_admin_excluded@example.com" not in emails
    assert "admin_excludes_admins@example.com" not in emails
    assert str(other_admin) not in {row["id"] for row in body["rows"]}


@pytest.mark.integration
def test_clients_list_stable_order_paging(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """Paging is stable on ``(created_at ASC, id ASC)``.

    Offset paging without a stable order shuffles rows between pages,
    making the SPA's "page N of M" UX a lie. The repo orders deterministically.
    """
    make_user(migrated_postgres_h, "admin_paging@example.com", role="admin")
    # Seed 5 clients; their `created_at` values are server-side now(),
    # so insertion order = creation order for this test.
    for i in range(5):
        make_user(migrated_postgres_h, f"paging_client_{i}@example.com", role="client")

    admin_token = login(client, "admin_paging@example.com")

    page1 = client.get(
        "/v1/admin/clients",
        headers=bearer(admin_token),
        params={"limit": 2, "offset": 0},
    )
    page2 = client.get(
        "/v1/admin/clients",
        headers=bearer(admin_token),
        params={"limit": 2, "offset": 2},
    )
    assert page1.status_code == 200 and page2.status_code == 200

    page1_ids = [r["id"] for r in page1.json()["rows"]]
    page2_ids = [r["id"] for r in page2.json()["rows"]]
    assert len(page1_ids) == 2
    assert len(page2_ids) == 2
    assert set(page1_ids).isdisjoint(set(page2_ids))


@pytest.mark.integration
def test_clients_list_limit_clamped_silently(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """``limit > 200`` is silently clamped; the response echoes the cap."""
    make_user(migrated_postgres_h, "admin_clamp@example.com", role="admin")
    make_user(migrated_postgres_h, "clamp_client@example.com", role="client")

    admin_token = login(client, "admin_clamp@example.com")
    response = client.get(
        "/v1/admin/clients",
        headers=bearer(admin_token),
        params={"limit": 100_000},
    )
    assert response.status_code == 200, response.text
    assert response.json()["limit"] == 200


@pytest.mark.integration
def test_clients_list_response_is_no_store(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """Admin enumeration must not be cacheable by intermediaries."""
    make_user(migrated_postgres_h, "admin_no_store@example.com", role="admin")
    make_user(migrated_postgres_h, "no_store_client@example.com", role="client")

    admin_token = login(client, "admin_no_store@example.com")
    response = client.get("/v1/admin/clients", headers=bearer(admin_token))
    assert response.status_code == 200, response.text
    assert response.headers.get("Cache-Control") == "private, no-store"


# ---- Authorisation gate ---------------------------------------------------


@pytest.mark.integration
def test_clients_list_non_admin_returns_403(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """A client bearer on the admin URL gets 403 per the WU4.5 contract."""
    make_user(migrated_postgres_h, "client_probes_admin@example.com", role="client")
    client_token = login(client, "client_probes_admin@example.com")

    response = client.get("/v1/admin/clients", headers=bearer(client_token))
    assert response.status_code == 403
    assert response.json() == {"detail": "admin role required"}


@pytest.mark.integration
def test_clients_list_missing_bearer_returns_401(client: TestClient) -> None:
    """Missing bearer → uniform 401 from the auth layer."""
    response = client.get("/v1/admin/clients")
    assert response.status_code == 401
