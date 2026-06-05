"""WU1.7 gate test #1 — cross-client privacy via ``WatchlistsRepository``.

The cross-client privacy axis from the design doc must hold through
the full stack: session bracket → role switch → RLS policy →
repository. These assertions prove it.

The fixture ``two_clients`` seeds A with a UK / BANKING subscription and
B with an EU / INSURANCE subscription, each with their own watchlist.
Every test in this file asserts at the repository layer, not at raw
SQL, so the repository is also part of the contract under test.

**This file is the gate.** No Track 2 / 3 / 4 work merges while it is
red.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from horizons_core.repos.watchlists import WatchlistsRepository

if TYPE_CHECKING:
    from tests.isolation.conftest import TwoClients


@pytest.mark.integration
async def test_b_cannot_see_a_watchlist_in_list_for(
    two_clients: TwoClients,
) -> None:
    """A's watchlist is invisible to B's ``list_for``."""
    async with two_clients.session_for(two_clients.b_id) as session:
        repo = WatchlistsRepository(session)
        ids = {w.id for w in await repo.list_for()}
    assert two_clients.a_watchlist_id not in ids
    assert two_clients.b_watchlist_id in ids


@pytest.mark.integration
async def test_a_cannot_see_b_watchlist_in_list_for(
    two_clients: TwoClients,
) -> None:
    """Symmetric: B's watchlist is invisible to A's ``list_for``."""
    async with two_clients.session_for(two_clients.a_id) as session:
        repo = WatchlistsRepository(session)
        ids = {w.id for w in await repo.list_for()}
    assert two_clients.b_watchlist_id not in ids
    assert two_clients.a_watchlist_id in ids


@pytest.mark.integration
async def test_b_get_by_id_for_a_watchlist_returns_none_not_403(
    two_clients: TwoClients,
) -> None:
    """A's watchlist must look like 404 (None) to B, never 403.

    A 403 would leak the row's existence to a third party. RLS filters
    the row out before the repo sees it, so ``get_by_id`` returns
    ``None`` and the API layer maps it to 404.
    """
    async with two_clients.session_for(two_clients.b_id) as session:
        repo = WatchlistsRepository(session)
        result = await repo.get_by_id(two_clients.a_watchlist_id)
    assert result is None


@pytest.mark.integration
async def test_b_delete_of_a_watchlist_returns_false(
    two_clients: TwoClients,
) -> None:
    """B's attempt to delete A's row is a silent no-op.

    Returns ``False`` (nothing deleted), does not raise, and A's row
    survives — checked from A's session.
    """
    async with two_clients.session_for(two_clients.b_id) as session:
        repo = WatchlistsRepository(session)
        removed = await repo.delete(
            user_id=two_clients.b_id,
            watchlist_id=two_clients.a_watchlist_id,
        )
    assert removed is False

    # Verify A's row is still there.
    async with two_clients.session_for(two_clients.a_id) as session:
        repo = WatchlistsRepository(session)
        survivor = await repo.get_by_id(two_clients.a_watchlist_id)
    assert survivor is not None


@pytest.mark.integration
async def test_admin_bypass_sees_both_watchlists(
    two_clients: TwoClients,
) -> None:
    """``admin_bypass`` is the audited escape hatch — sees every row.

    Note: ``WatchlistsRepository.list_for`` is unchanged under admin —
    it issues the same ``SELECT`` and lets the role decide what comes
    back. BYPASSRLS does the rest.
    """
    async with two_clients.admin_session() as session:
        repo = WatchlistsRepository(session)
        ids = {w.id for w in await repo.list_for()}
    assert two_clients.a_watchlist_id in ids
    assert two_clients.b_watchlist_id in ids
