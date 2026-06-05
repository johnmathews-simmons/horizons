"""WU3.5 / WU8.0 curated-set seed.

Library that bootstraps the ingestion worker's read-side substrate:

* **WU3.5** — ``documents`` + ``document_poll_schedule`` rows. The inputs
  are an inventory of upstream fixtures (``data/samples/fixtures.json``)
  and a curation policy (``data/curated_set.yaml``). The output is two
  idempotent INSERTs per document. Entry point: :func:`run_seed`.
* **WU8.0** — synthetic ``v2`` document-version staging. Given a list of
  ``(lawstronaut_document_id, v1_path, v2_path)`` pairs, parses both
  markdown files, runs :func:`horizons_core.core.alignment.align`, and
  inserts the v1 and v2 ``document_versions`` rows plus the resulting
  ``clauses`` and ``change_events``. Idempotent at the document level —
  re-runs skip any document that already has staged versions. Entry
  point: :func:`stage_synthetic_v2`.

See ``docs/seeding.md`` for the YAML schema, idempotency contract, and
WU8.0 hand-off plan. The CLI shim that calls into this module lives at
``scripts/seed_curated_set.py``.
"""

from __future__ import annotations

import hashlib
import uuid as _uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import yaml
from horizons_core.core.alignment import (
    ChangeEvent,
    Clause,
    TuningConfig,
    align,
    parse,
)
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path
    from uuid import UUID


# --- Data classes ------------------------------------------------------------


@dataclass(frozen=True)
class DocOverride:
    """Optional per-document overrides spelled out in ``curated_set.yaml``."""

    cadence_hours: float | None = None
    sector: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class CuratedSet:
    """Parsed ``curated_set.yaml``. The default sector is ``sectors[0]``."""

    jurisdictions: frozenset[str]
    sectors: tuple[str, ...]
    default_cadence_hours: float
    overrides: dict[str, DocOverride]

    @property
    def default_sector(self) -> str:
        return self.sectors[0]


@dataclass(frozen=True)
class PendingRow:
    """A row ready to be staggered. The output of :func:`select`."""

    lawstronaut_document_id: str
    jurisdiction: str
    sector: str
    title: str
    cadence: timedelta


@dataclass(frozen=True)
class SeedRow:
    """A row ready to be inserted. The output of :func:`stagger`."""

    lawstronaut_document_id: str
    jurisdiction: str
    sector: str
    title: str
    cadence: timedelta
    next_poll_at: datetime


@dataclass(frozen=True)
class SeedResult:
    documents_inserted: int
    schedules_inserted: int
    documents_skipped_conflict: int


# --- YAML parsing ------------------------------------------------------------

_ALLOWED_TOP_LEVEL = frozenset({"jurisdictions", "sectors", "default_cadence_hours", "documents"})
_ALLOWED_DOC_KEYS = frozenset({"id", "cadence_hours", "sector", "title"})


