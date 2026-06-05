"""``SubscriptionsRepository`` and DTOs.

Admin-write surface for the tenancy ledger. Three operations the WU4.5
admin endpoints compose:

- ``list_for_user(user_id)`` — every subscription the target owns plus
  its (active + soft-deleted) scope rows. Used by the admin GET.
- ``create_for_user(user_id, scopes, valid_from?)`` — insert one new
  ``subscriptions`` row + one ``subscription_scopes`` row per
  (jurisdiction, sector). Used by the admin POST.
- ``add_scopes(subscription_id, scopes)`` /
  ``soft_delete_scopes(subscription_id, scopes, now)`` — append-only
  scope evolution. Used by the admin PATCH.

The repo never opens, commits, or closes the session. The route's
admin context manager
(``horizons_core.core.auth.admin.admin_operator_session``) handles
that — the session yields under the ``admin_bypass`` role with
BYPASSRLS, which is what lets the admin write to a target user's
ledger.

Append-only discipline: scope-row UPDATE is allowed by the WU4.5
trigger only when ``valid_to`` moves NULL → timestamp with every other
column unchanged. Any attempt to rewrite a scope row is a bug — the
caller adds new scope rows or soft-deletes existing ones.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, select, tuple_
from sqlalchemy import update as sql_update

from horizons_core.db.models.subscriptions import Subscription, SubscriptionScope

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SubscriptionScopeDTO(BaseModel):
    """Serialisable view of a ``subscription_scopes`` row."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    subscription_id: uuid.UUID
    jurisdiction: str
    sector: str
    valid_to: datetime | None


