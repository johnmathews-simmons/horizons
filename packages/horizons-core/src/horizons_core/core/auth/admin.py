"""Admin operator and impersonation session brackets.

Two async context managers wrap ``session_for_user`` with the role
switch, audit-log write, and (impersonation only) the
``app.impersonating_admin_id`` GUC binding that distinguish admin code
paths from a normal client request.

- ``admin_operator_session(admin_id, *, engine=None, reason=None)``
  uses the ``admin_bypass`` role (BYPASSRLS). The session reads every
  row in every tenant. The audit row's ``target_user_id`` is ``None``
  and the database CHECK constraint enforces that consistency.

- ``admin_impersonation_session(admin_id, target_user_id, *, engine=None,
  reason=None)`` uses the ``api_app`` role under the target user's
  ``app.user_id``. RLS fires exactly as it would for a real client
  request from the target. The admin's id is captured in
  ``app.impersonating_admin_id`` so downstream observability can
  distinguish impersonated traffic from a direct client request.

Audit semantics. The audit row is written in its **own** transaction
that commits before the working session is yielded — if the caller's
body raises and rolls the working session back, the audit row still
persists. This is the only correct semantics for an admin-access trail:
the elevation happened the moment the row was issued, regardless of
what the body went on to do.

Both context managers mint a placeholder ``token_id`` per session and
record it in the audit row. Track 4 replaces the placeholder with the
real JWT id when the token-mint seam lands; the column itself stays
nullable so backfill / migration shape does not change. See
``db/rls.md`` §Admin code paths for the architecture.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from horizons_core.db.models.admin_access_log import AdminAccessMode
from horizons_core.db.session import (
    bind_impersonation_admin_id,
    get_engine,
    session_for_user,
    set_local_role,
)
from horizons_core.repos.admin_access_log import AdminAccessLogRepository

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


async def _record_audit_row(
    engine: AsyncEngine,
    *,
    mode: AdminAccessMode,
    admin_id: uuid.UUID,
    target_user_id: uuid.UUID | None,
    token_id: uuid.UUID,
    reason: str | None,
) -> None:
    """Write one audit row in its own committed transaction.

    Opens a short-lived ``session_for_user`` bracket bound to
    ``admin_id`` and assumes ``admin_bypass`` so the INSERT succeeds
    regardless of what role the caller's working session will assume.
    On exit the bracket commits, so the audit row persists even if the
    caller's subsequent working session rolls back.
    """
    async with session_for_user(engine, admin_id) as audit_session:
        await set_local_role(audit_session, "admin_bypass")
        await AdminAccessLogRepository(audit_session).record(
            mode=mode,
            admin_id=admin_id,
            target_user_id=target_user_id,
            token_id=token_id,
            reason=reason,
        )


@asynccontextmanager
async def admin_operator_session(
    admin_id: uuid.UUID,
    *,
    engine: AsyncEngine | None = None,
    reason: str | None = None,
) -> AsyncGenerator[AsyncSession]:
    """Yield a session bound to ``admin_id`` with ``admin_bypass`` assumed.

    The session reads every row in every tenant — the role attribute
    is the carve-out, no policy bypass is involved. The audit row is
    committed in its own transaction before the working session is
    yielded.

    ``engine`` defaults to the lazy module-level engine
    (``HORIZONS_DB_URL``). Tests inject their own.
    """
    eng = engine if engine is not None else get_engine()
    token_id = uuid.uuid4()
    await _record_audit_row(
        eng,
        mode=AdminAccessMode.OPERATOR,
        admin_id=admin_id,
        target_user_id=None,
        token_id=token_id,
        reason=reason,
    )
    async with session_for_user(eng, admin_id) as session:
        await set_local_role(session, "admin_bypass")
        yield session


@asynccontextmanager
async def admin_impersonation_session(
    admin_id: uuid.UUID,
    target_user_id: uuid.UUID,
    *,
    engine: AsyncEngine | None = None,
    reason: str | None = None,
) -> AsyncGenerator[AsyncSession]:
    """Yield a session that reads / writes as ``target_user_id`` would.

    ``session_for_user(engine, target_user_id)`` binds the target's
    ``app.user_id`` so the standard RLS predicates fire exactly as
    they would for a real client request from that user.
    ``bind_impersonation_admin_id`` captures the admin's id in a
    sibling GUC so downstream code can tell impersonated traffic from
    a direct client request. The yielded session assumes the
    ``api_app`` role; RLS narrows visibility to the target's rows.
    """
    eng = engine if engine is not None else get_engine()
    token_id = uuid.uuid4()
    await _record_audit_row(
        eng,
        mode=AdminAccessMode.IMPERSONATION,
        admin_id=admin_id,
        target_user_id=target_user_id,
        token_id=token_id,
        reason=reason,
    )
    async with session_for_user(eng, target_user_id) as session:
        await bind_impersonation_admin_id(session, admin_id)
        await set_local_role(session, "api_app")
        yield session
