"""Subscription summary + scope helpers used by the API.

Two operations that share the bound ``app.user_id`` to render the
caller's view of their entitlement:

- ``current_scope_pairs(session)`` — the set of ``(jurisdiction, sector)``
  the caller can read. Reads through ``app_private.current_scope()``,
  which raises if ``app.user_id`` is unset (the correct signal for
  "session opened wrong" rather than "user has no subscription").
- ``current_subscription_summary(session)`` — wraps the scope set plus
  the caller's currently-active subscription rows for ``/v1/me``.

Both call the SQLAlchemy expression / ORM layer rather than
``sqlalchemy.text()`` so the architectural-test carve-out around raw
SQL (``db/session.py`` only) stays intact.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic needs runtime resolution
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, String, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import UUID

from horizons_core.db.models.subscriptions import Subscription

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_APP_USER_ID = cast(func.current_setting("app.user_id"), UUID(as_uuid=True))


class ScopePair(BaseModel):
    """One (jurisdiction, sector) the caller is entitled to read."""

    model_config = ConfigDict(frozen=True)

    jurisdiction: str
    sector: str


class SubscriptionRowDTO(BaseModel):
    """A row from ``subscriptions`` for the summary response."""

    model_config = ConfigDict(frozen=True)

    valid_from: datetime
    valid_to: datetime | None


class SubscriptionSummaryDTO(BaseModel):
    """Composite response for ``/v1/me`` 's ``subscription`` field."""

    model_config = ConfigDict(frozen=True)

    scope: list[ScopePair]
    active_subscriptions: list[SubscriptionRowDTO]


async def current_scope_pairs(session: AsyncSession) -> set[tuple[str, str]]:
    """Return the ``(jurisdiction, sector)`` set bound to ``app.user_id``.

    Uses ``func`` to render
    ``SELECT jurisdiction, sector FROM app_private.current_scope()`` —
    SQLAlchemy's ``table_valued`` shapes the call as a relation with two
    columns so the result rows are directly iterable.
    """
    cs = (
        func.app_private.current_scope()
        .table_valued(Column("jurisdiction", String), Column("sector", String))
        .alias("cs")
    )
    rows = (await session.execute(select(cs.c.jurisdiction, cs.c.sector))).all()
    return {(r.jurisdiction, r.sector) for r in rows}


async def current_subscription_summary(
    session: AsyncSession,
) -> SubscriptionSummaryDTO:
    """Compose the ``/v1/me`` subscription field for the bound user."""
    scope_rows = await current_scope_pairs(session)
    scope = sorted(
        (ScopePair(jurisdiction=j, sector=s) for (j, s) in scope_rows),
        key=lambda p: (p.jurisdiction, p.sector),
    )

    stmt = (
        select(Subscription.valid_from, Subscription.valid_to)
        .where(
            and_(
                Subscription.user_id == _APP_USER_ID,
                Subscription.valid_from <= func.now(),
                or_(
                    Subscription.valid_to.is_(None),
                    Subscription.valid_to > func.now(),
                ),
            )
        )
        .order_by(Subscription.valid_from)
    )
    rows = (await session.execute(stmt)).all()
    subs = [SubscriptionRowDTO(valid_from=r.valid_from, valid_to=r.valid_to) for r in rows]
    return SubscriptionSummaryDTO(scope=scope, active_subscriptions=subs)
