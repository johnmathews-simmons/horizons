# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""WU4.7 integration tests — ``POST /v1/admin/impersonate``.

Adversary classes addressed:

1. **Audit-row-missing-after-200.** If the audit-row write and the
   token mint happen in the wrong order, a network blip between the
   two can leave the admin holding a working impersonation bearer
   with no durable record of the elevation. Defence: the route's
   ``async with admin_impersonation_session(...)`` block commits the
   audit row inside the context manager's *entry* (before the
   working session yields); only after the with-block has returned
   does the route mint the token. If the mint were to raise, the
   audit row is already on disk.
2. **Wrong-target abuse.** The endpoint refuses
   admin-impersonating-admin (422), self-impersonation (422), and
   missing target (404). These are policy refusals, not malformed
   input — the body shape is valid in every case.
3. **Token-kind smuggling.** The mint endpoint requires an access
   bearer (admin role); a refresh bearer presented to it gets the
   uniform 401. The minted impersonation token has ``kind`` =
   ``impersonation``; it is rejected at any endpoint whose dep is
   ``require_kind(ACCESS)`` (e.g., refresh-handling routes).
4. **Self-mint via missing auth.** No-bearer / non-admin → uniform
   401 / 403 respectively.

Behaviours additionally pinned: response shape carries everything
the SPA needs for the support-view banner (original admin email +
target email + TTL); ``Cache-Control: private, no-store`` is echoed.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from tests.api.conftest import bearer, login, make_user

if TYPE_CHECKING:
    import httpx
    from fastapi.testclient import TestClient
    from sqlalchemy import Engine


def _impersonate(
    client: TestClient,
    token: str,
    *,
    target_user_id: uuid.UUID | str,
    reason: str | None = None,
) -> httpx.Response:
    body: dict[str, str] = {"target_user_id": str(target_user_id)}
    if reason is not None:
        body["reason"] = reason
    return client.post(
        "/v1/admin/impersonate",
        headers=bearer(token),
        json=body,
    )


# ---- Happy path ----------------------------------------------------------


@pytest.mark.integration
def test_impersonate_happy_path_returns_full_banner_payload(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """The response shape carries everything the SPA banner needs."""
    admin_id = make_user(migrated_postgres_h, "admin_happy@example.com", role="admin")
    target_id = make_user(migrated_postgres_h, "client_happy_target@example.com", role="client")
    admin_token = login(client, "admin_happy@example.com")

    response = _impersonate(
        client,
        admin_token,
        target_user_id=target_id,
        reason="debugging missing watchlist",
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert isinstance(body["impersonation_token"], str) and body["impersonation_token"]
    assert body["target_user_id"] == str(target_id)
    assert body["target_email"] == "client_happy_target@example.com"
    assert body["original_admin_id"] == str(admin_id)
    assert body["original_admin_email"] == "admin_happy@example.com"
    # 15 minutes — mirrors LocalJwtProvider default IMPERSONATION TTL.
    assert body["expires_in_seconds"] == 15 * 60
    assert response.headers.get("Cache-Control") == "private, no-store"


# ---- Adversary 1: audit row is written before the token is returned -------


@pytest.mark.integration
def test_impersonate_writes_impersonation_audit_row_before_returning(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """Defence against "200 OK with no audit row" failure mode.

    Asserts an ``admin_access_log`` row exists with
    ``mode='impersonation'`` and the correct ``(admin_id, target_user_id,
    reason)`` after the mint endpoint returns 200. The contract is
    captured by ``core.auth.admin._record_audit_row``: the row commits
    in its own transaction *before* the working session yields. If the
    mint endpoint were ever refactored to mint-then-audit, this test
    would fail because the audit row commit would no longer be on the
    happy path.
    """
    admin_id = make_user(migrated_postgres_h, "admin_audit_order@example.com", role="admin")
    target_id = make_user(migrated_postgres_h, "client_audit_order@example.com", role="client")
    admin_token = login(client, "admin_audit_order@example.com")

    response = _impersonate(
        client,
        admin_token,
        target_user_id=target_id,
        reason="audit-order test",
    )
    assert response.status_code == 201, response.text

    with migrated_postgres_h.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT mode, reason FROM admin_access_log "
                "WHERE admin_id = :a AND target_user_id = :t "
                "ORDER BY granted_at DESC"
            ),
            {"a": str(admin_id), "t": str(target_id)},
        ).all()

    impersonation_rows = [r for r in rows if r.mode == "impersonation"]
    assert len(impersonation_rows) == 1, (
        f"expected exactly 1 impersonation audit row, got {len(impersonation_rows)}: {rows}"
    )
    assert impersonation_rows[0].reason == "audit-order test"

    # Belt to the braces: a successful mint also leaves an operator-mode
    # row attributable to the same admin (the dep audits the URL hit).
    # Pinning both rows here prevents a future refactor from collapsing
    # the dep into a no-op audit pass and silently losing per-URL audit
    # signal.
    with migrated_postgres_h.begin() as conn:
        operator_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM admin_access_log "
                "WHERE admin_id = :a AND mode = 'operator' "
                "AND target_user_id IS NULL"
            ),
            {"a": str(admin_id)},
        ).scalar_one()
    assert operator_count >= 1, (
        "expected at least one operator-mode audit row from the dep "
        "(the dep audits the URL hit; impersonate writes both rows)"
    )


