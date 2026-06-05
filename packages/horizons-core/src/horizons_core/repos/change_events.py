"""``ChangeEventsRepository`` — scope-aware reads of ``change_events``.

The single corpus read surface that ``api_app`` uses to answer the
three primitives (discovery / temporal / differential) at corpus /
document / clause scope. RLS narrows rows to the caller's
subscription scope via the ``change_events_in_scope`` policy
(WU3.4 migration 0010). The repo never re-derives scope.

Cursors are opaque base64-encoded JSON of the last
``(detected_at, id)`` pair returned, decoded back into a tuple-keyset
WHERE predicate against the ``idx_change_events_scope`` composite
index. Clients must not parse or generate them.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, select

from horizons_core.db.models.change_events import ChangeEvent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql import Select


# ----- scope discriminator (Pydantic-free; the API layer owns wire shape) ---


@dataclass(frozen=True, slots=True)
class CorpusScope:
    """Every visible change event, narrowed by optional filters."""

    kind: ClassVar[Literal["corpus"]] = "corpus"
    jurisdiction: str | None = None
    sector: str | None = None
    since: datetime | None = None
    until: datetime | None = None


@dataclass(frozen=True, slots=True)
class DocumentScope:
    """Every visible change event on one document."""

    kind: ClassVar[Literal["document"]] = "document"
    document_id: uuid.UUID = uuid.UUID(int=0)


@dataclass(frozen=True, slots=True)
class ClauseScope:
    """Every visible change event touching one ``clause_uid``.

    ``document_id`` narrows further if supplied — defensive against the
    (theoretical) case of the same ``clause_uid`` reappearing in
    another document.
    """

    kind: ClassVar[Literal["clause"]] = "clause"
    clause_uid: uuid.UUID = uuid.UUID(int=0)
    document_id: uuid.UUID | None = None


ChangeEventScope = CorpusScope | DocumentScope | ClauseScope


# ----- DTO -----------------------------------------------------------------


class ChangeEventDTO(BaseModel):
    """Serialisable view of a ``change_events`` row.

    Every column is here. The API layer chooses which subset to project
    onto the wire for each primitive (e.g. discovery drops the ``_text``
    fields; temporal drops both ``_text`` and ``_path``).
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

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


# ----- cursor encoding -----------------------------------------------------

# The cursor encodes the (detected_at, id) of the last row returned on
# the previous page. Decoded back into a tuple-keyset WHERE predicate
# that walks the composite index in (detected_at DESC, id DESC) order.

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class CursorError(ValueError):
    """Raised when an opaque cursor cannot be decoded."""


