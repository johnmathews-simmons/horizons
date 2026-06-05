"""WU3.5 curated-set seed.

Library that bootstraps the ingestion worker's read-side substrate:
``documents`` rows and matching ``document_poll_schedule`` rows. The
inputs are an inventory of upstream fixtures (``data/samples/fixtures.json``)
and a curation policy (``data/curated_set.yaml``). The output is two
idempotent INSERTs per document.

See ``docs/seeding.md`` for the YAML schema, idempotency contract, and
WU8.0 hand-off plan. The CLI shim that calls into this module lives at
``scripts/seed_curated_set.py``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import yaml
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


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


__all__ = [
    "CuratedSet",
    "DocOverride",
    "PendingRow",
    "SeedResult",
    "SeedRow",
    "parse_curated_set",
    "run_seed",
    "select",
    "stagger",
]