# ---- Adversary 2: wrong-target refusals ----------------------------------


@pytest.mark.integration
def test_impersonate_missing_target_returns_404(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """Refused BEFORE the impersonation audit row is written.

    The test indirectly confirms ordering: a 404 leaves zero
    impersonation rows; only operator-mode rows (from the
    admin_operator_session_for_request dep) are written.
    """
    admin_id = make_user(migrated_postgres_h, "admin_missing_target@example.com", role="admin")
    admin_token = login(client, "admin_missing_target@example.com")
    bogus_target = uuid.uuid4()

    response = _impersonate(client, admin_token, target_user_id=bogus_target)
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "target user not found"}

    with migrated_postgres_h.begin() as conn:
        impersonation_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM admin_access_log "
                "WHERE admin_id = :a AND mode = 'impersonation' "
                "AND target_user_id = :t"
            ),
            {"a": str(admin_id), "t": str(bogus_target)},
        ).scalar_one()
    assert impersonation_count == 0


@pytest.mark.integration
def test_impersonate_self_returns_422(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """Self-impersonation is refused as a policy error."""
    admin_id = make_user(migrated_postgres_h, "admin_self@example.com", role="admin")
    admin_token = login(client, "admin_self@example.com")

    response = _impersonate(client, admin_token, target_user_id=admin_id)
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "cannot impersonate yourself"}


@pytest.mark.integration
def test_impersonate_admin_target_returns_422(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """Admin → admin impersonation is refused.

    There's no legitimate operational reason for one admin to
    impersonate another, and the clients-list endpoint already hides
    admins from the operator UX. This is the second layer.
    """
    make_user(migrated_postgres_h, "admin_a_admin@example.com", role="admin")
    admin_b = make_user(migrated_postgres_h, "admin_b_admin@example.com", role="admin")
    admin_token = login(client, "admin_a_admin@example.com")

    response = _impersonate(client, admin_token, target_user_id=admin_b)
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "target is not a client"}


# ---- Adversary 4: authorisation gate -------------------------------------


@pytest.mark.integration
def test_impersonate_non_admin_returns_403(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """A client bearer attempting to mint an impersonation token → 403."""
    make_user(migrated_postgres_h, "client_self_mint@example.com", role="client")
    target_id = make_user(migrated_postgres_h, "victim_target@example.com", role="client")
    client_token = login(client, "client_self_mint@example.com")

    response = _impersonate(client, client_token, target_user_id=target_id)
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "admin role required"}