class SubscriptionDTO(BaseModel):
    """Serialisable view of a ``subscriptions`` row + its scopes."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID
    valid_from: datetime
    valid_to: datetime | None
    created_at: datetime
    scopes: list[SubscriptionScopeDTO]


class SubscriptionsRepository:
    """Admin-scoped reads and writes on the subscription ledger."""

    dto_type: ClassVar[type[BaseModel]] = SubscriptionDTO

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(self, user_id: uuid.UUID) -> list[SubscriptionDTO]:
        """Every subscription owned by ``user_id`` plus its scope rows.

        Returns subscriptions newest-first by ``valid_from``. Scope rows
        include soft-deleted ones (``valid_to`` set) so the admin
        surface can render history.
        """
        sub_rows = (
            (
                await self._session.execute(
                    select(Subscription)
                    .where(Subscription.user_id == user_id)
                    .order_by(Subscription.valid_from.desc())
                )
            )
            .scalars()
            .all()
        )
        if not sub_rows:
            return []

        sub_ids = [s.id for s in sub_rows]
        scope_rows = (
            (
                await self._session.execute(
                    select(SubscriptionScope).where(SubscriptionScope.subscription_id.in_(sub_ids))
                )
            )
            .scalars()
            .all()
        )
        scopes_by_sub: dict[uuid.UUID, list[SubscriptionScopeDTO]] = {sid: [] for sid in sub_ids}
        for row in scope_rows:
            scopes_by_sub[row.subscription_id].append(SubscriptionScopeDTO.model_validate(row))

        out: list[SubscriptionDTO] = []
        for sub in sub_rows:
            out.append(
                SubscriptionDTO(
                    id=sub.id,
                    user_id=sub.user_id,
                    valid_from=sub.valid_from,
                    valid_to=sub.valid_to,
                    created_at=sub.created_at,
                    scopes=sorted(
                        scopes_by_sub[sub.id],
                        key=lambda s: (s.jurisdiction, s.sector),
                    ),
                )
            )
        return out

    async def get_by_id(self, subscription_id: uuid.UUID) -> SubscriptionDTO | None:
        """One subscription + its scopes, or ``None`` if absent."""
        sub = (
            await self._session.execute(
                select(Subscription).where(Subscription.id == subscription_id)
            )
        ).scalar_one_or_none()
        if sub is None:
            return None
        scopes = (
            (
                await self._session.execute(
                    select(SubscriptionScope).where(
                        SubscriptionScope.subscription_id == subscription_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return SubscriptionDTO(
            id=sub.id,
            user_id=sub.user_id,
            valid_from=sub.valid_from,
            valid_to=sub.valid_to,
            created_at=sub.created_at,
            scopes=sorted(
                (SubscriptionScopeDTO.model_validate(s) for s in scopes),
                key=lambda s: (s.jurisdiction, s.sector),
            ),
        )

    async def create_for_user(
        self,
        *,
        user_id: uuid.UUID,
        valid_from: datetime,
        scopes: list[tuple[str, str]],
    ) -> SubscriptionDTO:
        """Insert one subscription + one row per ``(jurisdiction, sector)``.

        Returns the new subscription's DTO with its scope rows. The
        caller is responsible for ensuring the scope list is non-empty
        — an empty subscription is legal at the schema level but useless
        for the client; the admin route rejects it with 422.
        """
        sub = Subscription(user_id=user_id, valid_from=valid_from)
        self._session.add(sub)
        await self._session.flush()
        await self._session.refresh(sub)

        for jurisdiction, sector in scopes:
            self._session.add(
                SubscriptionScope(
                    subscription_id=sub.id,
                    jurisdiction=jurisdiction,
                    sector=sector,
                )
            )
        await self._session.flush()

        result = await self.get_by_id(sub.id)
        assert result is not None  # we just inserted it
        return result

    async def add_scopes(
        self,
        *,
        subscription_id: uuid.UUID,
        scopes: list[tuple[str, str]],
    ) -> int:
        """Insert each ``(jurisdiction, sector)`` for ``subscription_id``.

        Idempotency: if any of the requested pairs already exists (active
        or soft-deleted), the PK conflict is the caller's signal — the
        route layer pre-checks before calling and returns 422.

        Returns the number of rows added.
        """
        added = 0
        for jurisdiction, sector in scopes:
            self._session.add(
                SubscriptionScope(
                    subscription_id=subscription_id,
                    jurisdiction=jurisdiction,
                    sector=sector,
                )
            )
            added += 1
        await self._session.flush()
        return added

    async def soft_delete_scopes(
        self,
        *,
        subscription_id: uuid.UUID,
        scopes: list[tuple[str, str]],
        ended_at: datetime,
    ) -> int:
        """Mark each ``(jurisdiction, sector)`` as ended at ``ended_at``.

        Append-only: the row stays, only ``valid_to`` moves. The WU4.5
        trigger enforces the same shape — any UPDATE other than
        ``valid_to`` NULL → timestamp raises.

        Idempotent: only rows currently active (``valid_to IS NULL``)
        are touched; previously ended rows are skipped silently.

        Returns the number of rows soft-deleted.
        """
        if not scopes:
            return 0
        pairs = [(j, s) for (j, s) in scopes]
        stmt = (
            sql_update(SubscriptionScope)
            .where(
                and_(
                    SubscriptionScope.subscription_id == subscription_id,
                    SubscriptionScope.valid_to.is_(None),
                    tuple_(SubscriptionScope.jurisdiction, SubscriptionScope.sector).in_(pairs),
                )
            )
            .values(valid_to=ended_at)
            .returning(SubscriptionScope.subscription_id)
        )
        result = await self._session.execute(stmt)
        return len(result.all())

    async def active_scope_documents(
        self,
        *,
        user_id: uuid.UUID,
    ) -> set[uuid.UUID]:
        """Document ids visible under the user's *current* scope.

        Used by the reduction path to compute which watchlists to
        soft-hide. The query is a join from the user's active
        subscription scope rows to ``documents``; the WU4.5 update to
        ``current_scope()`` already filters by ``valid_to``, so the
        active-scope set here matches what the client's session would
        see after the reduction lands.

        We reach the result via the SQLAlchemy expression layer rather
        than ``app_private.current_scope()`` (which requires a bound
        ``app.user_id``) because admin sessions are bound to the admin's
        id, not the target's.
        """
        from horizons_core.db.models.documents import Document

        stmt = (
            select(Document.id)
            .join(
                SubscriptionScope,
                and_(
                    SubscriptionScope.jurisdiction == Document.jurisdiction,
                    SubscriptionScope.sector == Document.sector,
                    SubscriptionScope.valid_to.is_(None),
                ),
            )
            .join(
                Subscription,
                Subscription.id == SubscriptionScope.subscription_id,
            )
            .where(
                Subscription.user_id == user_id,
                Subscription.valid_to.is_(None),
            )
            .distinct()
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return set(rows)
