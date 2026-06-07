"""Unit tests for the WU3.5 curated-set seed library.

Exercises YAML parsing, fixture-filter/override expansion, and the
cadence-bucket stagger algorithm. No DB; integration tests for the
full-flow insert live at ``tests/integration/test_seed_curated_set.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from horizons_ingestion.seed import (
    CuratedSet,
    DocOverride,
    PendingRow,
    compute_v1_staging_payload,
    parse_curated_set,
    select,
    stagger,
)

# --- YAML parsing -------------------------------------------------------------

MINIMAL_YAML = """
jurisdictions: [IE, GB]
sectors: [financial-services]
default_cadence_hours: 24
"""

FULL_YAML = """
jurisdictions: [IE, GB, EU, BE]
sectors: [financial-services, employment]
default_cadence_hours: 24
documents:
  - id: "8064194"
    cadence_hours: 1
  - id: "19194112"
    sector: employment
  - id: "28914588"
    sector: employment
    title: "Foat v DWP - employment tribunal"
"""

JURISDICTION_OVERRIDE_YAML = """
jurisdictions: [GB, FR]
sectors: [BANKING, employment]
default_cadence_hours: 24
documents:
  - id: "28914588"
    jurisdiction: UK
    sector: BANKING
  - id: "31702142"
    jurisdiction: EU
    sector: BANKING
