"""Tests for the WU2.3 alignment pipeline (:mod:`horizons_core.core.alignment.align`).

Coverage groups:

* :class:`ChangeEvent` — validators for ADDED / REMOVED / MODIFIED / MOVED
  field-presence rules, frozen-model contract, confidence bounds.
* :func:`align` — identity, insert, delete, modify, swap, monotonic
  non-crossing constraint, boilerplate heading guard, short-body
  fallback, tuning-config plumbing.
* End-to-end against a real IE clause body from ``data/samples/``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from horizons_core.core.alignment import (
    ChangeEvent,
    Clause,
    TuningConfig,
    align,
    default_tuning_config,
    parse,
)
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
IE_FIXTURE = REPO_ROOT / "data" / "samples" / "ie-27732019-v1.md"


# ---------------------------------------------------------------------------
# Test fixtures: hand-built clause trees with realistic long bodies
# ---------------------------------------------------------------------------

# Each body is long enough (>= 25 words) that the default shingle_k=5
# yields a non-trivial shingle set. Short-body variants live further down.
BODY_A = (
    "an employer who fails to comply with the provisions of this section "
    "shall be guilty of an offence and liable on summary conviction to a "
    "fine not exceeding five thousand euro or to imprisonment for a term "
    "not exceeding six months or to both"
)
BODY_B = (
    "the minister may by regulation prescribe the manner in which an "
    "applicant shall furnish particulars to the social insurance fund "
    "including the form of any required declaration and the documents "
    "that shall accompany an application made under this part"
)
BODY_C = (
    "where an employer becomes insolvent within the meaning of this act "
    "the relevant date for the purposes of section six shall be "
    "ascertained by reference to the date of the appointment of a "
    "liquidator examiner or receiver as appropriate to the case"
)
BODY_D = (
    "in calculating any payment due under section six the minister shall "
    "have regard to the maximum weekly sum prescribed by order under "
    "subsection three and to any prior payment already made out of the "
    "social insurance fund in respect of the same employee"
)


def _node(
    path: tuple[str, ...],
    body: str,
    heading: str | None = None,
    children: tuple[Clause, ...] = (),
) -> Clause:
    return Clause(
        path=path,
        heading_text=heading,
        body_text=body,
        numbering_label=None,
        children=children,
    )


def _root(*children: Clause) -> Clause:
    return Clause(
        path=(),
        heading_text=None,
        body_text="",
        numbering_label=None,
        children=children,
    )


def _three_section_tree() -> Clause:
    return _root(
        _node(("1.",), BODY_A, heading="Short title"),
        _node(("2.",), BODY_B, heading="Interpretation"),
        _node(("3.",), BODY_C, heading="Application"),
    )


# ---------------------------------------------------------------------------
# ChangeEvent: validators and shape
# ---------------------------------------------------------------------------


def test_added_event_constructs_with_after_fields_only() -> None:
    ev = ChangeEvent(
        change_type="ADDED",
        after_path=("2A.",),
        after_text="brand new clause body text long enough to be coherent",
        alignment_confidence=1.0,
    )
    assert ev.before_path is None
    assert ev.before_text is None
    assert ev.before_clause_uid is None
    assert ev.after_path == ("2A.",)


def test_removed_event_constructs_with_before_fields_only() -> None:
    ev = ChangeEvent(
        change_type="REMOVED",
        before_path=("3.",),
        before_text="a clause that was deleted between v1 and v2 of this act",
        alignment_confidence=1.0,
    )
    assert ev.after_path is None
    assert ev.after_text is None


def test_modified_event_constructs_with_both_sides_and_differing_text() -> None:
    ev = ChangeEvent(
        change_type="MODIFIED",
        before_path=("2.",),
        after_path=("2.",),
        before_text="original body text version one of this clause",
        after_text="amended body text version two of this clause",
        alignment_confidence=0.9,
    )
    assert ev.before_text != ev.after_text


def test_moved_event_constructs_with_identical_text_and_different_paths() -> None:
    body = "identical body text on both sides because this is a MOVED event"
    ev = ChangeEvent(
        change_type="MOVED",
        before_path=("2.",),
        after_path=("3.",),
        before_text=body,
        after_text=body,
        alignment_confidence=1.0,
    )
    assert ev.before_text == ev.after_text
    assert ev.before_path != ev.after_path


def test_change_event_is_frozen() -> None:
    ev = ChangeEvent(
        change_type="ADDED",
        after_path=("X.",),
        after_text="some body",
        alignment_confidence=1.0,
    )
    with pytest.raises(ValidationError):
        ev.alignment_confidence = 0.5  # type: ignore[misc]


def test_added_rejects_before_side_fields() -> None:
    with pytest.raises(ValidationError, match="must not carry before-side"):
        ChangeEvent(
            change_type="ADDED",
            before_path=("X.",),
            after_path=("Y.",),
            after_text="body",
            alignment_confidence=1.0,
        )


def test_added_requires_after_path_and_text() -> None:
    with pytest.raises(ValidationError, match="must carry after_path and after_text"):
        ChangeEvent(
            change_type="ADDED",
            after_path=("Y.",),
            alignment_confidence=1.0,
        )


def test_removed_rejects_after_side_fields() -> None:
    with pytest.raises(ValidationError, match="must not carry after-side"):
        ChangeEvent(
            change_type="REMOVED",
            before_path=("X.",),
            before_text="body",
            after_path=("Y.",),
            alignment_confidence=1.0,
        )


def test_removed_requires_before_path_and_text() -> None:
    with pytest.raises(ValidationError, match="must carry before_path and before_text"):
        ChangeEvent(
            change_type="REMOVED",
            before_path=("X.",),
            alignment_confidence=1.0,
        )


def test_modified_requires_both_sides() -> None:
    with pytest.raises(ValidationError, match="must carry before/after"):
        ChangeEvent(
            change_type="MODIFIED",
            before_path=("X.",),
            before_text="body",
            alignment_confidence=0.9,
        )


def test_modified_rejects_identical_text() -> None:
    with pytest.raises(ValidationError, match="require differing text"):
        ChangeEvent(
            change_type="MODIFIED",
            before_path=("X.",),
            after_path=("X.",),
            before_text="same body",
            after_text="same body",
            alignment_confidence=0.9,
        )


def test_moved_requires_both_sides() -> None:
    with pytest.raises(ValidationError, match="must carry before/after"):
        ChangeEvent(
            change_type="MOVED",
            before_path=("X.",),
            before_text="body",
            alignment_confidence=1.0,
        )


def test_moved_rejects_differing_text() -> None:
    with pytest.raises(ValidationError, match="require identical text"):
        ChangeEvent(
            change_type="MOVED",
            before_path=("X.",),
            after_path=("Y.",),
            before_text="body one",
            after_text="body two",
            alignment_confidence=1.0,
        )


def test_moved_rejects_identical_paths() -> None:
    with pytest.raises(ValidationError, match="require distinct paths"):
        ChangeEvent(
            change_type="MOVED",
            before_path=("X.",),
            after_path=("X.",),
            before_text="same body",
            after_text="same body",
            alignment_confidence=1.0,
        )


def test_confidence_must_be_in_unit_interval_exclusive_zero() -> None:
    with pytest.raises(ValidationError):
        ChangeEvent(
            change_type="ADDED",
            after_path=("X.",),
            after_text="body",
            alignment_confidence=0.0,
        )
    with pytest.raises(ValidationError):
        ChangeEvent(
            change_type="ADDED",
            after_path=("X.",),
            after_text="body",
            alignment_confidence=1.5,
        )


# ---------------------------------------------------------------------------
# align: identity case
# ---------------------------------------------------------------------------


def test_align_identity_returns_no_events() -> None:
    tree = _three_section_tree()
    assert align(tree, tree) == []


def test_align_idempotent_re_ingest_is_zero_rows() -> None:
    # Construct two structurally-equal but distinct objects.
    a = _three_section_tree()
    b = _three_section_tree()
    assert a is not b
    assert align(a, b) == []


# ---------------------------------------------------------------------------
# align: pure structural transforms
# ---------------------------------------------------------------------------


def test_align_insert_emits_single_added_with_no_phantom_moveds() -> None:
    v1 = _three_section_tree()
    v2 = _root(
        _node(("1.",), BODY_A, heading="Short title"),
        _node(("2.",), BODY_B, heading="Interpretation"),
        _node(("2A.",), BODY_D, heading="Saver"),
        _node(("3.",), BODY_C, heading="Application"),
    )
    events = align(v1, v2)
    assert [e.change_type for e in events] == ["ADDED"]
    [added] = events
    assert added.after_path == ("2A.",)
    assert added.after_text == BODY_D
    assert added.before_path is None


def test_align_delete_emits_single_removed() -> None:
    v1 = _three_section_tree()
    v2 = _root(
        _node(("1.",), BODY_A, heading="Short title"),
        _node(("3.",), BODY_C, heading="Application"),
    )
    events = align(v1, v2)
    assert [e.change_type for e in events] == ["REMOVED"]
    [removed] = events
    assert removed.before_path == ("2.",)
    assert removed.before_text == BODY_B


def test_align_modify_emits_single_modified_with_both_texts() -> None:
    v1 = _three_section_tree()
    amended = BODY_B + " and any further provisions the minister may by order specify."
    v2 = _root(
        _node(("1.",), BODY_A, heading="Short title"),
        _node(("2.",), amended, heading="Interpretation"),
        _node(("3.",), BODY_C, heading="Application"),
    )
    events = align(v1, v2)
    assert [e.change_type for e in events] == ["MODIFIED"]
    [modified] = events
    assert modified.before_text == BODY_B
    assert modified.after_text == amended
    assert modified.before_path == ("2.",) == modified.after_path


def test_align_swap_at_same_depth_emits_two_moved_events() -> None:
    v1 = _three_section_tree()
    v2 = _root(
        _node(("1.",), BODY_A, heading="Short title"),
        # Headings track the clause's identity; positions 2 and 3 swap.
        _node(("2.",), BODY_C, heading="Application"),
        _node(("3.",), BODY_B, heading="Interpretation"),
    )
    events = align(v1, v2)
    assert all(e.change_type == "MOVED" for e in events)
    assert len(events) == 2
    by_before = {e.before_path: e for e in events}
    assert by_before[("2.",)].after_path == ("3.",)
    assert by_before[("2.",)].before_text == by_before[("2.",)].after_text == BODY_B
    assert by_before[("3.",)].after_path == ("2.",)
    assert by_before[("3.",)].before_text == by_before[("3.",)].after_text == BODY_C


def test_align_paired_clause_with_different_path_and_different_text_is_modified_not_moved() -> None:
    # Q3 rule: any text drift downgrades MOVED to MODIFIED; before_path !=
    # after_path on the event itself signals the move.
    v1 = _root(_node(("2.",), BODY_B, heading="Interpretation"))
    amended = BODY_B + " with one extra clause appended for further clarity."
    v2 = _root(_node(("3.",), amended, heading="Interpretation"))
    events = align(v1, v2)
    assert [e.change_type for e in events] == ["MODIFIED"]
    [ev] = events
    assert ev.before_path == ("2.",)
    assert ev.after_path == ("3.",)
    assert ev.before_text != ev.after_text


# ---------------------------------------------------------------------------
# align: pass-2 heading-match corroboration (boilerplate guard)
# ---------------------------------------------------------------------------


def test_align_does_not_pair_boilerplate_headings_with_disjoint_content() -> None:
    # Two clauses both titled "Definitions" but their bodies are
    # completely disjoint. Pass 2 must NOT pair them (this is the
    # docs/2 regression case). Pass 3 won't pair them either since
    # their shingle sets are disjoint. Outcome: REMOVED + ADDED.
    v1 = _root(_node(("2.",), BODY_B, heading="Definitions"))
    v2 = _root(_node(("2.",), BODY_C, heading="Definitions"))
    events = align(v1, v2)
    types = sorted(e.change_type for e in events)
    assert types == ["ADDED", "REMOVED"]


def test_align_pairs_same_heading_when_content_meets_threshold() -> None:
    # Sanity check that pass 2 does pair when corroboration holds.
    v1 = _root(_node(("2.",), BODY_B, heading="Interpretation"))
    v2 = _root(_node(("2A.",), BODY_B, heading="Interpretation"))
    events = align(v1, v2)
    assert [e.change_type for e in events] == ["MOVED"]


# ---------------------------------------------------------------------------
# align: pass-2 short-body fallback (heading + exact body equality)
# ---------------------------------------------------------------------------


def test_align_short_body_with_matching_heading_pairs_on_exact_text_equality() -> None:
    # Bodies are short enough that the default shingle_k=5 returns an
    # empty set. Pass 2's exact-text fallback must still pair them.
    short = "in this Act"
    v1 = _root(_node(("2.",), short, heading="Definition"))
    v2 = _root(_node(("2.",), short, heading="Definition"))
    assert align(v1, v2) == []  # identity short circuit


def test_align_short_body_with_matching_heading_and_distinct_bodies_does_not_pair() -> None:
    # The short-body fallback requires exact text equality; without it,
    # no pairing happens even though headings match.
    v1 = _root(_node(("2.",), "in this Act", heading="Definition"))
    v2 = _root(_node(("2.",), "for the purposes hereof", heading="Definition"))
    events = align(v1, v2)
    assert sorted(e.change_type for e in events) == ["ADDED", "REMOVED"]


def test_align_short_body_moved_path_emits_moved_event() -> None:
    short = "in this Act"
    v1 = _root(_node(("2.",), short, heading="Definition"))
    v2 = _root(_node(("3.",), short, heading="Definition"))
    events = align(v1, v2)
    assert [e.change_type for e in events] == ["MOVED"]


def test_align_short_body_one_side_short_other_long_does_not_pair() -> None:
    # One side has shingles, the other doesn't. Pass 2 should not pair
    # them (jaccard against an empty signature isn't meaningful and we
    # bail to the exact-equality branch, which fails too).
    v1 = _root(_node(("2.",), "short stub", heading="Section"))
    v2 = _root(_node(("2.",), BODY_B, heading="Section"))
    events = align(v1, v2)
    assert sorted(e.change_type for e in events) == ["ADDED", "REMOVED"]


# ---------------------------------------------------------------------------
# align: pass-3 content similarity with monotonic-order DP
# ---------------------------------------------------------------------------


def test_align_pass3_pairs_unheadinged_clauses_by_content_alone() -> None:
    # No headings at all — pass 2 cannot fire. Pass 3 LSH catches
    # the identical-content pair and emits MOVED for the path change.
    v1 = _root(_node(("1.",), BODY_A))
    v2 = _root(_node(("2.",), BODY_A))
    events = align(v1, v2)
    assert [e.change_type for e in events] == ["MOVED"]


def test_align_pass3_monotonic_constraint_forbids_crossing_pairs() -> None:
    # v1 = [BODY_A, BODY_B]; v2 = [BODY_B, BODY_A]. With no headings,
    # pass 3 would naively want to pair (A, A) and (B, B), but those
    # cross. The monotonic DP must pick at most one pair; the other
    # becomes REMOVED + ADDED.
    v1 = _root(_node(("1.",), BODY_A), _node(("2.",), BODY_B))
    v2 = _root(_node(("1.",), BODY_B), _node(("2.",), BODY_A))
    events = align(v1, v2)
    pairs = [e for e in events if e.change_type in {"MOVED", "MODIFIED"}]
    removed = [e for e in events if e.change_type == "REMOVED"]
    added = [e for e in events if e.change_type == "ADDED"]
    assert len(pairs) == 1
    assert len(removed) == 1
    assert len(added) == 1


def test_align_pass3_pair_below_threshold_does_not_match() -> None:
    # Two bodies with no shared shingles can't possibly clear the LSH
    # post-filter at the default 0.7 threshold.
    v1 = _root(_node(("1.",), BODY_A))
    v2 = _root(_node(("2.",), BODY_D))
    events = align(v1, v2)
    assert sorted(e.change_type for e in events) == ["ADDED", "REMOVED"]


def test_align_pass3_dp_walks_skip_v2_in_unbalanced_grid() -> None:
    # v1 length 1, v2 length 3, only (0,0) is in sim. The DP at cell
    # (1,2) and (1,3) must walk skip_v2 to reach the pair at (1,1).
    # The backtrace path also exercises the ``b -= 1`` arm.
    v1 = _root(_node(("V1.",), BODY_A))
    near_a = BODY_A.replace("employer", "operator")
    v2 = _root(
        _node(("V2A.",), near_a),
        _node(("V2B.",), BODY_B),
        _node(("V2C.",), BODY_C),
    )
    events = align(v1, v2)
    pairs = [e for e in events if e.change_type in {"MOVED", "MODIFIED"}]
    assert len(pairs) == 1
    assert [e for e in events if e.change_type == "ADDED"]


def test_align_pass3_filters_same_side_lsh_v1_duplicates() -> None:
    # Two near-identical v1 clauses with disjoint paths and no headings.
    # LSH will surface the (v1, v1) candidate pair; the pipeline must
    # drop it (cross-side only) — otherwise the unrelated v2 clause
    # would risk being mis-paired through a same-side spurious score.
    v1 = _root(
        _node(("V1A.",), BODY_A),
        _node(("V1B.",), BODY_A),
    )
    v2 = _root(_node(("V2.",), BODY_D))
    events = align(v1, v2)
    types = sorted(e.change_type for e in events)
    assert types == ["ADDED", "REMOVED", "REMOVED"]


def test_align_pass3_dp_rejects_lower_score_pair_when_skip_already_dominates() -> None:
    # Exercises the "pair_key in sim but score < best" branch of the DP.
    # v1 has two clauses similar to a single v2 clause; the DP must
    # only take the higher-scoring pair (v1[0] ↔ v2[0]) and reject
    # the available-but-worse pair at (v1[1], v2[0]).
    v1 = _root(
        _node(("V1A.",), BODY_A),
        _node(("V1B.",), BODY_A.replace("employer", "operator")),
    )
    v2 = _root(_node(("V2.",), BODY_A))
    events = align(v1, v2)
    pairs = [e for e in events if e.change_type in {"MOVED", "MODIFIED"}]
    removed = [e for e in events if e.change_type == "REMOVED"]
    assert len(pairs) == 1
    assert len(removed) == 1


def test_align_pass3_filters_same_side_lsh_v2_duplicates() -> None:
    # Mirror of the previous test — two near-identical v2 clauses,
    # exercising the same-side-skip branch from the v2 direction.
    v1 = _root(_node(("V1.",), BODY_D))
    v2 = _root(
        _node(("V2A.",), BODY_A),
        _node(("V2B.",), BODY_A),
    )
    events = align(v1, v2)
    types = sorted(e.change_type for e in events)
    assert types == ["ADDED", "ADDED", "REMOVED"]


# ---------------------------------------------------------------------------
# align: tuning-config plumbing
# ---------------------------------------------------------------------------


def test_align_accepts_explicit_tuning_config() -> None:
    custom = TuningConfig(
        shingle_k=3,
        signature_size=64,
        lsh_bands=8,
        similarity_threshold=0.5,
    )
    tree = _three_section_tree()
    assert align(tree, tree, tuning=custom) == []


def test_align_default_tuning_matches_default_tuning_config() -> None:
    # The default arg is wired to default_tuning_config(); aligning under
    # either form must produce the same event set.
    v1 = _three_section_tree()
    v2 = _root(
        _node(("1.",), BODY_A, heading="Short title"),
        _node(("2.",), BODY_B + " amended.", heading="Interpretation"),
        _node(("3.",), BODY_C, heading="Application"),
    )
    explicit = align(v1, v2, tuning=default_tuning_config())
    implicit = align(v1, v2)
    assert explicit == implicit


# ---------------------------------------------------------------------------
# align: skip pure structural containers (no body_text)
# ---------------------------------------------------------------------------


def test_align_skips_pure_structural_containers_with_no_body() -> None:
    # PART 1 has no body of its own; only its children carry text. The
    # alignment must not emit phantom events for the container itself.
    v1 = _root(
        _node(
            ("PART_1",),
            "",
            heading="Preliminary",
            children=(_node(("PART_1", "1."), BODY_A, heading="Short title"),),
        ),
    )
    v2 = _root(
        _node(
            ("PART_2",),
            "",
            heading="Preliminary",
            children=(_node(("PART_2", "1."), BODY_A, heading="Short title"),),
        ),
    )
    events = align(v1, v2)
    # The renumbered container itself has no body, so emits no event;
    # only its child re-aligns. Child body identical, path changed
    # (PART_1 -> PART_2) → one MOVED.
    assert [e.change_type for e in events] == ["MOVED"]


def test_align_intermediate_container_with_body_aligns_separately_from_children() -> None:
    # A Section node carrying a preamble body AND having sub-clauses.
    # Pass 2 should align the container and the children independently.
    # The preamble must be long enough relative to the amendment that
    # the MinHash jaccard estimate sits comfortably above the default
    # 0.7 threshold (a short preamble with a short amendment lands at
    # ~0.71 exact, which the 128-permutation estimator can underrun).
    pre = (
        "In this section the following terms have the meanings assigned "
        "to them below namely such meanings shall apply throughout the "
        "entire interpretation of this act for the avoidance of doubt"
    )
    v1 = _root(
        _node(
            ("5.",),
            pre,
            heading="Interpretation",
            children=(_node(("5.", "(1)"), BODY_A, heading="employee definition"),),
        ),
    )
    pre_amended = pre + " unless otherwise stated by the minister by order."
    v2 = _root(
        _node(
            ("5.",),
            pre_amended,
            heading="Interpretation",
            children=(_node(("5.", "(1)"), BODY_A, heading="employee definition"),),
        ),
    )
    events = align(v1, v2)
    # Container preamble changed → 1 MODIFIED. Child unchanged → no event.
    assert [e.change_type for e in events] == ["MODIFIED"]
    assert events[0].before_path == ("5.",)


# ---------------------------------------------------------------------------
# End-to-end against the IE fixture
# ---------------------------------------------------------------------------


def test_align_end_to_end_on_ie_fixture_identity_is_zero_events() -> None:
    text = IE_FIXTURE.read_text(encoding="utf-8")
    tree = parse(text, portal_slug="ie")
    assert align(tree, tree) == []


def test_align_end_to_end_on_ie_fixture_with_one_clause_modified() -> None:
    text = IE_FIXTURE.read_text(encoding="utf-8")
    v1 = parse(text, portal_slug="ie")

    # Walk the parsed tree, find a clause with a meaningful body, and
    # produce a v2 with that one body text amended. Pass through the
    # whole tree rebuilding from the leaves up so the frozen dataclass
    # invariant is preserved.
    target_path: tuple[str, ...] | None = None
    for node in v1.walk():
        if len(node.body_text.split()) >= 30 and node.heading_text is not None:
            target_path = node.path
            break
    assert target_path is not None, "no suitable target clause found in IE fixture"

    def rebuild(node: Clause) -> Clause:
        if node.path == target_path:
            return dataclasses.replace(
                node,
                body_text=node.body_text + " (as amended by the WU2.3 regression suite.)",
                children=tuple(rebuild(c) for c in node.children),
            )
        return dataclasses.replace(
            node,
            children=tuple(rebuild(c) for c in node.children),
        )

    v2 = rebuild(v1)
    events = align(v1, v2)
    modified = [e for e in events if e.change_type == "MODIFIED"]
    # Exactly one clause was edited — the alignment must produce
    # exactly one MODIFIED event, with both paths identical and the
    # texts differing by exactly our appended suffix.
    assert len(modified) == 1
    [m] = modified
    assert m.before_path == target_path == m.after_path
    assert m.after_text.endswith("(as amended by the WU2.3 regression suite.)")
    assert m.before_text != m.after_text
    # No spurious ADDED / REMOVED from the rebuild.
    assert not [e for e in events if e.change_type in {"ADDED", "REMOVED"}]
