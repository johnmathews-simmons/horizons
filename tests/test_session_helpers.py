"""Unit tests for the pure-Python guards in ``db/session``.

The WU1.9 helpers (``set_local_role``, ``bind_impersonation_admin_id``)
otherwise need a live Postgres because they issue SQL. The role
allow-list, however, is pure Python and worth testing as a unit so
the guard does not regress silently. The DB-bound paths are exercised
end-to-end in ``tests/isolation/test_admin_paths.py``.
"""

from __future__ import annotations

import pytest
from horizons_core.db.session import set_local_role


async def test_set_local_role_rejects_arbitrary_role() -> None:
    """``set_local_role`` raises ``ValueError`` before issuing any SQL.

    The allow-list ``_ADMIN_SETTABLE_ROLES`` covers the only two roles
    the admin code paths assume (``admin_bypass`` and ``api_app``).
    Anything else — including legitimate Postgres roles like
    ``schema_owner`` or ``ingestion_worker`` that should never be set
    from application code — is rejected.
    """
    with pytest.raises(ValueError, match="not in admin-settable allow-list"):
        await set_local_role(session=None, role="ingestion_worker")  # type: ignore[arg-type]
