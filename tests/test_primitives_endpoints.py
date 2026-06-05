# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Integration tests for ``/v1/discovery`` (WU4.4).

Drives the full FastAPI stack against testcontainers Postgres:

- ``GET /v1/discovery?scope=corpus`` — returns in-scope events only,
  paginated by opaque cursor, with ``Cache-Control: private, no-store``.
- ``GET /v1/discovery?scope=document&document_id=...``
- ``GET /v1/discovery?scope=clause&clause_uid=...``
- Discriminator validation (missing required filter → 422).
- Cursor garbage → 422 (mapped from repo's ``CursorError``).
- 401 without bearer.

Temporal and Differential land in commits 2 and 3 of WU4.4.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from horizons_core.core.auth import hash_password
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Connection, Engine
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

ISSUER = "horizons-api-primitives-test"
AUDIENCE = "horizons-clients-primitives-test"


@pytest.fixture(scope="session")
def migrated_postgres_p(postgres_container: PostgresContainer) -> Iterator[Engine]:
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    cfg = Config(str(ALEMBIC_INI))
    import os

    prev = os.environ.get("HORIZONS_DB_URL")
    os.environ["HORIZONS_DB_URL"] = sync_url
    try:
        command.upgrade(cfg, "head")
    finally:
        if prev is None:
            os.environ.pop("HORIZONS_DB_URL", None)
        else:
            os.environ["HORIZONS_DB_URL"] = prev
    eng = create_engine(sync_url, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def rsa_pems() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


@pytest.fixture
def configured_env(
    rsa_pems: tuple[bytes, bytes],
    migrated_postgres_p: Engine,
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[bytes, bytes]:
    _ = migrated_postgres_p
    private_pem, public_pem = rsa_pems
    monkeypatch.setenv("HORIZONS_JWT_PRIVATE_KEY_PEM", private_pem.decode())
    monkeypatch.setenv("HORIZONS_JWT_PUBLIC_KEY_PEM", public_pem.decode())
    monkeypatch.setenv("HORIZONS_JWT_ISSUER", ISSUER)
    monkeypatch.setenv("HORIZONS_JWT_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("HORIZONS_CORS_ORIGINS", "")
    monkeypatch.setenv(
        "HORIZONS_DB_URL",
        postgres_container.get_connection_url(driver="asyncpg"),
    )

    from horizons_api.deps.provider import reset_provider_for_tests
    from horizons_core.db import session as session_mod

    reset_provider_for_tests()
    session_mod._engine = None  # type: ignore[attr-defined]  # noqa: SLF001
    return private_pem, public_pem


@pytest.fixture
def client(configured_env: tuple[bytes, bytes]) -> Iterator[TestClient]:
    _ = configured_env
    from horizons_api.app import create_app

    app = create_app()
    with TestClient(app, base_url="https://testserver") as c:
        yield c


# ---- seed helpers --------------------------------------------------------


def _sha256() -> bytes:
    return hashlib.sha256(uuid.uuid4().bytes).digest()


def _seed_user(
    engine: Engine,
    email: str,
    *,
    scope: tuple[tuple[str, str], ...] = (("UK", "BANKING"),),
) -> uuid.UUID:
    pw_hash = hash_password("pw")
    with engine.begin() as conn:
        uid = conn.execute(
            text(
                "INSERT INTO users (email, password_hash, role) "
                "VALUES (:e, :p, 'client') RETURNING id"
            ),
            {"e": email, "p": pw_hash},
        ).scalar_one()
        sub = conn.execute(
            text(
                "INSERT INTO subscriptions (user_id, valid_from) "
                "VALUES (:u, now() - interval '1 day') RETURNING id"
            ),
            {"u": uid},
        ).scalar_one()
        for j, s in scope:
            conn.execute(
                text(
                    "INSERT INTO subscription_scopes "
                    "(subscription_id, jurisdiction, sector) "
                    "VALUES (:s, :j, :sec)"
                ),
                {"s": sub, "j": j, "sec": s},
            )
    return uid


def _seed_doc(
    engine: Engine,
    *,
    jurisdiction: str,
    sector: str,
    label: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    with engine.begin() as conn:
        return _make_doc(conn, jurisdiction, sector, label)


def _make_doc(
    conn: Connection,
    jurisdiction: str,
    sector: str,
    label: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    doc_id = conn.execute(
        text(
            "INSERT INTO documents "
            "(jurisdiction, sector, lawstronaut_document_id, title) "
            "VALUES (:j, :s, :lid, :t) RETURNING id"
        ),
        {
            "j": jurisdiction,
            "s": sector,
            "lid": f"prim_{label}_{uuid.uuid4()}",
            "t": f"prim_{label}",
        },
    ).scalar_one()
    ver_id = conn.execute(
        text(
            "INSERT INTO document_versions "
            "(document_id, version_label, publication_date, effective_date, "
            "content_blob_container, content_blob_key, content_sha256, "
            "content_bytes) "
            "VALUES (:d, 'v1', :p, :e, 'prim', :k, :h, 100) RETURNING id"
        ),
        {
            "d": doc_id,
            "p": datetime.now(UTC),
            "e": datetime.now(UTC),
            "k": f"{label}/v1.md",
            "h": _sha256(),
        },
    ).scalar_one()
    return doc_id, ver_id


def _seed_event(
    engine: Engine,
    *,
    document_id: uuid.UUID,
    document_version_id: uuid.UUID,
    jurisdiction: str,
    sector: str,
    change_type: str = "MODIFIED",
    detected_at: datetime | None = None,
    before_clause_uid: uuid.UUID | None = None,
    after_clause_uid: uuid.UUID | None = None,
    before_text: str | None = "before",
    after_text: str | None = "after",
) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO change_events ("
                "  document_id, document_version_id, jurisdiction, sector, "
                "  change_type, before_clause_uid, after_clause_uid, "
                "  before_path, after_path, before_text, after_text, "
                "  alignment_confidence, detected_at"
                ") VALUES ("
                "  :doc, :ver, :j, :sec, :ct, :bcu, :acu, "
                "  'P1/S1', 'P1/S1', :bt, :at, 0.92, :dt"
                ") RETURNING id"
            ),
            {
                "doc": document_id,
                "ver": document_version_id,
                "j": jurisdiction,
                "sec": sector,
                "ct": change_type,
                "bcu": before_clause_uid,
                "acu": after_clause_uid,
                "bt": before_text,
                "at": after_text,
                "dt": detected_at or datetime.now(UTC),
            },
        ).scalar_one()


def _login(client: TestClient, email: str) -> str:
    response = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "pw"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- discovery tests -----------------------------------------------------


@pytest.mark.integration
def test_discovery_requires_bearer(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    _ = migrated_postgres_p
    response = client.get("/v1/discovery")
    assert response.status_code == 401


@pytest.mark.integration
def test_discovery_corpus_returns_in_scope_with_cache_header(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    _seed_user(migrated_postgres_p, "disc_corpus@example.com")
    doc, ver = _seed_doc(migrated_postgres_p, jurisdiction="UK", sector="BANKING", label="dc")
    in_id = _seed_event(
        migrated_postgres_p,
        document_id=doc,
        document_version_id=ver,
        jurisdiction="UK",
        sector="BANKING",
    )
    out_doc, out_ver = _seed_doc(
        migrated_postgres_p, jurisdiction="EU", sector="INSURANCE", label="dc_out"
    )
    out_id = _seed_event(
        migrated_postgres_p,
        document_id=out_doc,
        document_version_id=out_ver,
        jurisdiction="EU",
        sector="INSURANCE",
    )

    token = _login(client, "disc_corpus@example.com")
    response = client.get("/v1/discovery", headers=_bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers.get("Cache-Control") == "private, no-store"
    body = response.json()
    ids = {it["id"] for it in body["items"]}
    assert in_id in ids
    assert out_id not in ids
    # discovery wire shape does NOT carry before/after text
    for item in body["items"]:
        assert "before_text" not in item
        assert "after_text" not in item


@pytest.mark.integration
def test_discovery_corpus_paginates_via_cursor(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    # Unique (jurisdiction, sector) so this user's subscription sees
    # only this test's 5 rows; the session-scoped testcontainer is
    # shared across tests.
    j, s = f"PAG-{uuid.uuid4().hex[:8]}", f"PAG-{uuid.uuid4().hex[:8]}"
    _seed_user(migrated_postgres_p, "disc_page@example.com", scope=((j, s),))
    doc, ver = _seed_doc(migrated_postgres_p, jurisdiction=j, sector=s, label="dp")
    base = datetime(2026, 5, 1, tzinfo=UTC)
    seeded = [
        _seed_event(
            migrated_postgres_p,
            document_id=doc,
            document_version_id=ver,
            jurisdiction=j,
            sector=s,
            detected_at=base + timedelta(seconds=i),
        )
        for i in range(5)
    ]

    token = _login(client, "disc_page@example.com")

    page1 = client.get(
        "/v1/discovery?scope=corpus&limit=2",
        headers=_bearer(token),
    ).json()
    assert page1["has_more"] is True
    assert page1["next_cursor"] is not None
    assert len(page1["items"]) == 2

    page2 = client.get(
        f"/v1/discovery?scope=corpus&limit=2&cursor={page1['next_cursor']}",
        headers=_bearer(token),
    ).json()
    page3 = client.get(
        f"/v1/discovery?scope=corpus&limit=2&cursor={page2['next_cursor']}",
        headers=_bearer(token),
    ).json()

    walked = [it["id"] for it in page1["items"] + page2["items"] + page3["items"]]
    assert walked == list(reversed(seeded))
    assert page3["next_cursor"] is None
    assert page3["has_more"] is False


@pytest.mark.integration
def test_discovery_document_scope_requires_document_id(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    _seed_user(migrated_postgres_p, "disc_doc_missing@example.com")
    token = _login(client, "disc_doc_missing@example.com")
    response = client.get("/v1/discovery?scope=document", headers=_bearer(token))
    assert response.status_code == 422
    assert "document_id" in response.json()["detail"]


@pytest.mark.integration
def test_discovery_clause_scope_requires_clause_uid(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    _seed_user(migrated_postgres_p, "disc_clause_missing@example.com")
    token = _login(client, "disc_clause_missing@example.com")
    response = client.get("/v1/discovery?scope=clause", headers=_bearer(token))
    assert response.status_code == 422
    assert "clause_uid" in response.json()["detail"]


@pytest.mark.integration
def test_discovery_rejects_garbage_cursor_with_422(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    _seed_user(migrated_postgres_p, "disc_bad_cursor@example.com")
    token = _login(client, "disc_bad_cursor@example.com")
    response = client.get(
        "/v1/discovery?cursor=garbage",
        headers=_bearer(token),
    )
    assert response.status_code == 422


@pytest.mark.integration
def test_discovery_document_scope_filters_to_target(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    _seed_user(migrated_postgres_p, "disc_doc_scope@example.com")
    target_doc, target_ver = _seed_doc(
        migrated_postgres_p, jurisdiction="UK", sector="BANKING", label="dd_t"
    )
    other_doc, other_ver = _seed_doc(
        migrated_postgres_p, jurisdiction="UK", sector="BANKING", label="dd_o"
    )
    target_id = _seed_event(
        migrated_postgres_p,
        document_id=target_doc,
        document_version_id=target_ver,
        jurisdiction="UK",
        sector="BANKING",
    )
    _seed_event(
        migrated_postgres_p,
        document_id=other_doc,
        document_version_id=other_ver,
        jurisdiction="UK",
        sector="BANKING",
    )

    token = _login(client, "disc_doc_scope@example.com")
    response = client.get(
        f"/v1/discovery?scope=document&document_id={target_doc}",
        headers=_bearer(token),
    )
    assert response.status_code == 200
    ids = [it["id"] for it in response.json()["items"]]
    assert ids == [target_id]


# ---- temporal tests ------------------------------------------------------


@pytest.mark.integration
def test_temporal_requires_bearer(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    _ = migrated_postgres_p
    response = client.get("/v1/temporal")
    assert response.status_code == 401


@pytest.mark.integration
def test_temporal_corpus_returns_timestamps_and_drops_text(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    j, s = f"TMP-{uuid.uuid4().hex[:8]}", f"TMP-{uuid.uuid4().hex[:8]}"
    _seed_user(migrated_postgres_p, "tmp_corpus@example.com", scope=((j, s),))
    doc, ver = _seed_doc(migrated_postgres_p, jurisdiction=j, sector=s, label="tmp")
    after_uid = uuid.uuid4()
    seeded = _seed_event(
        migrated_postgres_p,
        document_id=doc,
        document_version_id=ver,
        jurisdiction=j,
        sector=s,
        change_type="MODIFIED",
        before_clause_uid=after_uid,
        after_clause_uid=after_uid,
    )

    token = _login(client, "tmp_corpus@example.com")
    response = client.get("/v1/temporal?scope=corpus", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.headers.get("Cache-Control") == "private, no-store"
    body = response.json()
    ids = [it["id"] for it in body["items"]]
    assert seeded in ids
    for item in body["items"]:
        # Temporal projects only timestamp / identity / change_type
        assert "before_text" not in item
        assert "after_text" not in item
        assert "before_path" not in item
        assert "after_path" not in item
        assert "clause_uid" in item
        assert "detected_at" in item
        assert "change_type" in item


@pytest.mark.integration
def test_temporal_projects_after_uid_for_modified_and_before_uid_for_removed(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    j, s = f"TPR-{uuid.uuid4().hex[:8]}", f"TPR-{uuid.uuid4().hex[:8]}"
    _seed_user(migrated_postgres_p, "tmp_proj@example.com", scope=((j, s),))
    doc, ver = _seed_doc(migrated_postgres_p, jurisdiction=j, sector=s, label="tpr")
    before_uid = uuid.uuid4()
    after_uid = uuid.uuid4()
    removed_only = uuid.uuid4()
    modified_id = _seed_event(
        migrated_postgres_p,
        document_id=doc,
        document_version_id=ver,
        jurisdiction=j,
        sector=s,
        change_type="MODIFIED",
        before_clause_uid=before_uid,
        after_clause_uid=after_uid,
    )
    removed_id = _seed_event(
        migrated_postgres_p,
        document_id=doc,
        document_version_id=ver,
        jurisdiction=j,
        sector=s,
        change_type="REMOVED",
        before_clause_uid=removed_only,
        after_clause_uid=None,
        after_text=None,
    )

    token = _login(client, "tmp_proj@example.com")
    body = client.get("/v1/temporal?scope=corpus", headers=_bearer(token)).json()
    by_id = {it["id"]: it for it in body["items"]}

    assert by_id[modified_id]["clause_uid"] == str(after_uid)
    assert by_id[removed_id]["clause_uid"] == str(removed_only)


@pytest.mark.integration
def test_temporal_document_scope_requires_document_id(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    _seed_user(migrated_postgres_p, "tmp_doc_missing@example.com")
    token = _login(client, "tmp_doc_missing@example.com")
    response = client.get("/v1/temporal?scope=document", headers=_bearer(token))
    assert response.status_code == 422
    assert "document_id" in response.json()["detail"]


# ---- differential tests --------------------------------------------------


@pytest.mark.integration
def test_differential_requires_bearer(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    _ = migrated_postgres_p
    response = client.get("/v1/differential")
    assert response.status_code == 401


@pytest.mark.integration
def test_differential_corpus_default_omits_text(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    """At corpus scope, include_content defaults false → no body text."""
    j, s = f"DCD-{uuid.uuid4().hex[:8]}", f"DCD-{uuid.uuid4().hex[:8]}"
    _seed_user(migrated_postgres_p, "dif_corpus_default@example.com", scope=((j, s),))
    doc, ver = _seed_doc(migrated_postgres_p, jurisdiction=j, sector=s, label="dcd")
    _seed_event(
        migrated_postgres_p,
        document_id=doc,
        document_version_id=ver,
        jurisdiction=j,
        sector=s,
        before_text="old body",
        after_text="new body",
    )

    token = _login(client, "dif_corpus_default@example.com")
    response = client.get("/v1/differential?scope=corpus", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert all(it["before_text"] is None for it in body["items"])
    assert all(it["after_text"] is None for it in body["items"])


@pytest.mark.integration
def test_differential_document_scope_default_includes_text(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    """At document scope, include_content defaults true → text present."""
    j, s = f"DDD-{uuid.uuid4().hex[:8]}", f"DDD-{uuid.uuid4().hex[:8]}"
    _seed_user(migrated_postgres_p, "dif_doc_default@example.com", scope=((j, s),))
    doc, ver = _seed_doc(migrated_postgres_p, jurisdiction=j, sector=s, label="ddd")
    _seed_event(
        migrated_postgres_p,
        document_id=doc,
        document_version_id=ver,
        jurisdiction=j,
        sector=s,
        before_text="old body",
        after_text="new body",
    )

    token = _login(client, "dif_doc_default@example.com")
    response = client.get(
        f"/v1/differential?scope=document&document_id={doc}",
        headers=_bearer(token),
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["before_text"] == "old body"
    assert items[0]["after_text"] == "new body"


@pytest.mark.integration
def test_differential_corpus_include_content_true_rejected_above_cap(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    """include_content=true at corpus scope with limit > 10 → 422."""
    _seed_user(migrated_postgres_p, "dif_corpus_cap@example.com")
    token = _login(client, "dif_corpus_cap@example.com")

    response = client.get(
        "/v1/differential?scope=corpus&include_content=true&limit=50",
        headers=_bearer(token),
    )
    assert response.status_code == 422
    assert "include_content" in response.json()["detail"]


@pytest.mark.integration
def test_differential_corpus_include_content_true_allowed_under_cap(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    """include_content=true at corpus scope with limit <= 10 → 200 + text."""
    j, s = f"DCC-{uuid.uuid4().hex[:8]}", f"DCC-{uuid.uuid4().hex[:8]}"
    _seed_user(migrated_postgres_p, "dif_corpus_cap_ok@example.com", scope=((j, s),))
    doc, ver = _seed_doc(migrated_postgres_p, jurisdiction=j, sector=s, label="dcc")
    _seed_event(
        migrated_postgres_p,
        document_id=doc,
        document_version_id=ver,
        jurisdiction=j,
        sector=s,
        before_text="cap_before",
        after_text="cap_after",
    )

    token = _login(client, "dif_corpus_cap_ok@example.com")
    response = client.get(
        "/v1/differential?scope=corpus&include_content=true&limit=10",
        headers=_bearer(token),
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert any(it["before_text"] == "cap_before" for it in items)


# ---- differential by id (WU5.3) -----------------------------------------


@pytest.mark.integration
def test_differential_by_id_requires_bearer(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    _ = migrated_postgres_p
    response = client.get("/v1/differential/1")
    assert response.status_code == 401


@pytest.mark.integration
def test_differential_by_id_returns_event_with_text(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    """In-scope event returns 200 with before/after text by default."""
    j, s = f"DBI-{uuid.uuid4().hex[:8]}", f"DBI-{uuid.uuid4().hex[:8]}"
    _seed_user(migrated_postgres_p, "dif_by_id_in@example.com", scope=((j, s),))
    doc, ver = _seed_doc(migrated_postgres_p, jurisdiction=j, sector=s, label="dbi")
    event_id = _seed_event(
        migrated_postgres_p,
        document_id=doc,
        document_version_id=ver,
        jurisdiction=j,
        sector=s,
        before_text="prior text",
        after_text="updated text",
    )

    token = _login(client, "dif_by_id_in@example.com")
    response = client.get(f"/v1/differential/{event_id}", headers=_bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers.get("Cache-Control") == "private, no-store"
    body = response.json()
    assert body["id"] == event_id
    assert body["before_text"] == "prior text"
    assert body["after_text"] == "updated text"


@pytest.mark.integration
def test_differential_by_id_include_content_false_omits_text(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    j, s = f"DBIC-{uuid.uuid4().hex[:8]}", f"DBIC-{uuid.uuid4().hex[:8]}"
    _seed_user(migrated_postgres_p, "dif_by_id_nc@example.com", scope=((j, s),))
    doc, ver = _seed_doc(migrated_postgres_p, jurisdiction=j, sector=s, label="dbic")
    event_id = _seed_event(
        migrated_postgres_p,
        document_id=doc,
        document_version_id=ver,
        jurisdiction=j,
        sector=s,
        before_text="prior text",
        after_text="updated text",
    )

    token = _login(client, "dif_by_id_nc@example.com")
    response = client.get(
        f"/v1/differential/{event_id}?include_content=false",
        headers=_bearer(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["before_text"] is None
    assert body["after_text"] is None


@pytest.mark.integration
def test_differential_by_id_out_of_scope_is_404(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    """Out-of-scope rows are invisible via RLS — must look like 404, not 403."""
    in_j, in_s = f"OOS-IN-{uuid.uuid4().hex[:6]}", f"OOS-IN-{uuid.uuid4().hex[:6]}"
    out_j, out_s = f"OOS-OUT-{uuid.uuid4().hex[:6]}", f"OOS-OUT-{uuid.uuid4().hex[:6]}"
    _seed_user(migrated_postgres_p, "dif_by_id_oos@example.com", scope=((in_j, in_s),))
    out_doc, out_ver = _seed_doc(migrated_postgres_p, jurisdiction=out_j, sector=out_s, label="oos")
    out_id = _seed_event(
        migrated_postgres_p,
        document_id=out_doc,
        document_version_id=out_ver,
        jurisdiction=out_j,
        sector=out_s,
    )

    token = _login(client, "dif_by_id_oos@example.com")
    response = client.get(f"/v1/differential/{out_id}", headers=_bearer(token))
    assert response.status_code == 404


@pytest.mark.integration
def test_differential_by_id_nonexistent_is_404(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    _seed_user(migrated_postgres_p, "dif_by_id_missing@example.com")
    token = _login(client, "dif_by_id_missing@example.com")
    response = client.get("/v1/differential/9999999", headers=_bearer(token))
    assert response.status_code == 404


# ---- load budget --------------------------------------------------------


@pytest.mark.integration
def test_discovery_corpus_p95_under_three_seconds(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    """Doc 3 sets a 3 s p95 budget for corpus-scope queries.

    Seeds ~500 events into a unique scope, then issues 50 paginated
    requests and asserts the 95th-percentile latency stays under
    3 s. Bounded inline to keep the dev loop's signal fast; the real
    deployed smoke runs in WU6.3.
    """
    import time

    j, s = f"P95-{uuid.uuid4().hex[:8]}", f"P95-{uuid.uuid4().hex[:8]}"
    _seed_user(migrated_postgres_p, "p95_load@example.com", scope=((j, s),))
    doc, ver = _seed_doc(migrated_postgres_p, jurisdiction=j, sector=s, label="p95")

    base = datetime(2026, 5, 1, tzinfo=UTC)
    seeds = 500
    with migrated_postgres_p.begin() as conn:
        for i in range(seeds):
            conn.execute(
                text(
                    "INSERT INTO change_events ("
                    "  document_id, document_version_id, jurisdiction, sector, "
                    "  change_type, before_clause_uid, after_clause_uid, "
                    "  before_path, after_path, before_text, after_text, "
                    "  alignment_confidence, detected_at"
                    ") VALUES ("
                    "  :doc, :ver, :j, :sec, 'MODIFIED', NULL, NULL, "
                    "  'P1/S1', 'P1/S1', 'b', 'a', 0.9, :dt"
                    ")"
                ),
                {
                    "doc": doc,
                    "ver": ver,
                    "j": j,
                    "sec": s,
                    "dt": base + timedelta(seconds=i),
                },
            )

    token = _login(client, "p95_load@example.com")
    iters = 50
    latencies: list[float] = []
    for _ in range(iters):
        t0 = time.perf_counter()
        response = client.get(
            "/v1/discovery?scope=corpus&limit=50",
            headers=_bearer(token),
        )
        latencies.append(time.perf_counter() - t0)
        assert response.status_code == 200

    latencies.sort()
    p95_idx = max(0, int(0.95 * iters) - 1)
    p95 = latencies[p95_idx]
    assert p95 < 3.0, f"p95 latency {p95:.3f}s exceeds 3s budget; sample: {latencies}"


@pytest.mark.integration
def test_discovery_clause_scope_filters_to_uid(
    client: TestClient,
    migrated_postgres_p: Engine,
) -> None:
    _seed_user(migrated_postgres_p, "disc_clause_scope@example.com")
    doc, ver = _seed_doc(migrated_postgres_p, jurisdiction="UK", sector="BANKING", label="dcl")
    target_uid = uuid.uuid4()
    other_uid = uuid.uuid4()
    target_id = _seed_event(
        migrated_postgres_p,
        document_id=doc,
        document_version_id=ver,
        jurisdiction="UK",
        sector="BANKING",
        before_clause_uid=target_uid,
        after_clause_uid=target_uid,
    )
    _seed_event(
        migrated_postgres_p,
        document_id=doc,
        document_version_id=ver,
        jurisdiction="UK",
        sector="BANKING",
        before_clause_uid=other_uid,
        after_clause_uid=other_uid,
    )

    token = _login(client, "disc_clause_scope@example.com")
    response = client.get(
        f"/v1/discovery?scope=clause&clause_uid={target_uid}",
        headers=_bearer(token),
    )
    assert response.status_code == 200
    ids = [it["id"] for it in response.json()["items"]]
    assert ids == [target_id]
