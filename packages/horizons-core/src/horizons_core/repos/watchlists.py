"""``WatchlistsRepository`` and its DTO.

Owner-keyed private state. Reads rely on the session-bound
``app.user_id`` GUC and the ``watchlists_owner_select`` RLS policy.
Writes take an explicit ``*, user_id: UUID`` so the caller's ownership
claim is visible at the call site and the database's ``WITH CHECK``
predicate enforces the same equality.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy import update as sql_update

from horizons_core.db.models.watchlists import Watchlist

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class WatchlistDTO(BaseModel):
    """Serialisable view of a ``watchlists`` row.

    ``from_attributes=True`` is what lets ``model_validate`` read
    SQLAlchemy ORM attribute access (``row.id``) instead of dict access.
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID
    document_id: uuid.UUID
    name: str
    active: bool
    created_at: datetime


class WatchlistsRepository:
    """Reads and writes per-user watchlists under owner-keyed RLS.

    The session is injected at construction and the repo holds a
    reference for the duration of its caller's use. The repo never
    opens, commits, or closes the session — that is the
    ``session_for_user`` / ``get_session`` bracket's job.
    """

    dto_type: ClassVar[type[BaseModel]] = WatchlistDTO

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for(self) -> list[WatchlistDTO]:
        """Every *active* watchlist visible to the current session.

        RLS via the ``watchlists_owner_select`` policy filters cross-
        client rows; the repo adds ``active = true`` so soft-hidden
        rows (set inactive when an admin reduced the owning user's
        subscription scope) disappear from the client surface. Admin
        views use ``list_all_including_inactive_for_user`` instead.
        """
        rows = (
            (await self._session.execute(select(Watchlist).where(Watchlist.active.is_(True))))
            .scalars()
            .all()
        )
        return [WatchlistDTO.model_validate(r) for r in rows]

    async def list_all_including_inactive_for_user(self, user_id: uuid.UUID) -> list[WatchlistDTO]:
        """Every watchlist owned by ``user_id``, active or not.

        Used by admin paths that need the audit-trail view of a client's
        soft-hidden rows. The caller is expected to hold an
        ``admin_bypass`` session — under ``api_app`` RLS would narrow
        cross-user reads to zero rows.
        """
        rows = (
            (await self._session.execute(select(Watchlist).where(Watchlist.user_id == user_id)))
            .scalars()
            .all()
        )
        return [WatchlistDTO.model_validate(r) for r in rows]

    async def soft_hide_out_of_scope(
        self,
        *,
        user_id: uuid.UUID,
        in_scope_document_ids: set[uuid.UUID],
    ) -> int:
        """Set ``active=false`` on each active row whose document is no
        longer in the user's scope; return the number of rows touched.

        Idempotent: rows already inactive are not re-touched (the WHERE
        narrows to ``active = true``). The caller picks
        ``in_scope_document_ids`` by joining ``documents`` against the
        post-reduction scope; an empty set hides every active watchlist
        for the user.

        Requires UPDATE on ``watchlists``. The session is expected to
        run as ``admin_bypass`` — the WU4.5 migration grants UPDATE on
        that role for this code path.
        """
        stmt = (
            sql_update(Watchlist)
            .where(
                Watchlist.user_id == user_id,
                Watchlist.active.is_(True),
            )
            .values(active=False)
            .returning(Watchlist.id)
        )
        if in_scope_document_ids:
            stmt = stmt.where(Watchlist.document_id.not_in(in_scope_document_ids))
        result = await self._session.execute(stmt)
        return len(result.all())

    async def get_by_id(self, watchlist_id: uuid.UUID) -> WatchlistDTO | None:
        """Fetch one watchlist by primary key, or ``None``.

        ``None`` covers both "no such row" and "row belongs to another
        user" — the RLS predicate filters cross-user rows out of the
        result set, so the repo cannot distinguish, and on the wire we
        prefer 404 over 403 to avoid leaking row existence.
        """
        row = (
            await self._session.execute(select(Watchlist).where(Watchlist.id == watchlist_id))
        ).scalar_one_or_none()
        return WatchlistDTO.model_validate(row) if row is not None else None

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        document_id: uuid.UUID,
        name: str,
    ) -> WatchlistDTO:
        """Insert a watchlist owned by ``user_id`` for ``document_id``.

        The keyword-only ``user_id`` is required by the policy's
        ``WITH CHECK`` predicate and by call-site clarity — a write
        always names its owner. ``document_id`` carries the same
        clarity: the row is meaningless without it.

        Scope validation against the caller's subscription is the
        service layer's job; the database backs it with the
        ``watchlists_in_subscription_scope`` trigger.
        """
        row = Watchlist(user_id=user_id, document_id=document_id, name=name)
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return WatchlistDTO.model_validate(row)

    async def delete(self, *, user_id: uuid.UUID, watchlist_id: uuid.UUID) -> bool:
        """Delete the named watchlist; return whether anything matched.

        ``user_id`` belongs in the ``WHERE`` so the call site's
        ownership intent is documented; the RLS ``DELETE USING``
        predicate would also filter, so a row owned by a different
        user is a silent no-op and returns ``False`` either way.
        """
        deleted = await self._session.execute(
            sql_delete(Watchlist)
            .where(
                Watchlist.id == watchlist_id,
                Watchlist.user_id == user_id,
            )
            .returning(Watchlist.id)
        )
        return deleted.scalar_one_or_none() is not None