def parse_curated_set(source: str) -> CuratedSet:
    """Parse YAML text into a :class:`CuratedSet`. Raises ``ValueError`` on schema errors."""
    raw_any: Any = yaml.safe_load(source)
    if raw_any is None:
        raise ValueError("curated_set.yaml is empty")
    if not isinstance(raw_any, dict):
        raise ValueError(
            f"curated_set.yaml must be a mapping at top level, got {type(raw_any).__name__}"
        )
    raw = cast("dict[str, Any]", raw_any)

    unknown = set(raw) - _ALLOWED_TOP_LEVEL
    if unknown:
        raise ValueError(f"unknown top-level key(s): {sorted(unknown)}")

    for required in ("jurisdictions", "sectors", "default_cadence_hours"):
        if required not in raw:
            raise ValueError(f"missing required key '{required}'")

    jurisdictions_raw: Any = raw["jurisdictions"]
    if not isinstance(jurisdictions_raw, list) or not jurisdictions_raw:
        raise ValueError("'jurisdictions' must be a non-empty list")
    jurisdictions = frozenset(str(j) for j in cast("list[Any]", jurisdictions_raw))

    sectors_raw: Any = raw["sectors"]
    if not isinstance(sectors_raw, list) or not sectors_raw:
        raise ValueError("'sectors' must be a non-empty list")
    sectors = tuple(str(s) for s in cast("list[Any]", sectors_raw))
    allowed_sectors = set(sectors)

    cadence_raw: Any = raw["default_cadence_hours"]
    default_cadence_hours = float(cadence_raw)
    if default_cadence_hours <= 0:
        raise ValueError("'default_cadence_hours' must be a positive number")

    overrides: dict[str, DocOverride] = {}
    documents_raw: Any = raw.get("documents") or []
    if not isinstance(documents_raw, list):
        raise ValueError("'documents' must be a list of entries")
    for entry_any in cast("list[Any]", documents_raw):
        if not isinstance(entry_any, dict):
            raise ValueError(f"document entry must be a mapping, got {type(entry_any).__name__}")
        entry = cast("dict[str, Any]", entry_any)
        unknown_keys = set(entry) - _ALLOWED_DOC_KEYS
        if unknown_keys:
            raise ValueError(f"document entry has unknown key(s): {sorted(unknown_keys)}")
        if "id" not in entry:
            raise ValueError("document entry is missing 'id'")
        doc_id = str(entry["id"])

        sector_val: Any = entry.get("sector")
        sector: str | None = None
        if sector_val is not None:
            sector = str(sector_val)
            if sector not in allowed_sectors:
                raise ValueError(
                    f"document id={doc_id!r}: sector {sector!r} is not in the top-level "
                    f"'sectors' list {sorted(allowed_sectors)}"
                )

        cadence_val: Any = entry.get("cadence_hours")
        cadence_hours: float | None = None
        if cadence_val is not None:
            cadence_hours = float(cadence_val)
            if cadence_hours <= 0:
                raise ValueError(f"document id={doc_id!r}: cadence_hours must be positive")

        title_val: Any = entry.get("title")
        title: str | None = str(title_val) if title_val is not None else None

        overrides[doc_id] = DocOverride(cadence_hours=cadence_hours, sector=sector, title=title)

    return CuratedSet(
        jurisdictions=jurisdictions,
        sectors=sectors,
        default_cadence_hours=default_cadence_hours,
        overrides=overrides,
    )


# --- Filter + override expansion --------------------------------------------


def select(
    cs: CuratedSet,
    fixtures: Iterable[dict[str, Any]],
    warn: Callable[[str], None] | None = None,
) -> list[PendingRow]:
    """Filter ``fixtures`` to those in scope and apply overrides.

    Fixtures whose ``iso`` is not in ``cs.jurisdictions`` are skipped.
    Overrides whose ``id`` is not present in ``fixtures`` are reported via
    the ``warn`` callback (one call per unmatched id) and skipped.
    """
    rows: list[PendingRow] = []
    matched_ids: set[str] = set()
    for fixture in fixtures:
        iso = fixture.get("iso")
        if iso not in cs.jurisdictions:
            continue
        doc_id = str(fixture["document_id"])
        matched_ids.add(doc_id)
        override = cs.overrides.get(doc_id)

        sector = (
            override.sector
            if override is not None and override.sector is not None
            else cs.default_sector
        )
        cadence_hours = (
            override.cadence_hours
            if override is not None and override.cadence_hours is not None
            else cs.default_cadence_hours
        )
        title = (
            override.title
            if override is not None and override.title is not None
            else str(fixture["title"])
        )

        rows.append(
            PendingRow(
                lawstronaut_document_id=doc_id,
                jurisdiction=str(iso),
                sector=sector,
                title=title,
                cadence=timedelta(hours=cadence_hours),
            )
        )

    if warn is not None:
        for unmatched_id in sorted(set(cs.overrides) - matched_ids):
            warn(
                f"curated_set override id={unmatched_id!r} not found in fixture inventory; skipped"
            )

    return rows


