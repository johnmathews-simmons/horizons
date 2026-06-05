"""The per-document poll transaction (WU3.4).

The body that the WU3.3 claim loop's :data:`PollFn` seam calls for
each due document. See ``poll.md`` for the design and operating
contract.

The public entry point is :func:`poll_document` — an async callable
that the worker's ``__main__`` binds with its
:class:`LawstronautClient` and :class:`BlobStore` instances via
:func:`functools.partial` before handing the partial to
:class:`ClaimLoop`.
"""

from __future__ import annotations

import hashlib
import logging
import uuid as _uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, cast

from horizons_core.core.alignment import (
    ChangeEvent,
    Clause,
    align,
    parse,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from horizons_core.core.alignment import TuningConfig
    from horizons_core.core.lawstronaut import LawstronautClient

    from horizons_ingestion.blob import BlobStore
    from horizons_ingestion.loop import PoolConnection


_log = logging.getLogger(__name__)


# --- SQL ---------------------------------------------------------------------

LIVE_VERSION_SQL: Final = """
SELECT id, content_sha256, version_no
  FROM document_versions
 WHERE document_id = $1
   AND valid_to IS NULL
 ORDER BY version_no DESC NULLS LAST
 LIMIT 1
"""

PREV_CLAUSES_SQL: Final = """
SELECT clause_uid, clause_path, text_content, ord
  FROM clauses
 WHERE document_version_id = $1
 ORDER BY ord
"""

DOCUMENT_SCOPE_SQL: Final = """
SELECT jurisdiction, sector FROM documents WHERE id = $1
"""

EXTEND_VALID_TO_SQL: Final = """
UPDATE document_versions
   SET valid_to = $2
 WHERE id = $1
"""

INSERT_VERSION_SQL: Final = """
INSERT INTO document_versions
       (document_id, version_label, version_no, valid_from, valid_to,
        publication_date, effective_date,
        content_blob_container, content_blob_key,
        content_sha256, content_bytes)
VALUES ($1, $2, $3, $4, NULL, $5, $6, $7, $8, $9, $10)
RETURNING id
"""

CLOSE_PREV_VERSION_SQL: Final = """
UPDATE document_versions
   SET valid_to = $2
 WHERE id = $1
   AND valid_to IS NULL
"""

INSERT_CLAUSE_SQL: Final = """
INSERT INTO clauses
       (document_version_id, clause_uid, clause_path, text_content, ord)
VALUES ($1, $2, $3, $4, $5)
"""

INSERT_CHANGE_EVENT_SQL: Final = """
INSERT INTO change_events
       (document_id, document_version_id, jurisdiction, sector, change_type,
        before_clause_uid, after_clause_uid,
        before_path, after_path,
        before_text, after_text,
        alignment_confidence, effective_date)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
"""


# --- Public surface ----------------------------------------------------------


async def poll_document(
    conn: PoolConnection,
    document_id: _uuid.UUID,
    *,
    client: LawstronautClient,
    blob_store: BlobStore,
    blob_container: str = "originals",
    tuning: TuningConfig | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Poll one due document and persist any version change.

    The seam in WU3.3 calls this with ``conn`` (the asyncpg connection
    holding the SKIP-LOCKED row lock) and ``document_id``. Extra
    dependencies (``client``, ``blob_store``, ``blob_container``,
    ``tuning``, ``clock``) are bound by the worker's ``__main__`` via
    :func:`functools.partial` before the partial is handed to
    :class:`ClaimLoop`.

    See ``poll.md`` for the flow.
    """
    now_fn = clock or _utcnow
    doc = await client.get_markdown(str(document_id))
    if doc is None:
        _log.info(
            "poll_document: get_markdown returned None for document_id=%s; skipping",
            document_id,
        )
        return

    body_bytes = doc.markdown.encode("utf-8")
    sha_bytes = hashlib.sha256(body_bytes).digest()

    live = await conn.fetchrow(LIVE_VERSION_SQL, document_id)
    if live is not None and bytes(live["content_sha256"]) == sha_bytes:
        await conn.execute(EXTEND_VALID_TO_SQL, live["id"], now_fn())
        return

    blob_key = sha_bytes.hex() + ".md"
    await blob_store.put(blob_key, body_bytes)

    scope = await conn.fetchrow(DOCUMENT_SCOPE_SQL, document_id)
    if scope is None:
        raise RuntimeError(
            f"poll_document: documents row vanished mid-tick for document_id={document_id}"
        )
    jurisdiction: str = scope["jurisdiction"]
    sector: str = scope["sector"]

    new_tree = parse(doc.markdown)

    prev_version_id: _uuid.UUID | None = (
        cast("_uuid.UUID", live["id"]) if live is not None else None
    )
    prev_version_no: int = cast("int | None", live["version_no"]) or 0 if live is not None else 0
    prev_tree: Clause | None = None
    prev_uid_by_path: dict[tuple[str, ...], _uuid.UUID] = {}
    if prev_version_id is not None:
        prev_tree, prev_uid_by_path = await _load_previous_tree(conn, prev_version_id)

    if prev_tree is None:
        # First version we have ever seen for this document. Every
        # non-empty leaf is an ADDED event at confidence 1.0.
        events: list[ChangeEvent] = _initial_events(new_tree)
    else:
        events = align(prev_tree, new_tree, tuning=tuning)

    uid_map = _build_clause_uid_map(new_tree, events, prev_uid_by_path)

    publication = doc.publication_date
    effective = publication  # placeholder; per-jurisdiction lag is a future unit
    new_version_no = prev_version_no + 1

    now = now_fn()
    new_version_id = await conn.fetchval(
        INSERT_VERSION_SQL,
        document_id,
        f"v{new_version_no}",
        new_version_no,
        now,
        publication,
        effective,
        blob_container,
        blob_key,
        sha_bytes,
        len(body_bytes),
    )
    if new_version_id is None:
        raise RuntimeError("poll_document: INSERT INTO document_versions returned no id")

    clause_rows = list(_clause_insert_rows(new_tree, uid_map, new_version_id))
    if clause_rows:
        await conn.executemany(INSERT_CLAUSE_SQL, clause_rows)

    event_rows = list(
        _change_event_insert_rows(
            events,
            uid_map=uid_map,
            prev_uid_by_path=prev_uid_by_path,
            document_id=document_id,
            document_version_id=new_version_id,
            jurisdiction=jurisdiction,
            sector=sector,
            effective_date=effective,
        )
    )
    if event_rows:
        await conn.executemany(INSERT_CHANGE_EVENT_SQL, event_rows)

    if prev_version_id is not None:
        await conn.execute(CLOSE_PREV_VERSION_SQL, prev_version_id, now)


# --- Helpers -----------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _load_previous_tree(
    conn: PoolConnection,
    document_version_id: _uuid.UUID,
) -> tuple[Clause | None, dict[tuple[str, ...], _uuid.UUID]]:
    """Rebuild a depth-1 :class:`Clause` tree + path → UID map for a prior version.

    The aligner walks pre-order and only inspects ``body_text``,
    ``heading_text``, and ``path`` — it doesn't care about the
    structural depth. A flat list of leaves is a legitimate substrate.

    The path → UID map is what :func:`_build_clause_uid_map` uses to
    inherit identity across versions.
    """
    rows = await conn.fetch(PREV_CLAUSES_SQL, document_version_id)
    if not rows:
        return None, {}
    children: list[Clause] = []
    uid_by_path: dict[tuple[str, ...], _uuid.UUID] = {}
    for row in rows:
        path_str = cast("str", row["clause_path"])
        path = tuple(path_str.split("/")) if path_str else ()
        uid_by_path[path] = cast("_uuid.UUID", row["clause_uid"])
        children.append(
            Clause(
                path=path,
                heading_text=None,
                body_text=cast("str", row["text_content"]),
                numbering_label=None,
            )
        )
    root = Clause(
        path=(),
        heading_text=None,
        body_text="",
        numbering_label=None,
        children=tuple(children),
    )
    return root, uid_by_path


def _initial_events(new_tree: Clause) -> list[ChangeEvent]:
    """Emit one ADDED event per non-empty leaf when no predecessor exists."""
    events: list[ChangeEvent] = []
    for node in new_tree.walk():
        if not node.body_text.strip():
            continue
        events.append(
            ChangeEvent(
                change_type="ADDED",
                after_path=node.path,
                after_text=node.body_text,
                alignment_confidence=1.0,
            )
        )
    return events


def _build_clause_uid_map(
    new_tree: Clause,
    events: list[ChangeEvent],
    prev_uid_by_path: dict[tuple[str, ...], _uuid.UUID],
) -> dict[tuple[str, ...], _uuid.UUID]:
    """Build ``{after_path: clause_uid}`` for every clause in the new tree.

    A new-side clause the aligner paired with a before-side clause
    inherits the before-side's UID; an unpaired clause gets a fresh
    ``uuid4()``. The map is keyed by ``after_path`` because that is
    what each ``clauses`` row stores.

    Additionally, any unchanged paired clause (path and text both
    identical — emits no event from :func:`align`) inherits its UID by
    direct path lookup against ``prev_uid_by_path``.

    Path uniqueness within a version is enforced by
    ``UNIQUE(document_version_id, clause_path)`` (migration 0003), so
    the dict's key is well-defined.
    """
    # MODIFIED / MOVED events name the paired before-side path.
    pair_by_after_path: dict[tuple[str, ...], tuple[str, ...]] = {}
    for ev in events:
        if ev.after_path is not None and ev.before_path is not None:
            pair_by_after_path[ev.after_path] = ev.before_path

    out: dict[tuple[str, ...], _uuid.UUID] = {}
    for node in new_tree.walk():
        if not node.body_text.strip():
            continue
        # 1. Explicit pairing recorded on an event.
        before_path = pair_by_after_path.get(node.path)
        if before_path is not None and before_path in prev_uid_by_path:
            out[node.path] = prev_uid_by_path[before_path]
            continue
        # 2. Unchanged clause (same path, same text) — :func:`align`
        #    emits no event for it but identity must still carry over.
        if node.path in prev_uid_by_path:
            out[node.path] = prev_uid_by_path[node.path]
            continue
        # 3. Genuinely new clause.
        out[node.path] = _uuid.uuid4()
    return out


def _clause_insert_rows(
    new_tree: Clause,
    uid_map: dict[tuple[str, ...], _uuid.UUID],
    document_version_id: _uuid.UUID,
) -> list[tuple[_uuid.UUID, _uuid.UUID, str, str, int]]:
    """Yield ``(document_version_id, clause_uid, clause_path, text_content, ord)``."""
    rows: list[tuple[_uuid.UUID, _uuid.UUID, str, str, int]] = []
    ord_counter = 0
    for node in new_tree.walk():
        if not node.body_text.strip():
            continue
        ord_counter += 1
        rows.append(
            (
                document_version_id,
                uid_map[node.path],
                "/".join(node.path),
                node.body_text,
                ord_counter,
            )
        )
    return rows


def _change_event_insert_rows(
    events: list[ChangeEvent],
    *,
    uid_map: dict[tuple[str, ...], _uuid.UUID],
    prev_uid_by_path: dict[tuple[str, ...], _uuid.UUID],
    document_id: _uuid.UUID,
    document_version_id: _uuid.UUID,
    jurisdiction: str,
    sector: str,
    effective_date: datetime | None,
) -> list[
    tuple[
        _uuid.UUID,
        _uuid.UUID,
        str,
        str,
        str,
        _uuid.UUID | None,
        _uuid.UUID | None,
        str | None,
        str | None,
        str | None,
        str | None,
        float,
        datetime | None,
    ]
]:
    """Translate :class:`ChangeEvent` records into ``INSERT_CHANGE_EVENT_SQL`` tuples."""
    rows: list[
        tuple[
            _uuid.UUID,
            _uuid.UUID,
            str,
            str,
            str,
            _uuid.UUID | None,
            _uuid.UUID | None,
            str | None,
            str | None,
            str | None,
            str | None,
            float,
            datetime | None,
        ]
    ] = []
    for ev in events:
        after_uid = uid_map.get(ev.after_path) if ev.after_path is not None else None
        # before_uid: for MODIFIED / MOVED the paired clause inherited
        # its UID from the predecessor, so before_uid == after_uid. For
        # REMOVED the before-side path was in the predecessor's
        # clauses; look up the stored UID directly. ADDED never carries
        # a before-side.
        before_uid: _uuid.UUID | None
        if ev.change_type in {"MODIFIED", "MOVED"}:
            before_uid = after_uid
        elif ev.change_type == "REMOVED" and ev.before_path is not None:
            before_uid = prev_uid_by_path.get(ev.before_path)
        else:
            before_uid = None
        rows.append(
            (
                document_id,
                document_version_id,
                jurisdiction,
                sector,
                ev.change_type,
                before_uid,
                after_uid,
                "/".join(ev.before_path) if ev.before_path is not None else None,
                "/".join(ev.after_path) if ev.after_path is not None else None,
                ev.before_text,
                ev.after_text,
                ev.alignment_confidence,
                effective_date,
            )
        )
    return rows
