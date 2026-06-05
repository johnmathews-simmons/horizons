"""WU1.9 — Admin operator and impersonation paths.

Three scenarios cover the acceptance contract:

1. **Operator** — an admin opens an ``admin_operator_session`` and
   reads watchlists; both clients' rows come back (BYPASSRLS), and
   exactly one ``admin_access_log`` row is written with
   ``mode='operator'`` and ``target_user_id IS NULL``.

2. **Impersonation** — an admin opens an
   ``admin_impersonation_session(target=A)``; only A's watchlists are
   visible (RLS fires as if A made the request), and exactly one
   ``admin_access_log`` row is written with ``mode='impersonation'``
   and ``target_user_id = A``. The yielded session also reports the
   admin's id under ``app.impersonating_admin_id``.

3. **Exit cleanly** — after exiting both admin contexts, a normal
   ``two_clients.session_for(b)`` request sees only B's rows. The
   ``DISCARD ALL`` on pool checkin from WU1.5 is the safety net; no
   admin GUC leaks into the next request.

All assertions go through ``WatchlistsRepository`` so the contract is
end-to-end. Verification reads of ``admin_access_log`` use the
``two_clients.admin_session()`` fixture (which assumes
``admin_bypass``) — ``api_app`` has no grant on the table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import sqlalchemy
from horizons_core.core.auth.admin import (
    admin_impersonation_session,
    admin_operator_session,
)
from horizons_core.db.models.admin_access_log import AdminAccessMode
from horizons_core.repos.admin_access_log import AdminAccessLogRepository
from horizons_core.repos.watchlists import WatchlistsRepository

if TYPE_CHECKING:
    from tests.isolation.conftest import TwoClients


@pytest.mark.integration
async def test_operator_sees_both_clients_and_writes_audit_row(
    two_clients: TwoClients,
) -> None:
    """``admin_operator_session`` reads cross-tenant and audits the access."""
    async with admin_operator_session(
        two_clients.admin_id,
        engine=two_clients.async_engine,
        reason="support",
    ) as session:
        ids = {w.id for w in await WatchlistsRepository(session).list_for()}

    assert two_clients.a_watchlist_id in ids
    assert two_clients.b_watchlist_id in ids

    async with two_clients.admin_session() as session:
        audit_rows = await AdminAccessLogRepository(session).list_for_admin(two_clients.admin_id)

    operator_rows = [r for r in audit_rows if r.mode == AdminAccessMode.OPERATOR]
    assert len(operator_rows) == 1
    row = operator_rows[0]
    assert row.admin_id == two_clients.admin_id
    assert row.target_user_id is None
    assert row.token_id is not None
    assert row.reason == "support"


@pytest.mark.integration
async def test_impersonation_sees_only_target_and_writes_audit_row(
    two_clients: TwoClients,
) -> None:
    """``admin_impersonation_session`` is RLS-bound to the target user."""
    async with admin_impersonation_session(
        two_clients.admin_id,
        two_clients.a_id,
        engine=two_clients.async_engine,
        reason="support-impersonate-A",
    ) as session:
        ids = {w.id for w in await WatchlistsRepository(session).list_for()}
        # The admin's id is reachable to downstream observability.
        impersonating = await session.execute(
            sqlalchemy.text("SELECT current_setting('app.impersonating_admin_id', true)")
        )

    assert ids == {two_clients.a_watchlist_id}, (
        f"impersonation must see only target A's rows, got {ids}"
    )
    assert impersonating.scalar_one() == str(two_clients.admin_id)

    async with two_clients.admin_session() as session:
        audit_rows = await AdminAccessLogRepository(session).list_for_admin(two_clients.admin_id)

    impersonation_rows = [r for r in audit_rows if r.mode == AdminAccessMode.IMPERSONATION]
    assert len(impersonation_rows) == 1
    row = impersonation_rows[0]
    assert row.admin_id == two_clients.admin_id
    assert row.target_user_id == two_clients.a_id
    assert row.token_id is not None
    assert row.reason == "support-impersonate-A"


@pytest.mark.integration
async def test_normal_session_after_admin_contexts_is_clean(
    two_clients: TwoClients,
) -> None:
    """Admin GUCs do not leak into the next request.

    Run both admin contexts back-to-back, then open a normal client
    session for B and assert RLS isolation still holds: B sees its own
    rows and not A's. The ``DISCARD ALL`` on pool checkin from WU1.5 is
    the safety net; ``SET LOCAL`` and transaction-scoped ``set_config``
    are the first layer.
    """
    async with admin_operator_session(
        two_clients.admin_id,
        engine=two_clients.async_engine,
    ) as session:
        await WatchlistsRepository(session).list_for()

    async with admin_impersonation_session(
        two_clients.admin_id,
        two_clients.a_id,
        engine=two_clients.async_engine,
    ) as session:
        await WatchlistsRepository(session).list_for()

    async with two_clients.session_for(two_clients.b_id) as session:
        ids = {w.id for w in await WatchlistsRepository(session).list_for()}
        impersonating = await session.execute(
            sqlalchemy.text("SELECT current_setting('app.impersonating_admin_id', true)")
        )

    assert ids == {two_clients.b_watchlist_id}
    # set_config with is_local=true + missing setting returns '' under
    # the `, true` (missing_ok=true) form. Either '' or NULL is fine —
    # both signal "no impersonation context bleed into this request".
    leaked = impersonating.scalar_one()
    assert leaked in ("", None), f"impersonating_admin_id leaked: {leaked!r}"