def encode_cursor(detected_at: datetime, row_id: int) -> str:
    """Opaque-encode a keyset position. Clients treat this as a blob."""
    payload = json.dumps(
        {"dt": detected_at.isoformat(), "id": row_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    """Decode a previously-emitted cursor. Raises ``CursorError`` on bad input."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        body = json.loads(raw.decode("utf-8"))
        return datetime.fromisoformat(body["dt"]), int(body["id"])
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        msg = "cursor is not a valid pagination token"
        raise CursorError(msg) from exc


# ----- repository ----------------------------------------------------------


class ChangeEventsRepository:
    """Reads of ``change_events`` for the three primitives.

    RLS narrows to subscription scope; this repo only adds primitive-
    and scope-specific predicates plus opaque-cursor keyset pagination.
    Writes are the ingestion worker's surface (separate role + policy).
    """

    dto_type: ClassVar[type[BaseModel]] = ChangeEventDTO

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_discovery(
        self,
        scope: ChangeEventScope,
        *,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> tuple[list[ChangeEventDTO], str | None]:
        """Recent change events for the scope.

        Returns ``(items, next_cursor)``. ``next_cursor`` is ``None``
        when the page is the last one. ``limit`` is capped at
        ``MAX_LIMIT``; values below 1 raise ``ValueError``.

        The composite index ``(jurisdiction, sector, detected_at,
        effective_date)`` answers corpus-scope queries without a sort;
        ``(document_id, detected_at)`` answers document-scope. Clause
        scope falls back to a row scan plus the RLS predicate — in
        practice this is bounded by the per-clause event count (a
        handful) and is acceptable without an extra index.
        """
        return await self._fetch_page(scope, limit, cursor)

    async def timeline(
        self,
        scope: ChangeEventScope,
        *,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> tuple[list[ChangeEventDTO], str | None]:
        """When did things change in this scope.

        Same underlying read as ``list_discovery``; the API layer
        projects different fields onto the wire (timestamps and the
        change identity, no path / body / confidence). Separate method
        so future per-primitive optimisations (e.g. selecting only the
        timestamp columns) can land without callers changing.
        """
        return await self._fetch_page(scope, limit, cursor)

    async def _fetch_page(
        self,
        scope: ChangeEventScope,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[ChangeEventDTO], str | None]:
        normalised_limit = _normalise_limit(limit)
        stmt = self._base_stmt(scope, cursor).limit(normalised_limit + 1)
        rows = (await self._session.execute(stmt)).scalars().all()
        return _paginate(rows, normalised_limit)

    def _base_stmt(
        self,
        scope: ChangeEventScope,
        cursor: str | None,
    ) -> Select[tuple[ChangeEvent]]:
        stmt: Select[tuple[ChangeEvent]] = select(ChangeEvent)
        stmt = _apply_scope(stmt, scope)
        stmt = _apply_cursor(stmt, cursor)
        return stmt.order_by(ChangeEvent.detected_at.desc(), ChangeEvent.id.desc())


def _normalise_limit(limit: int) -> int:
    if limit < 1:
        msg = "limit must be >= 1"
        raise ValueError(msg)
    return min(limit, MAX_LIMIT)


def _apply_scope(
    stmt: Select[tuple[ChangeEvent]],
    scope: ChangeEventScope,
) -> Select[tuple[ChangeEvent]]:
    if isinstance(scope, CorpusScope):
        if scope.jurisdiction is not None:
            stmt = stmt.where(ChangeEvent.jurisdiction == scope.jurisdiction)
        if scope.sector is not None:
            stmt = stmt.where(ChangeEvent.sector == scope.sector)
        if scope.since is not None:
            stmt = stmt.where(ChangeEvent.detected_at >= scope.since)
        if scope.until is not None:
            stmt = stmt.where(ChangeEvent.detected_at < scope.until)
        return stmt
    if isinstance(scope, DocumentScope):
        return stmt.where(ChangeEvent.document_id == scope.document_id)
    # ClauseScope: the same uid can appear as before_ or after_ on the
    # diff boundary depending on change_type (ADDED has only after_,
    # REMOVED has only before_, etc.). Either matching is "an event
    # touching this clause uid".
    predicate = or_(
        ChangeEvent.before_clause_uid == scope.clause_uid,
        ChangeEvent.after_clause_uid == scope.clause_uid,
    )
    if scope.document_id is not None:
        predicate = and_(predicate, ChangeEvent.document_id == scope.document_id)
    return stmt.where(predicate)


def _apply_cursor(
    stmt: Select[tuple[ChangeEvent]],
    cursor: str | None,
) -> Select[tuple[ChangeEvent]]:
    if cursor is None:
        return stmt
    cursor_dt, cursor_id = decode_cursor(cursor)
    # Tuple-keyset: rows strictly earlier than (cursor_dt, cursor_id)
    # on the (detected_at DESC, id DESC) ordering. Expanded out so the
    # planner can use the composite index directly without resolving
    # row-value comparisons.
    return stmt.where(
        or_(
            ChangeEvent.detected_at < cursor_dt,
            and_(
                ChangeEvent.detected_at == cursor_dt,
                ChangeEvent.id < cursor_id,
            ),
        )
    )


def _paginate(
    rows: list[ChangeEvent] | object,
    limit: int,
) -> tuple[list[ChangeEventDTO], str | None]:
    # SQLAlchemy's .all() returns a Sequence; we treat it as a list.
    materialised: list[ChangeEvent] = list(rows)  # type: ignore[arg-type]
    has_more = len(materialised) > limit
    page = materialised[:limit]
    next_cursor: str | None = None
    if has_more:
        last = page[-1]
        next_cursor = encode_cursor(last.detected_at, last.id)
    return [ChangeEventDTO.model_validate(r) for r in page], next_cursor
