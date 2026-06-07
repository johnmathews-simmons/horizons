"""``/v1/documents`` — browse documents in scope; open one with its clauses.

Same handler serves both clients and admins through
``session_for_request_or_admin``: clients see their subscription scope
(RLS-filtered under ``api_app``); admins see the full corpus under the
audited ``admin_bypass`` path. The wire shape is identical for both
roles — the only difference is how many rows come back.

The 404 on out-of-scope reads mirrors the primitives surface: a client
cannot distinguish "not found" from "not in your subscription," which
is intentional for cross-tenant privacy.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from horizons_core.core.auth import Principal
from horizons_core.repos.clauses import ClausesRepository
from horizons_core.repos.documents import DocumentsRepository
from horizons_core.repos.versions import DocumentVersionsRepository
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from horizons_api.deps import authenticated_user, session_for_request_or_admin

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


# ----- response models ----------------------------------------------------


class DocumentItem(BaseModel):
    """List-row shape: a document without its versions."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    jurisdiction: str
    sector: str
    lawstronaut_document_id: str
    title: str
    created_at: datetime


class DocumentPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[DocumentItem]
    total: int
    limit: int
    offset: int


class DocumentVersionItem(BaseModel):
    """Version row attached to a ``DocumentDetail``."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    version_label: str
    publication_date: datetime | None
    effective_date: datetime | None
    content_bytes: int
    created_at: datetime


class DocumentDetail(BaseModel):
    """Detail shape: a document plus the list of its in-scope versions."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    jurisdiction: str
    sector: str
    lawstronaut_document_id: str
    title: str
    created_at: datetime
    versions: list[DocumentVersionItem]


class ClauseItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    clause_uid: uuid.UUID
    clause_path: str
    text_content: str
    heading_text: str | None = None
    ord: int


class ClauseBundle(BaseModel):
    """Flat ordered list of clauses for a single version."""

    model_config = ConfigDict(frozen=True)

    document_id: uuid.UUID
    version_id: uuid.UUID
    version_label: str
    clauses: list[ClauseItem]


# ----- router -------------------------------------------------------------


router = APIRouter(prefix="/v1/documents", tags=["documents"])


@router.get("", response_model=DocumentPage)
async def list_documents(
    response: Response,
    _principal: Annotated[Principal, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(session_for_request_or_admin)],
    jurisdiction: Annotated[str | None, Query()] = None,
    sector: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentPage:
    _no_store(response)
    rows, total = await DocumentsRepository(session).list_filtered(
        jurisdiction=jurisdiction,
        sector=sector,
        search=search,
        limit=limit,
        offset=offset,
    )
    return DocumentPage(
        items=[
            DocumentItem(
                id=r.id,
                jurisdiction=r.jurisdiction,
                sector=r.sector,
                lawstronaut_document_id=r.lawstronaut_document_id,
                title=r.title,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    response: Response,
    document_id: uuid.UUID,
    _principal: Annotated[Principal, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(session_for_request_or_admin)],
) -> DocumentDetail:
    _no_store(response)
    document = await DocumentsRepository(session).get_by_id(document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="document not found",
        )
    versions = await DocumentVersionsRepository(session).list_for_document(document_id)
    return DocumentDetail(
        id=document.id,
        jurisdiction=document.jurisdiction,
        sector=document.sector,
        lawstronaut_document_id=document.lawstronaut_document_id,
        title=document.title,
        created_at=document.created_at,
        versions=[
            DocumentVersionItem(
                id=v.id,
                version_label=v.version_label,
                publication_date=v.publication_date,
                effective_date=v.effective_date,
                content_bytes=v.content_bytes,
                created_at=v.created_at,
            )
            for v in versions
        ],
    )


@router.get(
    "/{document_id}/versions/{version_label}/clauses",
    response_model=ClauseBundle,
)
async def get_clauses(
    response: Response,
    document_id: uuid.UUID,
    version_label: str,
    _principal: Annotated[Principal, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(session_for_request_or_admin)],
) -> ClauseBundle:
    _no_store(response)
    version = await DocumentVersionsRepository(session).get_by_label(document_id, version_label)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="version not found",
        )
    clauses = await ClausesRepository(session).list_for_version(version.id)
    return ClauseBundle(
        document_id=document_id,
        version_id=version.id,
        version_label=version.version_label,
        clauses=[
            ClauseItem(
                id=c.id,
                clause_uid=c.clause_uid,
                clause_path=c.clause_path,
                text_content=c.text_content,
                heading_text=c.heading_text,
                ord=c.ord,
            )
            for c in clauses
        ],
    )
