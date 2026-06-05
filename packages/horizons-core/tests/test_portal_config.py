"""Tests for the per-portal :class:`ParserConfig` loader and bundled YAMLs.

Coverage falls into four groups:

* Loader behaviour — slug resolution, unknown-slug errors, listing.
* ``_default`` drift detection — the bundled YAML round-trips with
  :func:`default_parser_config` so they cannot silently diverge.
* End-to-end parser hookup — ``parse(..., portal_slug=...)`` resolves
  and applies the bundled config.
* Per-portal landmarks — each bundled portal asserts at least one
  substrate-specific behavioural difference against a real fixture
  from ``data/samples/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from horizons_core.core.alignment import (
    Clause,
    IgnorePattern,
    ParserConfig,
    StructuralPattern,
    default_parser_config,
    list_portal_slugs,
    load_portal_config,
    parse,
)
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLES = REPO_ROOT / "data" / "samples"
IE_FIXTURE = SAMPLES / "ie-27732019-v1.md"
CZ_FIXTURE = SAMPLES / "cz-29662776-v1.md"
AU_FIXTURE = SAMPLES / "au-2145602-v1.md"
AT_FIXTURE = SAMPLES / "at-32061749-v1.md"
EU_FIXTURE = SAMPLES / "eu-31366184-v1.md"


def _find(root: Clause, path: tuple[str, ...]) -> Clause | None:
    for node in root.walk():
        if node.path == path:
            return node
    return None


# ---------------------------------------------------------------------------
# Loader behaviour
# ---------------------------------------------------------------------------


def test_load_portal_config_returns_parser_config_for_each_bundled_slug() -> None:
    for slug in list_portal_slugs():
        cfg = load_portal_config(slug)
        assert isinstance(cfg, ParserConfig)


def test_load_portal_config_unknown_slug_raises_key_error() -> None:
    with pytest.raises(KeyError):
        load_portal_config("definitely-not-a-portal")


def test_list_portal_slugs_includes_default_and_is_sorted() -> None:
    slugs = list_portal_slugs()
    assert "_default" in slugs
    assert slugs == sorted(slugs)


def test_list_portal_slugs_contains_the_five_curated_portals() -> None:
    slugs = set(list_portal_slugs())
    assert {"_default", "ie", "cz", "au", "at", "eu"} <= slugs


# ---------------------------------------------------------------------------
# Default round-trip — drift between YAML and Python defaults fails loudly
# ---------------------------------------------------------------------------


def test_default_yaml_round_trips_against_default_parser_config() -> None:
    loaded = load_portal_config("_default")
    expected = default_parser_config()
    assert loaded.model_dump() == expected.model_dump()


# ---------------------------------------------------------------------------
# ParserConfig.ignore_patterns
# ---------------------------------------------------------------------------


def test_ignore_pattern_is_frozen() -> None:
    p = IgnorePattern(name="ex", regex=r"hello")
    with pytest.raises(ValidationError):
        p.name = "mutated"  # type: ignore[misc]


def test_default_parser_config_has_empty_ignore_patterns() -> None:
    cfg = default_parser_config()
    assert cfg.ignore_patterns == []


def test_ignore_pattern_drops_paragraph_completely() -> None:
    cfg = ParserConfig(
        patterns=[
            StructuralPattern(name="dollar", regex=r"\$\d+", depth=1, requires_boundary=False),
        ],
        ignore_patterns=[IgnorePattern(name="skipme", regex=r"SKIP THIS")],
    )
    md = "$1 first\n\nSKIP THIS\n\n$2 second\n"
    root = parse(md, config=cfg)
    labels = [c.numbering_label for c in root.children]
    bodies = [c.body_text for c in root.children]
    assert labels == ["$1", "$2"]
    assert "SKIP THIS" not in bodies


def test_ignore_pattern_requires_fullmatch_not_prefix() -> None:
    cfg = ParserConfig(
        ignore_patterns=[IgnorePattern(name="exact", regex=r"DROP ME")],
    )
    # The pattern only matches an exact paragraph; prefix-style content
    # passes through as a normal leaf.
    root = parse("DROP ME with extra\n", config=cfg)
    assert len(root.children) == 1
    assert root.children[0].body_text == "DROP ME with extra"


# ---------------------------------------------------------------------------
# parse() entry-point precedence
# ---------------------------------------------------------------------------


def test_parse_portal_slug_loads_bundled_config() -> None:
    md = "Be it enacted by the Oireachtas as follows:\n\n**PART 1**\n\nbody.\n"
    root = parse(md, portal_slug="ie")
    part1 = next((c for c in root.children if c.numbering_label == "PART 1"), None)
    assert part1 is not None
    # The enacting formula was dropped by ie.yaml's ignore_patterns —
    # if it had been kept, it would be PART 1's pending heading.
    assert part1.heading_text is None or "enacted" not in part1.heading_text


def test_parse_explicit_config_wins_over_portal_slug() -> None:
    # Even with a portal slug pointing at IE (which has ignore_patterns),
    # the explicit config takes precedence.
    explicit = ParserConfig()
    md = "Be it enacted by the Oireachtas as follows:\n"
    root = parse(md, config=explicit, portal_slug="ie")
    # With default config, no ignore_patterns, so the formula survives
    # as a leaf at root.
    bodies = [c.body_text for c in root.children]
    assert any("enacted" in b for b in bodies)


# ---------------------------------------------------------------------------
# Per-portal landmark assertions against real fixtures
# ---------------------------------------------------------------------------


def test_ie_portal_drops_enacting_formula_from_part1_heading() -> None:
    root = parse(IE_FIXTURE.read_text(encoding="utf-8"), portal_slug="ie")
    part1 = _find(root, ("PART 1",))
    assert part1 is not None
    if part1.heading_text is not None:
        assert "enacted by the Oireachtas" not in part1.heading_text


def test_cz_portal_recognises_cast_and_clanek_structure() -> None:
    root = parse(CZ_FIXTURE.read_text(encoding="utf-8"), portal_slug="cz")
    clanek = _find(root, ("ČÁST PRVNÍ", "Čl. I"))
    assert clanek is not None
    assert clanek.numbering_label == "Čl. I"


def test_cz_portal_drops_latin_letter_para_pattern() -> None:
    # cz.yaml omits `letter_para` — so the parser must not produce any
    # clause whose label is a single Latin letter in parens.
    cfg = load_portal_config("cz")
    pattern_names = {p.name for p in cfg.patterns}
    assert "letter_para" not in pattern_names
    assert "roman_subpara" not in pattern_names


def test_au_portal_recognises_no_period_section_headings() -> None:
    root = parse(AU_FIXTURE.read_text(encoding="utf-8"), portal_slug="au")
    labels = {c.numbering_label for c in root.walk() if c.numbering_label}
    # The fixture has "1  Name", "2  Commencement", "3  Authority",
    # "4  Definitions" — the section regex captures the bare digits.
    assert {"1", "2", "3", "4"} <= labels


def test_at_portal_recognises_paragraph_symbol_marker() -> None:
    root = parse(AT_FIXTURE.read_text(encoding="utf-8"), portal_slug="at")
    paragraph_labels = [
        c.numbering_label
        for c in root.walk()
        if c.numbering_label and c.numbering_label.startswith("§")
    ]
    assert paragraph_labels, "AT portal config should open at least one §-marker clause"


def test_eu_portal_uses_markdown_headings_only() -> None:
    cfg = load_portal_config("eu")
    assert cfg.patterns == []
    assert cfg.treat_unmatched_bold_as_heading is False
    root = parse(EU_FIXTURE.read_text(encoding="utf-8"), portal_slug="eu")
    # At least one markdown-heading clause survives at the top level
    # (h1 / h2 produced the doc's outline).
    headings = [c.heading_text for c in root.walk() if c.heading_text]
    assert any("BEREC" in (h or "") for h in headings)


# ---------------------------------------------------------------------------
# YAML body sanity — the snapshot is reachable and parses cleanly
# ---------------------------------------------------------------------------


def test_default_yaml_safe_loads_to_dict() -> None:
    from importlib import resources

    text = (
        resources.files("horizons_core.core.alignment.parser_configs")
        .joinpath("_default.yaml")
        .read_text(encoding="utf-8")
    )
    raw = yaml.safe_load(text)
    assert isinstance(raw, dict)
    assert "patterns" in raw
    assert isinstance(raw["patterns"], list)