"""


def test_parse_curated_set_minimal() -> None:
    cs = parse_curated_set(MINIMAL_YAML)
    assert cs.jurisdictions == frozenset({"IE", "GB"})
    assert cs.sectors == ("financial-services",)
    assert cs.default_cadence_hours == 24
    assert cs.overrides == {}


def test_parse_curated_set_full() -> None:
    cs = parse_curated_set(FULL_YAML)
    assert cs.jurisdictions == frozenset({"IE", "GB", "EU", "BE"})
    assert cs.sectors == ("financial-services", "employment")
    assert cs.default_cadence_hours == 24
    assert set(cs.overrides) == {"8064194", "19194112", "28914588"}
    assert cs.overrides["8064194"] == DocOverride(cadence_hours=1)
    assert cs.overrides["19194112"] == DocOverride(sector="employment")
    assert cs.overrides["28914588"] == DocOverride(
        sector="employment", title="Foat v DWP - employment tribunal"
    )


def test_parse_rejects_missing_required_keys() -> None:
    with pytest.raises(ValueError, match="jurisdictions"):
        parse_curated_set("sectors: [x]\ndefault_cadence_hours: 24")
    with pytest.raises(ValueError, match="sectors"):
        parse_curated_set("jurisdictions: [IE]\ndefault_cadence_hours: 24")
    with pytest.raises(ValueError, match="default_cadence_hours"):
        parse_curated_set("jurisdictions: [IE]\nsectors: [x]")


def test_parse_rejects_unknown_top_level_key() -> None:
    bad = """
    jurisdictions: [IE]
    sectors: [x]
    default_cadence_hours: 24
    something_extra: 1
    """
    with pytest.raises(ValueError, match="something_extra"):
        parse_curated_set(bad)


def test_parse_rejects_unknown_override_sector() -> None:
    bad = """
    jurisdictions: [IE]
    sectors: [financial-services]
    default_cadence_hours: 24
    documents:
      - id: "1"
        sector: unknown-sector
    """
    with pytest.raises(ValueError, match="unknown-sector"):
        parse_curated_set(bad)


def test_parse_rejects_empty_sectors() -> None:
    bad = """
    jurisdictions: [IE]
    sectors: []
    default_cadence_hours: 24
    """
    with pytest.raises(ValueError, match="sectors.*empty|empty.*sectors"):
        parse_curated_set(bad)


def test_parse_rejects_non_positive_cadence() -> None:
    bad = """
    jurisdictions: [IE]
    sectors: [x]
    default_cadence_hours: 0
    """
    with pytest.raises(ValueError, match="cadence"):
        parse_curated_set(bad)


# --- Filter / override expansion ---------------------------------------------


def _fix(iso: str, doc_id: str, title: str = "T") -> dict[str, Any]:
    return {"iso": iso, "document_id": doc_id, "title": title}


def test_select_filters_by_jurisdiction() -> None:
    cs = parse_curated_set(MINIMAL_YAML)
    fixtures = [_fix("IE", "1"), _fix("FR", "2"), _fix("GB", "3")]
    rows = select(cs, fixtures)
    assert {r.lawstronaut_document_id for r in rows} == {"1", "3"}


def test_select_applies_default_sector_and_cadence() -> None:
    cs = parse_curated_set(MINIMAL_YAML)
    fixtures = [_fix("IE", "1", title="Doc One")]
    rows = select(cs, fixtures)
    assert rows == [
        PendingRow(
            lawstronaut_document_id="1",
            jurisdiction="IE",
            sector="financial-services",
            title="Doc One",
            cadence=timedelta(hours=24),
        ),
    ]


def test_select_applies_per_document_overrides() -> None:
    cs = parse_curated_set(FULL_YAML)
    fixtures = [
        _fix("IE", "8064194", title="upstream-title"),
        _fix("BE", "19194112", title="BE title"),
        _fix("GB", "28914588", title="GB title"),
    ]
    rows = {r.lawstronaut_document_id: r for r in select(cs, fixtures)}

    # cadence override
    assert rows["8064194"].cadence == timedelta(hours=1)
    assert rows["8064194"].sector == "financial-services"  # default sector
    assert rows["8064194"].title == "upstream-title"

    # sector override
    assert rows["19194112"].sector == "employment"
    assert rows["19194112"].cadence == timedelta(hours=24)

    # sector + title override
    assert rows["28914588"].sector == "employment"
    assert rows["28914588"].title == "Foat v DWP - employment tribunal"


def test_parse_accepts_jurisdiction_override() -> None:
    cs = parse_curated_set(JURISDICTION_OVERRIDE_YAML)
    # The fixture-iso filter is unchanged: only GB/FR fixtures pass through.
    assert cs.jurisdictions == frozenset({"GB", "FR"})
    assert cs.overrides["28914588"] == DocOverride(jurisdiction="UK", sector="BANKING")
    assert cs.overrides["31702142"] == DocOverride(jurisdiction="EU", sector="BANKING")


def test_select_applies_jurisdiction_override() -> None:
    """WU8.1 demo path: a GB fixture is relabelled to UK on output."""
    cs = parse_curated_set(JURISDICTION_OVERRIDE_YAML)
    fixtures = [
        _fix("GB", "28914588", title="Foat v DWP"),
        _fix("FR", "31702142", title="ACPR decision"),
    ]
    rows = {r.lawstronaut_document_id: r for r in select(cs, fixtures)}
    assert rows["28914588"].jurisdiction == "UK"
    assert rows["28914588"].sector == "BANKING"
    assert rows["31702142"].jurisdiction == "EU"
    assert rows["31702142"].sector == "BANKING"


def test_select_falls_back_to_fixture_iso_when_no_jurisdiction_override() -> None:
    cs = parse_curated_set(MINIMAL_YAML)
    fixtures = [_fix("IE", "1", title="t")]
    rows = select(cs, fixtures)
    assert rows[0].jurisdiction == "IE"


def test_select_warns_on_override_with_unknown_fixture() -> None:
    cs = parse_curated_set(FULL_YAML)
    fixtures: list[dict[str, Any]] = []
    warnings: list[str] = []
    rows = select(cs, fixtures, warn=warnings.append)
    assert rows == []
    # Three overrides, none of which match any fixture.
    assert len(warnings) == 3
    assert all("8064194" in w or "19194112" in w or "28914588" in w for w in warnings)


def test_select_lawstronaut_document_id_coerced_to_string() -> None:
    """fixtures.json stores some ids as ints — must coerce to string."""
    cs = parse_curated_set(MINIMAL_YAML)
    fixtures = [{"iso": "IE", "document_id": 8064194, "title": "t"}]
    rows = select(cs, fixtures)
    assert rows[0].lawstronaut_document_id == "8064194"


# --- Stagger -----------------------------------------------------------------


def _row(doc_id: str, cadence_hours: float = 24) -> PendingRow:
    return PendingRow(
        lawstronaut_document_id=doc_id,
        jurisdiction="IE",
        sector="financial-services",
        title="t",
        cadence=timedelta(hours=cadence_hours),
    )


def test_stagger_single_row_starts_at_now() -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    [seeded] = stagger([_row("1")], now)
    assert seeded.next_poll_at == now


def test_stagger_distributes_evenly_within_cadence_window() -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    rows = [_row(str(i), cadence_hours=24) for i in range(4)]
    seeded = stagger(rows, now)
    # 4 docs sharing a 24h window → offsets 0h, 6h, 12h, 18h.
    expected = [
        now,
        now + timedelta(hours=6),
        now + timedelta(hours=12),
        now + timedelta(hours=18),
    ]
    assert [s.next_poll_at for s in seeded] == expected


def test_stagger_buckets_by_cadence_independently() -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    rows = [
        _row("daily-1", cadence_hours=24),
        _row("hourly-1", cadence_hours=1),
        _row("daily-2", cadence_hours=24),
        _row("hourly-2", cadence_hours=1),
    ]
    seeded = {s.lawstronaut_document_id: s for s in stagger(rows, now)}
    # Daily bucket has 2 docs → offsets 0h, 12h.
    assert seeded["daily-1"].next_poll_at == now
    assert seeded["daily-2"].next_poll_at == now + timedelta(hours=12)
    # Hourly bucket has 2 docs → offsets 0h, 30min.
    assert seeded["hourly-1"].next_poll_at == now
    assert seeded["hourly-2"].next_poll_at == now + timedelta(minutes=30)


def test_stagger_preserves_input_order() -> None:
    now = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    rows = [_row(str(i)) for i in range(3)]
    seeded = stagger(rows, now)
    assert [s.lawstronaut_document_id for s in seeded] == ["0", "1", "2"]


# --- CuratedSet dataclass sanity ---------------------------------------------


def test_curated_set_default_sector_is_sectors_first_element() -> None:
    cs = CuratedSet(
        jurisdictions=frozenset({"IE"}),
        sectors=("financial-services", "employment"),
        default_cadence_hours=24,
        overrides={},
    )
    assert cs.default_sector == "financial-services"


# --- v1 staging helper -------------------------------------------------------


def test_compute_v1_staging_payload_parses_clauses() -> None:
    """The helper returns parsed clauses with paths the inserter can write."""
    markdown = "# Part 1\n\n## Section 1\n\nAlpha clause.\n\n## Section 2\n\nBeta clause.\n"
    payload = compute_v1_staging_payload(markdown)
    paths = [tuple(c.path) for c in payload.clauses]
    bodies = [c.body_text.strip() for c in payload.clauses]
    # ``parse(...)`` slugifies heading text, so the paths the inserter will
    # write are e.g. ``("part-1", "section-1", "#1")``. The exact tail token
    # is an implementation detail of the parser; assert the slugified
    # ancestry the inserter relies on.
    assert any(p[:2] == ("part-1", "section-1") for p in paths)
    assert any(p[:2] == ("part-1", "section-2") for p in paths)
    assert "Alpha clause." in bodies
    assert "Beta clause." in bodies
    assert payload.content_bytes == len(markdown.encode("utf-8"))
    assert len(payload.content_sha256) == 32  # SHA-256 digest length
