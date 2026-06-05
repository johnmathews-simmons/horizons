# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""WU7.4 architectural test — ``admin_access_log`` is append-only.

The audit table is the source of truth for cross-tenant admin
elevations. It must never expose a DELETE or UPDATE surface to any
application role. WU1.0 / WU1.9 set this up:

- No ``CREATE POLICY ... FOR DELETE`` is attached.
- No role is granted ``DELETE`` or ``UPDATE`` privilege.
- BEFORE UPDATE / BEFORE DELETE triggers raise — a defence-in-depth
  fence behind the grant.

WU7.4 introduces a read surface on this table; this test pins the
invariant so a future migration that silently grants DELETE / UPDATE
or attaches a deletion policy can never reach production unnoticed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy import Engine


@pytest.mark.integration
def test_no_delete_or_update_grants_on_admin_access_log(
    migrated_postgres_h: Engine,
) -> None:
    """No application role may DELETE or UPDATE rows in ``admin_access_log``.

    ``schema_owner`` is excluded because it *owns* the table — those
    privileges are inherent ownership rights, not application-level
    grants, and ``schema_owner`` is not an identity any application
    code (api_app / ingestion_worker / admin_bypass) can assume.
    """
    with migrated_postgres_h.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT grantee, privilege_type "
                "FROM information_schema.role_table_grants "
                "WHERE table_name = 'admin_access_log' "
                "  AND privilege_type IN ('DELETE', 'UPDATE') "
                "  AND grantee NOT IN ('schema_owner', 'PUBLIC')"
            )
        ).all()
    assert rows == [], f"admin_access_log must be append-only — found illegal grants: {rows}"


@pytest.mark.integration
def test_no_delete_policy_on_admin_access_log(
    migrated_postgres_h: Engine,
) -> None:
    """No RLS policy may permit DELETE on ``admin_access_log``."""
    with migrated_postgres_h.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT policyname, cmd FROM pg_policies "
                "WHERE schemaname = 'public' "
                "  AND tablename = 'admin_access_log' "
                "  AND cmd IN ('DELETE', 'UPDATE', 'ALL')"
            )
        ).all()
    assert rows == [], (
        f"admin_access_log must not expose a DELETE/UPDATE/ALL policy — found: {rows}"
    )
