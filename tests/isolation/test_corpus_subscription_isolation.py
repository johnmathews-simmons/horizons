"""WU1.7 gate test #2 — corpus subscription scoping via the corpus repos.

The subscription-scope axis from the design doc must hold through the
full stack: session bracket → role switch → ``current_scope()`` →
``*_in_scope`` RLS policy → repository.

A holds a UK / BANKING subscription, B holds an EU / INSURANCE one.
Each scope has a document chain (document + version + clause). No row
should cross.

**This file is the gate.** Same severity as the private-state gate;
both axes are independently load-bearing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from horizons_core.repos.clauses import ClausesRepository
from horizons_core.repos.documents import DocumentsRepository
from horizons_core.repos.versions import DocumentVersionsRepository

if TYPE_CHECKING:
    from tests.isolation.conftest import TwoClients


@pytest.mark.integration
async def test_b_cannot_see_a_document_in_list_all(
    two_clients: TwoClients,
) -> None:
    async with two_clients.session_for(two_clients.b_id) as session:
        repo = DocumentsRepository(session)
        ids = {d.id for d in await repo.list_all()}
    assert two_clients.a_document_id not in ids
    assert two_clients.b_document_id in ids


@pytest.mark.integration
async def test_a_cannot_see_b_document_in_list_all(
    two_clients: TwoClients,
) -> None:
    async with two_clients.session_for(two_clients.a_id) as session:
        repo = DocumentsRepository(session)
        ids = {d.id for d in await repo.list_all()}
    assert two_clients.b_document_id not in ids
    assert two_clients.a_document_id in ids


@pytest.mark.integration
async def test_b_get_by_id_for_a_document_returns_none(
    two_clients: TwoClients,
) -> None:
    """Same 404-not-403 contract as the private-state axis."""
    async with two_clients.session_for(two_clients.b_id) as session:
        repo = DocumentsRepository(session)
        result = await repo.get_by_id(two_clients.a_document_id)
    assert result is None


@pytest.mark.integration
async def test_b_version_for_a_document_returns_none_or_empty(
    two_clients: TwoClients,
) -> None:
    """RLS on ``document_versions`` walks the FK chain to ``documents``.

    Versions of an out-of-scope document are filtered out — both the
    ``get_by_id`` and ``list_for_document`` paths.
    """
    async with two_clients.session_for(two_clients.b_id) as session:
        repo = DocumentVersionsRepository(session)
        direct = await repo.get_by_id(two_clients.a_version_id)
        listed = await repo.list_for_document(two_clients.a_document_id)
    assert direct is None
    assert listed == []


@pytest.mark.integration
async def test_b_clause_for_a_version_returns_none_or_empty(
    two_clients: TwoClients,
) -> None:
    """RLS on ``clauses`` walks two FKs up to ``documents``."""
    async with two_clients.session_for(two_clients.b_id) as session:
        repo = ClausesRepository(session)
        direct = await repo.get_by_id(two_clients.a_clause_id)
        listed = await repo.list_for_version(two_clients.a_version_id)
    assert direct is None
    assert listed == []


@pytest.mark.integration
async def test_admin_bypass_sees_both_scopes_of_documents(
    two_clients: TwoClients,
) -> None:
    """admin_bypass is the audited escape hatch across the corpus too."""
    async with two_clients.admin_session() as session:
        repo = DocumentsRepository(session)
        ids = {d.id for d in await repo.list_all()}
    assert two_clients.a_document_id in ids
    assert two_clients.b_document_id in ids
