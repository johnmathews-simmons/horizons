"""``/v1/discovery``, ``/v1/temporal``, ``/v1/differential`` — the three primitives.

All three accept the same ``scope`` discriminator (``corpus`` /
``document`` / ``clause``) with scope-specific filter parameters; the
response shape differs by primitive. See
``docs/api/horizons-primitives.md`` for the wire contract.

Discovery and Temporal project the ``change_events`` row onto the
identity / location fields only — no clause body text. Differential
projects the body text too when ``include_content`` resolves to
``true`` (the default at document / clause scope, an opt-in capped at
``limit <= 10`` at corpus scope).

RLS narrows visible rows to the caller's subscription scope; the
service layer adds no scope check of its own beyond the explicit
discriminator predicates.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from horizons_core.core.auth import Principal
from horizons_core.repos.change_events import (
    MAX_LIMIT,
    ChangeEventDTO,
    ChangeEventScope,
    ChangeEventsRepository,
    ClauseScope,
    CorpusScope,
    CursorError,
    DocumentScope,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from horizons_api.deps import authenticated_user, session_for_request

ScopeKind = Literal["corpus", "document", "clause"]

CORPUS_INCLUDE_CONTENT_MAX_LIMIT = 10


# ----- response models ----------------------------------------------------


class DiscoveryItem(BaseModel):
    """Discovery wire shape — identity + location, no body text."""

    model_config = ConfigDict(frozen=True)

    id: int
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    jurisdiction: str
    sector: str
    change_type: str
    before_clause_uid: uuid.UUID | None
    after_clause_uid: uuid.UUID | None
    before_path: str | None
    after_path: str | None
    alignment_confidence: float
    detected_at: datetime
    effective_date: datetime | None


class TemporalItem(BaseModel):
    """Temporal wire shape — when, where (by uid), what kind of change."""

    model_config = ConfigDict(frozen=True)

    id: int
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    clause_uid: uuid.UUID | None
    change_type: str
    detected_at: datetime
    effective_date: datetime | None


class DifferentialItem(BaseModel):
    """Differential wire shape — identity, location, optional body text."""

    model_config = ConfigDict(frozen=True)

    id: int
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    jurisdiction: str
    sector: str
    change_type: str
    before_clause_uid: uuid.UUID | None
    after_clause_uid: uuid.UUID | None
    before_path: str | None
    after_path: str | None
    before_text: str | None
    after_text: str | None
    alignment_confidence: float
    detected_at: datetime
    effective_date: datetime | None


class DiscoveryPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[DiscoveryItem]
    next_cursor: str | None = None
    has_more: bool = False


class TemporalPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[TemporalItem]
    next_cursor: str | None = None
    has_more: bool = False


class DifferentialPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[DifferentialItem]
    next_cursor: str | None = None
    has_more: bool = False


# ----- shared helpers -----------------------------------------------------


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _build_scope(
    scope: ScopeKind,
    *,
    jurisdiction: str | None,
    sector: str | None,
    since: datetime | None,
    until: datetime | None,
    document_id: uuid.UUID | None,
    clause_uid: uuid.UUID | None,
) -> ChangeEventScope:
    """Turn query-string discriminator + filters into a typed ``ChangeEventScope``.

    Invalid combinations (e.g. ``scope=document`` without
    ``document_id``) raise 422. The corpus filters are silently ignored
    on non-corpus scopes — the discriminator is the authority.
    """
    if scope == "corpus":
        return CorpusScope(
            jurisdiction=jurisdiction,
            sector=sector,
            since=since,
            until=until,
        )
    if scope == "document":
        if document_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="scope=document requires document_id",
            )
        return DocumentScope(document_id=document_id)
    # scope == "clause"
    if clause_uid is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="scope=clause requires clause_uid",
        )
    return ClauseScope(clause_uid=clause_uid, document_id=document_id)


def _validate_limit(limit: int) -> int:
    if limit < 1 or limit > MAX_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"limit must be between 1 and {MAX_LIMIT}",
        )
    return limit


async def _fetch_discovery(
    session: AsyncSession,
    *,
    scope: ChangeEventScope,
    limit: int,
    cursor: str | None,
) -> tuple[list[ChangeEventDTO], str | None]:
    try:
        return await ChangeEventsRepository(session).list_discovery(
            scope, limit=limit, cursor=cursor
        )
    except CursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


async def _fetch_timeline(
    session: AsyncSession,
    *,
    scope: ChangeEventScope,
    limit: int,
    cursor: str | None,
) -> tuple[list[ChangeEventDTO], str | None]:
    try:
        return await ChangeEventsRepository(session).timeline(scope, limit=limit, cursor=cursor)
    except CursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


# ----- routers ------------------------------------------------------------


discovery_router = APIRouter(prefix="/v1/discovery", tags=["discovery"])
temporal_router = APIRouter(prefix="/v1/temporal", tags=["temporal"])
differential_router = APIRouter(prefix="/v1/differential", tags=["differential"])


@discovery_router.get("", response_model=DiscoveryPage)
async def discovery(  # noqa: PLR0913 — every parameter maps to a wire field
    response: Response,
    _principal: Annotated[Principal, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(session_for_request)],
    scope: Annotated[ScopeKind, Query()] = "corpus",
    jurisdiction: Annotated[str | None, Query()] = None,
    sector: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    document_id: Annotated[uuid.UUID | None, Query()] = None,
    clause_uid: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query()] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> DiscoveryPage:
    """Recent change events for the scope. No body text."""
    _no_store(response)
    bounded_limit = _validate_limit(limit)
    typed_scope = _build_scope(
        scope,
        jurisdiction=jurisdiction,
        sector=sector,
        since=since,
        until=until,
        document_id=document_id,
        clause_uid=clause_uid,
    )
    rows, next_cursor = await _fetch_discovery(
        session, scope=typed_scope, limit=bounded_limit, cursor=cursor
    )
    return DiscoveryPage(
        items=[_to_discovery_item(r) for r in rows],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
    )


def _to_discovery_item(dto: ChangeEventDTO) -> DiscoveryItem:
    return DiscoveryItem(
        id=dto.id,
        document_id=dto.document_id,
        document_version_id=dto.document_version_id,
        jurisdiction=dto.jurisdiction,
        sector=dto.sector,
        change_type=dto.change_type,
        before_clause_uid=dto.before_clause_uid,
        after_clause_uid=dto.after_clause_uid,
        before_path=dto.before_path,
        after_path=dto.after_path,
        alignment_confidence=dto.alignment_confidence,
        detected_at=dto.detected_at,
        effective_date=dto.effective_date,
    )


@temporal_router.get("", response_model=TemporalPage)
async def temporal(  # noqa: PLR0913 — every parameter maps to a wire field
    response: Response,
    _principal: Annotated[Principal, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(session_for_request)],
    scope: Annotated[ScopeKind, Query()] = "corpus",
    jurisdiction: Annotated[str | None, Query()] = None,
    sector: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    document_id: Annotated[uuid.UUID | None, Query()] = None,
    clause_uid: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query()] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> TemporalPage:
    """When change events happened in the scope. No body text, no path."""
    _no_store(response)
    bounded_limit = _validate_limit(limit)
    typed_scope = _build_scope(
        scope,
        jurisdiction=jurisdiction,
        sector=sector,
        since=since,
        until=until,
        document_id=document_id,
        clause_uid=clause_uid,
    )
    rows, next_cursor = await _fetch_timeline(
        session, scope=typed_scope, limit=bounded_limit, cursor=cursor
    )
    return TemporalPage(
        items=[_to_temporal_item(r) for r in rows],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
    )


def _to_temporal_item(dto: ChangeEventDTO) -> TemporalItem:
    # For ADDED / MODIFIED / MOVED the clause's current identity is the
    # after_clause_uid. REMOVED has only before_clause_uid (the row
    # that's gone in the new version). Project the right side onto the
    # wire so a clause-scoped temporal query returns one uid per event,
    # not two.
    clause_uid = dto.after_clause_uid if dto.change_type != "REMOVED" else dto.before_clause_uid
    return TemporalItem(
        id=dto.id,
        document_id=dto.document_id,
        document_version_id=dto.document_version_id,
        clause_uid=clause_uid,
        change_type=dto.change_type,
        detected_at=dto.detected_at,
        effective_date=dto.effective_date,
    )