# --- Stagger -----------------------------------------------------------------


def stagger(rows: list[PendingRow], now: datetime) -> list[SeedRow]:
    """Distribute ``next_poll_at`` evenly within each cadence bucket.

    Documents that share a cadence get evenly spaced offsets in
    ``[0, cadence)``; documents with distinct cadences are staggered
    independently of each other.
    """
    by_cadence: dict[timedelta, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        by_cadence[row.cadence].append(i)

    offsets: dict[int, timedelta] = {}
    for cadence, indices in by_cadence.items():
        n = len(indices)
        for k, i in enumerate(indices):
            offsets[i] = cadence * k / n

    return [
        SeedRow(
            lawstronaut_document_id=row.lawstronaut_document_id,
            jurisdiction=row.jurisdiction,
            sector=row.sector,
            title=row.title,
            cadence=row.cadence,
            next_poll_at=now + offsets[i],
        )
        for i, row in enumerate(rows)
    ]


# --- DB writer ---------------------------------------------------------------


_INSERT_DOCUMENT_SQL = text(
    "INSERT INTO documents "
    "(jurisdiction, sector, lawstronaut_document_id, title) "
    "VALUES (:j, :s, :lid, :t) "
    "ON CONFLICT (lawstronaut_document_id) DO NOTHING "
    "RETURNING id"
)

_SELECT_DOCUMENT_ID_SQL = text("SELECT id FROM documents WHERE lawstronaut_document_id = :lid")

_INSERT_SCHEDULE_SQL = text(
    "INSERT INTO document_poll_schedule "
    "(document_id, cadence_interval, next_poll_at) "
    "VALUES (:d, :c, :n) "
    "ON CONFLICT (document_id) DO NOTHING "
    "RETURNING document_id"
)


def run_seed(
    dsn: str,
    curated: CuratedSet,
    fixtures: Iterable[dict[str, Any]],
    *,
    now: datetime,
    warn: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> SeedResult:
    """Apply ``curated`` to ``fixtures`` and write rows to the DB at ``dsn``.

    Idempotent: re-runs with identical inputs leave the DB unchanged. The
    return value reports how many documents were freshly inserted versus
    already present (``documents_skipped_conflict``).
    """
    pending = select(curated, fixtures, warn=warn)
    seeded = stagger(pending, now)

    if dry_run:
        return SeedResult(
            documents_inserted=len(seeded),
            schedules_inserted=len(seeded),
            documents_skipped_conflict=0,
        )

    engine = create_engine(dsn, future=True)
    docs_inserted = 0
    schedules_inserted = 0
    docs_skipped = 0
    try:
        with engine.begin() as conn:
            for row in seeded:
                inserted_id: Any = conn.execute(
                    _INSERT_DOCUMENT_SQL,
                    {
                        "j": row.jurisdiction,
                        "s": row.sector,
                        "lid": row.lawstronaut_document_id,
                        "t": row.title,
                    },
                ).scalar()
                if inserted_id is None:
                    docs_skipped += 1
                    document_id: Any = conn.execute(
                        _SELECT_DOCUMENT_ID_SQL,
                        {"lid": row.lawstronaut_document_id},
                    ).scalar_one()
                else:
                    docs_inserted += 1
                    document_id = inserted_id

                schedule_id: Any = conn.execute(
                    _INSERT_SCHEDULE_SQL,
                    {"d": document_id, "c": row.cadence, "n": row.next_poll_at},
                ).scalar()
                if schedule_id is not None:
                    schedules_inserted += 1
    finally:
        engine.dispose()

    return SeedResult(
        documents_inserted=docs_inserted,
        schedules_inserted=schedules_inserted,
        documents_skipped_conflict=docs_skipped,
    )


# --- WU8.0: synthetic v2 staging ---------------------------------------------


@dataclass(frozen=True)
class SyntheticV2Pair:
    """One ``(v1, v2)`` markdown pair to stage for the demo."""

    lawstronaut_document_id: str
    v1_path: Path
    v2_path: Path


@dataclass(frozen=True)
class StagingResult:
    """Outcome counters for :func:`stage_synthetic_v2`."""

    documents_staged: int
    documents_skipped_missing: int
    documents_skipped_already_staged: int
    clauses_inserted: int
    change_events_inserted: int


# v1 blob_container is the on-disk fixture root; v2 is the synthesised set.
# Real production storage will be Azure Blob, named under "originals" by the
# worker. Sentinel values here keep the pointer explicit without pretending
# the markdown lives in a real container.
_V1_BLOB_CONTAINER = "samples"
_V2_BLOB_CONTAINER = "synthetic_v2"


_FIND_DOCUMENT_SQL = text(
    "SELECT id, jurisdiction, sector FROM documents WHERE lawstronaut_document_id = :lid"
)

_HAS_VERSIONS_SQL = text("SELECT 1 FROM document_versions WHERE document_id = :d LIMIT 1")

_INSERT_VERSION_SQL = text(
    "INSERT INTO document_versions "
    "(document_id, version_label, version_no, valid_from, valid_to, "
    " publication_date, effective_date, "
    " content_blob_container, content_blob_key, "
    " content_sha256, content_bytes) "
    "VALUES (:d, :lbl, :vno, :vf, :vt, :pub, :eff, :bc, :bk, :sha, :bytes) "
    "RETURNING id"
)

_INSERT_CLAUSE_SQL = text(
    "INSERT INTO clauses "
    "(document_version_id, clause_uid, clause_path, text_content, ord) "
    "VALUES (:dv, :uid, :path, :body, :ord)"
)

_INSERT_CHANGE_EVENT_SQL = text(
    "INSERT INTO change_events "
    "(document_id, document_version_id, jurisdiction, sector, change_type, "
    " before_clause_uid, after_clause_uid, "
    " before_path, after_path, "
    " before_text, after_text, "
    " alignment_confidence, effective_date) "
    "VALUES (:doc, :dv, :j, :s, :ct, :bu, :au, :bp, :ap, :bt, :at, :conf, :eff)"
)


def _walk_emitting_leaves(tree: Clause) -> list[Clause]:
    """Return clauses the aligner treats as units: every node with non-empty body."""
    return [node for node in tree.walk() if node.body_text.strip()]


def _build_uid_map_for_v2(
    v2_tree: Clause,
    events: list[ChangeEvent],
    prev_uid_by_path: dict[tuple[str, ...], UUID],
) -> dict[tuple[str, ...], UUID]:
    """Mirror :func:`horizons_ingestion.poll._build_clause_uid_map`.

    Paired clauses inherit their predecessor's UID; unchanged paired
    clauses inherit by direct path lookup; genuinely new clauses get a
    fresh ``uuid4()``.
    """
    pair_by_after_path: dict[tuple[str, ...], tuple[str, ...]] = {}
    for ev in events:
        if ev.after_path is not None and ev.before_path is not None:
            pair_by_after_path[ev.after_path] = ev.before_path

    out: dict[tuple[str, ...], UUID] = {}
    for node in _walk_emitting_leaves(v2_tree):
        before_path = pair_by_after_path.get(node.path)
        if before_path is not None and before_path in prev_uid_by_path:
            out[node.path] = prev_uid_by_path[before_path]
            continue
        if node.path in prev_uid_by_path:
            out[node.path] = prev_uid_by_path[node.path]
            continue
        out[node.path] = _uuid.uuid4()
    return out


def _stage_one_pair(
    conn: Any,  # SQLAlchemy Connection (typed as Any to avoid Sequence overload churn)
    pair: SyntheticV2Pair,
    *,
    now: datetime,
    tuning: TuningConfig | None,
    warn: Callable[[str], None] | None,
) -> tuple[int, int] | None:
    """Stage one v1/v2 pair. Returns ``(clauses, events)`` or ``None`` if skipped."""
    row = conn.execute(_FIND_DOCUMENT_SQL, {"lid": pair.lawstronaut_document_id}).first()
    if row is None:
        if warn is not None:
            warn(
                f"stage_synthetic_v2: document {pair.lawstronaut_document_id!r} not in "
                f"documents table; skipped (seed it via run_seed first)"
            )
        return None
    doc_id: UUID = cast("UUID", row.id)
    jurisdiction = cast("str", row.jurisdiction)
    sector = cast("str", row.sector)

    if conn.execute(_HAS_VERSIONS_SQL, {"d": doc_id}).first() is not None:
        return None

    v1_bytes = pair.v1_path.read_bytes()
    v2_bytes = pair.v2_path.read_bytes()
    v1_tree = parse(v1_bytes.decode("utf-8"))
    v2_tree = parse(v2_bytes.decode("utf-8"))
    events = align(v1_tree, v2_tree, tuning=tuning)

    # v1 → closed at `now`; v2 → live (valid_to NULL). The append-only
    # trigger on document_versions permits this initial INSERT because
    # the rule only constrains UPDATEs.
    v1_id: UUID = conn.execute(
        _INSERT_VERSION_SQL,
        {
            "d": doc_id,
            "lbl": "v1",
            "vno": 1,
            "vf": now,
            "vt": now,
            "pub": None,
            "eff": None,
            "bc": _V1_BLOB_CONTAINER,
            "bk": pair.v1_path.name,
            "sha": hashlib.sha256(v1_bytes).digest(),
            "bytes": len(v1_bytes),
        },
    ).scalar_one()

    prev_uid_by_path: dict[tuple[str, ...], UUID] = {}
    clause_count = 0
    for ord_i, node in enumerate(_walk_emitting_leaves(v1_tree), start=1):
        uid = _uuid.uuid4()
        prev_uid_by_path[node.path] = uid
        conn.execute(
            _INSERT_CLAUSE_SQL,
            {
                "dv": v1_id,
                "uid": uid,
                "path": "/".join(node.path),
                "body": node.body_text,
                "ord": ord_i,
            },
        )
        clause_count += 1

    v2_id: UUID = conn.execute(
        _INSERT_VERSION_SQL,
        {
            "d": doc_id,
            "lbl": "v2-synthetic",
            "vno": 2,
            "vf": now,
            "vt": None,
            "pub": None,
            "eff": None,
            "bc": _V2_BLOB_CONTAINER,
            "bk": pair.v2_path.name,
            "sha": hashlib.sha256(v2_bytes).digest(),
            "bytes": len(v2_bytes),
        },
    ).scalar_one()

    uid_map = _build_uid_map_for_v2(v2_tree, events, prev_uid_by_path)
    for ord_i, node in enumerate(_walk_emitting_leaves(v2_tree), start=1):
        conn.execute(
            _INSERT_CLAUSE_SQL,
            {
                "dv": v2_id,
                "uid": uid_map[node.path],
                "path": "/".join(node.path),
                "body": node.body_text,
                "ord": ord_i,
            },
        )
        clause_count += 1

    event_count = 0
    for ev in events:
        after_uid = uid_map.get(ev.after_path) if ev.after_path is not None else None
        before_uid: UUID | None
        if ev.change_type in {"MODIFIED", "MOVED"}:
            before_uid = after_uid
        elif ev.change_type == "REMOVED" and ev.before_path is not None:
            before_uid = prev_uid_by_path.get(ev.before_path)
        else:
            before_uid = None
        conn.execute(
            _INSERT_CHANGE_EVENT_SQL,
            {
                "doc": doc_id,
                "dv": v2_id,
                "j": jurisdiction,
                "s": sector,
                "ct": ev.change_type,
                "bu": before_uid,
                "au": after_uid,
                "bp": "/".join(ev.before_path) if ev.before_path is not None else None,
                "ap": "/".join(ev.after_path) if ev.after_path is not None else None,
                "bt": ev.before_text,
                "at": ev.after_text,
                "conf": ev.alignment_confidence,
                "eff": None,
            },
        )
        event_count += 1

    return clause_count, event_count


def stage_synthetic_v2(
    dsn: str,
    pairs: Iterable[SyntheticV2Pair],
    *,
    now: datetime,
    tuning: TuningConfig | None = None,
    warn: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> StagingResult:
    """Insert v1 + v2 ``document_versions``, ``clauses``, and ``change_events``.

    Idempotent at the document level — a document with at least one
    existing ``document_versions`` row is skipped. Missing source
    documents (no row in ``documents`` for the given
    ``lawstronaut_document_id``) are reported via ``warn`` and skipped;
    the caller is expected to have run :func:`run_seed` first.

    ``dry_run=True`` parses both markdown files and runs the alignment
    pipeline but performs no DB writes. The returned counts mirror what
    *would* be inserted, less the missing/already-staged tallies, which
    cannot be determined without a DB.
    """
    pairs_list = list(pairs)

    if dry_run:
        clauses = 0
        events = 0
        for pair in pairs_list:
            v1_tree = parse(pair.v1_path.read_text(encoding="utf-8"))
            v2_tree = parse(pair.v2_path.read_text(encoding="utf-8"))
            v1_leaves = _walk_emitting_leaves(v1_tree)
            v2_leaves = _walk_emitting_leaves(v2_tree)
            clauses += len(v1_leaves) + len(v2_leaves)
            events += len(align(v1_tree, v2_tree, tuning=tuning))
        return StagingResult(
            documents_staged=len(pairs_list),
            documents_skipped_missing=0,
            documents_skipped_already_staged=0,
            clauses_inserted=clauses,
            change_events_inserted=events,
        )

    engine = create_engine(dsn, future=True)
    staged = 0
    missing = 0
    already = 0
    clauses_total = 0
    events_total = 0
    try:
        for pair in pairs_list:
            with engine.begin() as conn:
                # A second SELECT before the work tells us whether the
                # skip was "no documents row" or "already staged" — the
                # _stage_one_pair returns None for both, so we re-probe
                # to get distinguishable counters.
                doc_row = conn.execute(
                    _FIND_DOCUMENT_SQL,
                    {"lid": pair.lawstronaut_document_id},
                ).first()
                if doc_row is None:
                    missing += 1
                    if warn is not None:
                        warn(
                            f"stage_synthetic_v2: document "
                            f"{pair.lawstronaut_document_id!r} not in documents "
                            f"table; skipped (seed it via run_seed first)"
                        )
                    continue
                if conn.execute(_HAS_VERSIONS_SQL, {"d": doc_row.id}).first() is not None:
                    already += 1
                    continue
                outcome = _stage_one_pair(conn, pair, now=now, tuning=tuning, warn=warn)
                if outcome is None:  # pragma: no cover — re-checked above
                    continue
                pair_clauses, pair_events = outcome
                clauses_total += pair_clauses
                events_total += pair_events
                staged += 1
    finally:
        engine.dispose()

    return StagingResult(
        documents_staged=staged,
        documents_skipped_missing=missing,
        documents_skipped_already_staged=already,
        clauses_inserted=clauses_total,
        change_events_inserted=events_total,
    )


__all__ = [
    "CuratedSet",
    "DocOverride",
    "PendingRow",
    "SeedResult",
    "SeedRow",
    "StagingResult",
    "SyntheticV2Pair",
    "parse_curated_set",
    "run_seed",
    "select",
    "stage_synthetic_v2",
    "stagger",
]