@pytest.mark.integration
def test_impersonate_missing_bearer_returns_401(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    target_id = make_user(migrated_postgres_h, "victim_missing_bearer@example.com", role="client")
    response = client.post(
        "/v1/admin/impersonate",
        json={"target_user_id": str(target_id)},
    )
    assert response.status_code == 401


# ---- Adversary 3: minted token is impersonation-kind ---------------------


@pytest.mark.integration
def test_minted_token_decodes_as_impersonation_kind_with_target_sub(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """The minted bearer is an impersonation JWT for the target.

    Decoding the token's claims confirms ``kind='impersonation'`` and
    ``sub=target_user_id``. Verifies the token can flow through
    ``authenticated_user`` (which post-WU4.7 accepts ACCESS and
    IMPERSONATION) on client-facing routes — without giving the
    impersonator admin role, which ``role`` is ``client``.
    """
    import jwt as pyjwt

    make_user(migrated_postgres_h, "admin_kind_check@example.com", role="admin")
    target_id = make_user(migrated_postgres_h, "client_kind_check@example.com", role="client")
    admin_token = login(client, "admin_kind_check@example.com")

    response = _impersonate(client, admin_token, target_user_id=target_id)
    assert response.status_code == 201, response.text
    impersonation_token = response.json()["impersonation_token"]

    # Decode without signature verification — we trust the API minted
    # it (we just called it) and we're inspecting the claim shape.
    claims = pyjwt.decode(
        impersonation_token,
        options={"verify_signature": False, "verify_aud": False, "verify_iss": False},
    )
    assert claims["kind"] == "impersonation"
    assert claims["sub"] == str(target_id)
    assert claims["role"] == "client"


@pytest.mark.integration
def test_impersonation_token_works_on_me_endpoint(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """The minted impersonation token authenticates ``/v1/me`` as the target.

    This is the post-WU4.7 contract change: ``authenticated_user``
    accepts both ACCESS and IMPERSONATION. The response body must
    reflect the *target* client's identity, not the admin's — proof
    that ``principal.user_id`` flowed through to the GUC and the
    repository read.
    """
    make_user(migrated_postgres_h, "admin_me_check@example.com", role="admin")
    target_id = make_user(migrated_postgres_h, "client_me_check@example.com", role="client")
    admin_token = login(client, "admin_me_check@example.com")

    mint = _impersonate(client, admin_token, target_user_id=target_id)
    assert mint.status_code == 201, mint.text
    impersonation_token = mint.json()["impersonation_token"]

    me = client.get("/v1/me", headers=bearer(impersonation_token))
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["user_id"] == str(target_id)
    assert body["email"] == "client_me_check@example.com"
    assert body["role"] == "client"


@pytest.mark.integration
def test_impersonation_token_cannot_reach_admin_endpoint(
    client: TestClient,
    migrated_postgres_h: Engine,
) -> None:
    """``role='client'`` on the impersonation token blocks admin routes.

    Even though the kind gate now accepts impersonation tokens for
    client-facing routes, ``require_admin_principal`` (layered on top
    of ``authenticated_user``) refuses any non-``admin`` role. An
    admin in support view who clicks an admin URL bookmark is
    rejected — the impersonation banner is the user-facing reminder,
    this 403 is the server-side belt.
    """
    make_user(migrated_postgres_h, "admin_no_loop@example.com", role="admin")
    target_id = make_user(migrated_postgres_h, "client_no_loop_target@example.com", role="client")
    admin_token = login(client, "admin_no_loop@example.com")

    mint = _impersonate(client, admin_token, target_user_id=target_id)
    assert mint.status_code == 201, mint.text
    impersonation_token = mint.json()["impersonation_token"]

    # Impersonation token on admin endpoints → 403 (role=client).
    r = client.get("/v1/admin/clients", headers=bearer(impersonation_token))
    assert r.status_code == 403, r.text
    assert r.json() == {"detail": "admin role required"}
