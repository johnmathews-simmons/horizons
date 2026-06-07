"""Unit tests for ``horizons_core.core.alignment.parser``.

The parser turns markdown into an immutable ``Clause`` tree using a
configurable list of structural patterns. Tests fall into four groups:

* IE fixture — heading-anchored substrate (bold-wrapped numbering).
* CZ fixture — inline-numbered substrate (markers flowing in prose).
* Synthetic input — small inline strings that exercise specific
  algorithmic branches (heading depth, pending heading, stack pop, etc.).
* Edge cases — empty / headings-only / body-only / disabled bold-heading.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from horizons_core.core.alignment import (
    Clause,
    ParserConfig,
    StructuralPattern,
    default_parser_config,
    default_patterns,
    parse,
)
from horizons_core.core.alignment.parser import (
    _has_boundary_before,
    _is_bold_only,
    _slugify,
    _TreeBuilder,
)
from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterable

REPO_ROOT = Path(__file__).resolve().parents[3]
IE_FIXTURE = REPO_ROOT / "data" / "samples" / "ie-27732019-v1.md"
CZ_FIXTURE = REPO_ROOT / "data" / "samples" / "cz-29662776-v1.md"


def _find(root: Clause, path: tuple[str, ...]) -> Clause | None:
    for node in root.walk():
        if node.path == path:
            return node
    return None


def _paths(root: Clause) -> Iterable[tuple[str, ...]]:
    for node in root.walk():
        yield node.path


# ---------------------------------------------------------------------------
# Heading-anchored: Irish Statute Book fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ie_tree() -> Clause:
    return parse(IE_FIXTURE.read_text(encoding="utf-8"))


def test_ie_three_parts_at_top_level(ie_tree: Clause) -> None:
    parts = [
        c for c in ie_tree.children if c.numbering_label and c.numbering_label.startswith("PART")
    ]
    labels = [p.numbering_label for p in parts]
    assert labels == ["PART 1", "PART 2", "PART 3"]


def test_ie_part1_has_heading(ie_tree: Clause) -> None:
    part1 = _find(ie_tree, ("PART 1",))
    assert part1 is not None
    assert part1.heading_text is not None
    assert "enacted by the Oireachtas" in part1.heading_text


def test_ie_section_1_has_title_attached_from_bold_paragraph(ie_tree: Clause) -> None:
    section1 = _find(ie_tree, ("PART 1", "1."))
    assert section1 is not None
    assert section1.numbering_label == "1."
    assert section1.heading_text == "Short title, citation and commencement"


def test_ie_section_1_subsections_nest_correctly(ie_tree: Clause) -> None:
    section1 = _find(ie_tree, ("PART 1", "1."))
    assert section1 is not None
    sub_labels = [c.numbering_label for c in section1.children]
    assert sub_labels == ["(1)", "(2)", "(3)"]


def test_ie_deep_path_part_section_letter_roman_reachable(ie_tree: Clause) -> None:
    deep = _find(ie_tree, ("PART 2", "4.", "(a)", "(i)"))
    assert deep is not None
    assert deep.numbering_label == "(i)"
    assert deep.body_text  # non-empty


def test_ie_letter_b_does_not_collide_with_roman(ie_tree: Clause) -> None:
    # "(b)" must remain depth-6 letter even though several Roman-ambiguous
    # single chars exist in the alphabet.
    letter_b = _find(ie_tree, ("PART 2", "4.", "(b)"))
    assert letter_b is not None
    assert letter_b.numbering_label == "(b)"


# ---------------------------------------------------------------------------
# Inline-numbered: Czech fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cz_tree() -> Clause:
    return parse(CZ_FIXTURE.read_text(encoding="utf-8"))


def test_cz_top_level_cast(cz_tree: Clause) -> None:
    casts = [
        c for c in cz_tree.children if c.numbering_label and c.numbering_label.startswith("ČÁST")
    ]
    assert any(c.numbering_label == "ČÁST PRVNÍ" for c in casts)


def test_cz_clanek_nests_under_cast(cz_tree: Clause) -> None:
    clanek = _find(cz_tree, ("ČÁST PRVNÍ", "Čl. I"))
    assert clanek is not None
    assert clanek.numbering_label == "Čl. I"


def test_cz_numbered_items_nest_under_clanek(cz_tree: Clause) -> None:
    clanek = _find(cz_tree, ("ČÁST PRVNÍ", "Čl. I"))
    assert clanek is not None
    labels = {c.numbering_label for c in clanek.children if c.numbering_label}
    assert "1." in labels
    assert "2." in labels


def test_cz_subsection_marker_recognised(cz_tree: Clause) -> None:
    def is_paren_digit(label: str | None) -> bool:
        return bool(label and label.startswith("(") and label[1].isdigit())

    assert any(is_paren_digit(node.numbering_label) for node in cz_tree.walk())


# ---------------------------------------------------------------------------
# Synthetic markdown: precise control over the input
# ---------------------------------------------------------------------------


def test_root_is_empty_clause() -> None:
    root = parse("")
    assert root.path == ()
    assert root.heading_text is None
    assert root.body_text == ""
    assert root.numbering_label is None
    assert root.children == ()


def test_single_paragraph_no_markers_becomes_unlabelled_leaf() -> None:
    root = parse("Just some prose.")
    assert len(root.children) == 1
    leaf = root.children[0]
    assert leaf.numbering_label is None
    assert leaf.body_text == "Just some prose."


def test_five_deep_ladder_part_section_subsection_letter_roman() -> None:
    md = (
        "**PART 1**\n\n"
        "**Title of part**\n\n"
        "**Title of section**\n\n"
        "**1\\.** (1\\) outer subsection text.\n\n"
        "(a) letter para text.\n\n"
        "(i) roman subpara text.\n"
    )
    root = parse(md)
    target = _find(root, ("PART 1", "1.", "(1)", "(a)", "(i)"))
    assert target is not None
    assert target.body_text == "roman subpara text."


def test_markdown_headings_create_clauses_with_explicit_heading_text() -> None:
    md = "# Top\n\n## Middle\n\nSome body.\n"
    root = parse(md)
    assert len(root.children) == 1
    top = root.children[0]
    assert top.heading_text == "Top"
    assert top.numbering_label is None
    middle = top.children[0]
    assert middle.heading_text == "Middle"
    body_leaf = middle.children[0]
    assert body_leaf.body_text == "Some body."


def test_heading_depth_offset_shifts_markdown_headings_below_part_level() -> None:
    cfg = ParserConfig(heading_depth_offset=2)
    root = parse("# title\n\nbody.", config=cfg)
    top = root.children[0]
    # depth 1 + offset 2 = 3, so the heading-clause's children paths grow normally
    assert top.heading_text == "title"


def test_pending_heading_attaches_to_next_numbered_clause() -> None:
    md = "**PART 1**\n\n**A**\n\n**B**\n\n**1\\.** text.\n"
    root = parse(md)
    part1 = root.children[0]
    assert part1.heading_text == "A"  # first bold-only attached to PART 1
    section = part1.children[0]
    assert section.numbering_label == "1."
    assert section.heading_text == "B"  # second bold-only became pending → section


def test_bold_heading_after_open_clause_does_not_attach_to_stack_top() -> None:
    # Regression for the "Amendment of section N of Principal Act"
    # off-by-one observed on ie-27732019-v1.md (see journal entry
    # 260607-parser-heading-off-by-one.md). When a bold-only heading
    # arrives while the stack top has already accumulated body or
    # children, the heading must defer via _pending_heading and bind
    # to the *next* opened structural clause — not get absorbed by the
    # currently-open clause.
    md = (
        "**PART 1**\n\n"
        "**1\\.** first section body.\n\n"
        "(a) bullet inside section 1.\n\n"
        "**Heading meant for section 2**\n\n"
        "**2\\.** second section body.\n"
    )
    root = parse(md)
    # Section 1 was opened with no pending heading — stays None. (The
    # PART-level heading behaviour is exercised by the test above.)
    section1 = _find(root, ("PART 1", "1."))
    assert section1 is not None
    assert section1.heading_text is None
    # (a) is the stack top when the bold heading arrives. (a) has body,
    # so it must NOT absorb the heading.
    letter_a = _find(root, ("PART 1", "1.", "(a)"))
    assert letter_a is not None
    assert letter_a.heading_text is None
    # The heading must land on the next opened structural clause.
    section2 = _find(root, ("PART 1", "2."))
    assert section2 is not None
    assert section2.heading_text == "Heading meant for section 2"


def test_ie_section_11_heading_describes_what_it_actually_amends(
    ie_tree: Clause,
) -> None:
    # In the source markdown the heading "Amendment of section 10 of
    # Principal Act" precedes "**11\\.** Section 10(2A)..." — because
    # clause 11 of the Act is what amends section 10 of the Principal
    # Act. Before the parser fix, the heading was absorbed by a tail
    # leaf inside clause 10's subtree and clause 11 carried the *next*
    # heading instead. This guards the regression on real fixture text.
    section_11 = _find(ie_tree, ("PART 2", "11."))
    assert section_11 is not None
    assert section_11.heading_text == "Amendment of section 10 of Principal Act"


def test_treat_unmatched_bold_as_heading_disabled_creates_leaves() -> None:
    cfg = ParserConfig(treat_unmatched_bold_as_heading=False)
    md = "**Just a title**\n\nMore prose."
    root = parse(md, config=cfg)
    labels_and_bodies = [(c.numbering_label, c.body_text) for c in root.children]
    assert (None, "Just a title") in labels_and_bodies
    assert (None, "More prose.") in labels_and_bodies


def test_unrecognised_paragraph_becomes_leaf_with_label_none_per_q4() -> None:
    md = "**1\\.** opener.\n\nStray prose with no marker.\n\n(a) letter.\n"
    root = parse(md)
    section = root.children[0]
    # Children of section: leaf (stray prose) then (a)
    labels = [c.numbering_label for c in section.children]
    assert None in labels
    assert "(a)" in labels


def test_prefix_text_before_first_marker_becomes_leaf() -> None:
    md = "Some preamble. **PART 1**\n"
    root = parse(md)
    # The "Some preamble." prefix before PART becomes an own leaf at the root.
    bodies = [c.body_text for c in root.children]
    assert "Some preamble." in bodies
    part = next(c for c in root.children if c.numbering_label == "PART 1")
    assert part is not None


def test_code_block_content_lands_in_current_parent_body() -> None:
    md = "**PART 1**\n\n```\nliteral block\n```\n"
    root = parse(md)
    part1 = root.children[0]
    assert "literal block" in part1.body_text


def test_html_block_content_treated_as_loose_text() -> None:
    md = "**PART 1**\n\n<div>raw html</div>\n"
    root = parse(md)
    part1 = root.children[0]
    assert "raw html" in part1.body_text


def test_loose_text_with_only_whitespace_is_ignored() -> None:
    md = "**PART 1**\n\n```\n   \n```\n"
    root = parse(md)
    part1 = root.children[0]
    assert part1.body_text == ""


def test_sibling_headings_with_same_slug_get_disambiguated() -> None:
    """Three sibling headings that slugify to the same string must produce
    distinct paths. Backstop for the WU8.0 FR-31702142 (ACPR) fixture:
    three ``# Position de la Commission`` headings under different Griefs
    would otherwise collide on ``clauses_unique_path_per_version`` when
    the synthetic-v2 staging path inserts them into the same document
    version.
    """
    md = (
        "# Position de la Commission\n\nfirst.\n\n"
        "# Position de la Commission\n\nsecond.\n\n"
        "# Position de la Commission\n\nthird.\n"
    )
    root = parse(md)
    segments = [c.path[-1] for c in root.children]
    assert segments == [
        "position-de-la-commission",
        "position-de-la-commission-2",
        "position-de-la-commission-3",
    ]
    # Same input twice → same paths (alignment relies on stability).
    again = parse(md)
    assert [c.path for c in again.children] == [c.path for c in root.children]


def test_sibling_labels_with_same_text_get_disambiguated() -> None:
    """Two siblings with the same structural label still get distinct paths.
    Rare in legal docs but defensive against errata-style re-issues.
    """
    md = "**1\\.** first.\n\n**1\\.** repeated.\n"
    root = parse(md)
    # Both clauses parsed with numbering_label == "1.", but their path
    # segments differ so the unique constraint won't fire downstream.
    assert root.children[0].path[-1] != root.children[1].path[-1]
    # The first occurrence keeps the base segment.
    assert root.children[0].path[-1] == "1."
    assert root.children[1].path[-1] == "1.-2"


def test_sibling_pop_when_same_depth_marker_appears_again() -> None:
    md = "**1\\.** first.\n\n**2\\.** second.\n"
    root = parse(md)
    labels = [c.numbering_label for c in root.children]
    assert labels == ["1.", "2."]


def test_walk_yields_pre_order_dfs() -> None:
    md = "**1\\.** a.\n\n(a) inside.\n\n**2\\.** b.\n"
    root = parse(md)
    walked = [n.numbering_label for n in root.walk()]
    assert walked[0] is None  # root
    assert walked[1] == "1."
    assert walked[2] == "(a)"
    assert walked[3] == "2."


# ---------------------------------------------------------------------------
# Pattern and config helpers
# ---------------------------------------------------------------------------


def test_default_config_uses_default_patterns() -> None:
    cfg = default_parser_config()
    assert any(p.name == "ie_part" for p in cfg.patterns)
    assert any(p.name == "cz_cast" for p in cfg.patterns)


def test_default_patterns_are_immutable_via_pydantic_frozen() -> None:
    cfg = default_parser_config()
    with pytest.raises(ValidationError):
        cfg.patterns[0].name = "mutated"  # type: ignore[misc]


def test_custom_pattern_list_can_override_defaults() -> None:
    cfg = ParserConfig(
        patterns=[
            StructuralPattern(
                name="custom_marker", regex=r"§\s*\d+", depth=1, requires_boundary=False
            ),
        ]
    )
    md = "Intro § 42 first body.\n"
    root = parse(md, config=cfg)
    # The marker should open a clause at depth 1
    paths = list(_paths(root))
    assert any(p and p[0].startswith("§") for p in paths)


def test_default_patterns_returns_fresh_list() -> None:
    # Independent lists so mutation in one config doesn't leak into another.
    a = default_patterns()
    b = default_patterns()
    assert a == b
    assert a is not b


def test_inline_code_text_contributes_to_body() -> None:
    md = "Some `inline code` in a paragraph.\n"
    root = parse(md)
    assert len(root.children) == 1
    assert "inline code" in root.children[0].body_text


def test_bullet_list_tokens_are_skipped_inner_paragraphs_still_processed() -> None:
    md = "- alpha\n- beta\n"
    root = parse(md)
    # Each list item's paragraph becomes an unlabelled leaf at the root.
    bodies = [c.body_text for c in root.children]
    assert "alpha" in bodies
    assert "beta" in bodies


def test_softbreak_in_paragraph_yields_single_spaces_between_lines() -> None:
    # Two text lines in one paragraph: markdown-it emits softbreak between.
    md = "first line\nsecond line\n"
    root = parse(md)
    assert root.children[0].body_text == "first line second line"


def test_empty_markdown_heading_is_ignored() -> None:
    md = "#  \n\nbody.\n"
    root = parse(md)
    # No top-level heading clause; only the body leaf remains.
    assert all(c.numbering_label is None for c in root.children)
    assert "body." in [c.body_text for c in root.children]


def test_whitespace_only_prefix_before_marker_skipped() -> None:
    cfg = ParserConfig(
        patterns=[
            StructuralPattern(
                name="leading_marker",
                regex=r"§\d+",
                depth=1,
                requires_boundary=False,
            ),
        ]
    )
    # The markdown paragraph plain text is "   §1 body" — prefix is all
    # whitespace; the marker still opens at depth 1.
    root = parse("  §1 body.\n", config=cfg)
    section = next((c for c in root.children if c.numbering_label == "§1"), None)
    assert section is not None
    assert section.body_text == "body."


# ---------------------------------------------------------------------------
# Private helpers (direct unit coverage)
# ---------------------------------------------------------------------------


def test_has_boundary_before_start_of_string() -> None:
    assert _has_boundary_before("(1) text", 0) is True


def test_has_boundary_before_directly_after_non_space_is_rejected() -> None:
    assert _has_boundary_before("ab(1)", 2) is False


def test_has_boundary_before_after_terminator_then_space_accepts() -> None:
    assert _has_boundary_before("end. (1)", 5) is True


def test_has_boundary_before_after_non_terminator_then_space_rejects() -> None:
    assert _has_boundary_before("word (1)", 5) is False


def test_has_boundary_before_after_multiple_whitespaces_walks_back() -> None:
    assert _has_boundary_before("end.   (1)", 7) is True


def test_has_boundary_before_only_whitespace_before_pos_treated_as_start() -> None:
    assert _has_boundary_before("   (1)", 3) is True


def test_is_bold_only_returns_false_for_empty_text() -> None:
    assert _is_bold_only("", []) is False


def test_is_bold_only_returns_false_for_whitespace_only_text() -> None:
    assert _is_bold_only("   ", []) is False


def test_is_bold_only_true_for_fully_bold_paragraph() -> None:
    text = "Bold Title"
    assert _is_bold_only(text, [(0, len(text))]) is True


def test_is_bold_only_false_when_some_chars_outside_bold_ranges() -> None:
    text = "Bold and not bold"
    assert _is_bold_only(text, [(0, 4)]) is False


def test_is_bold_only_clamps_bold_ranges_that_overrun_text_length() -> None:
    text = "Short"
    assert _is_bold_only(text, [(0, 999)]) is True


def test_slugify_strips_punctuation_and_lowercases() -> None:
    assert _slugify("Hello, World!") == "hello-world"


def test_slugify_returns_empty_for_all_punctuation_input() -> None:
    assert _slugify("!!!---!!!") == ""


def test_slugify_truncates_at_64_chars() -> None:
    out = _slugify("a" * 100)
    assert len(out) == 64


def test_whitespace_only_prefix_within_paragraph_creates_no_stray_leaf() -> None:
    # Drive the private builder directly: a paragraph whose plain text
    # begins with whitespace followed by a marker. Real markdown
    # strips leading whitespace from paragraph blocks, so we can't
    # express this through ``parse()`` — but the defensive branch
    # in :meth:`_TreeBuilder.consume_paragraph` still needs coverage.
    cfg = ParserConfig(
        patterns=[
            StructuralPattern(
                name="dollar_marker",
                regex=r"\$\d+",
                depth=1,
                requires_boundary=False,
            ),
        ]
    )
    builder = _TreeBuilder(cfg)
    builder.consume_paragraph(" $1 body", [])
    root = builder.finalize()
    # No leaf for the leading whitespace; only the marker clause.
    assert len(root.children) == 1
    assert root.children[0].numbering_label == "$1"


def test_clause_is_hashable_and_immutable() -> None:
    leaf = Clause(path=("x",), heading_text=None, body_text="a", numbering_label="x")
    {leaf}  # noqa: B018  must hash without error
    with pytest.raises(FrozenInstanceError):
        leaf.body_text = "b"  # type: ignore[misc]
