"""Alignment regression suite over every fixture in ``data/samples/``.

Two cases per fixture:

* :func:`test_identity_emits_no_events` — ``align(v1, v1)`` must return
  ``[]``. Idempotent re-ingestion of an unchanged version is a
  zero-event operation; any non-empty result is a hard failure.
* :func:`test_four_mutations_align_correctly` — a deterministically
  synthesised v2 with one ADDED, one REMOVED, one MODIFIED, and one
  MOVED clause is expected to align to exactly those four events. The
  hard floor is ``true_positives >= TP_FLOOR`` (catches catastrophic
  algorithm regressions); the full precision / recall / F1 numbers are
  reported via the session terminal summary in
  :mod:`tests.alignment.conftest`.

Both tests record their outcome into the shared score store BEFORE
asserting, so failing fixtures still contribute their degraded scores
to the demo-period quality table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from horizons_core.core.alignment.align import align
from horizons_core.core.alignment.parser import parse
from horizons_core.core.alignment.portal_config import load_portal_config

from tests.alignment._mutations import (
    UnsuitableFixture,
    synthesize_mutations,
)
from tests.alignment.conftest import (
    FIXTURE_SLUGS,
    SAMPLES_DIR,
    portal_slug_for,
    record_identity,
    record_mutation,
    record_skip,
)

if TYPE_CHECKING:
    from horizons_core.core.alignment.align import ChangeEvent
    from horizons_core.core.alignment.clause import Clause

    from tests.alignment._mutations import ExpectedEvents

TP_FLOOR = 2
"""Mutation cases must match at least this many of the four expected
events. ADDED and REMOVED depend only on residual detection (no content
pairing), so an algorithm that's even minimally working surfaces both —
``TP_FLOOR = 2`` therefore catches catastrophic algorithm regressions
without blocking boilerplate-rich corpora where precision degrades from
spurious MOVED/MODIFIED pairings. WU2.4's deliverable is the score
report (see ``docs/2. clause-alignment.md`` — *Calibration*), not a
hard gate."""


def _parse_fixture(slug: str) -> Clause:
    text = (SAMPLES_DIR / f"{slug}.md").read_text(encoding="utf-8")
    portal = portal_slug_for(slug)
    try:
        cfg = load_portal_config(portal)
    except KeyError:
        return parse(text)
    return parse(text, config=cfg)


@pytest.mark.parametrize("slug", FIXTURE_SLUGS, ids=FIXTURE_SLUGS)
def test_identity_emits_no_events(slug: str) -> None:
    """``align(v1, v1) == []`` for every fixture.

    The identity case is the load-bearing correctness check: any event
    here means re-ingesting an unchanged version would emit spurious
    diffs to customers.
    """
    tree = _parse_fixture(slug)
    events = align(tree, tree)
    record_identity(slug, events=len(events))
    assert events == [], (
        f"{slug}: expected zero events on identity alignment, "
        f"got {len(events)}: "
        f"{[(e.change_type, e.before_path, e.after_path) for e in events[:5]]}"
    )


@pytest.mark.parametrize("slug", FIXTURE_SLUGS, ids=FIXTURE_SLUGS)
def test_four_mutations_align_correctly(slug: str) -> None:
    """Synthesised 4-mutation v2 must align to exactly four events.

    Mutations are deterministic per fixture slug (see
    :mod:`tests.alignment._mutations`); the test computes precision and
    recall against the expected event set and records them into the
    session score store before asserting the :data:`TP_FLOOR` floor.
    """
    tree = _parse_fixture(slug)
    try:
        v2, expected = synthesize_mutations(tree, slug=slug)
    except UnsuitableFixture as exc:
        record_skip(slug, reason=str(exc))
        pytest.skip(str(exc))

    actual = align(tree, v2)
    tp, notes = _score(actual, expected)
    record_mutation(
        slug,
        expected=4,
        actual=len(actual),
        true_positives=tp,
        notes=notes,
    )

    precision = tp / len(actual) if actual else 0.0
    recall = tp / 4
    assert tp >= TP_FLOOR, (
        f"{slug}: only {tp}/4 expected events matched (floor {TP_FLOOR}); "
        f"P={precision:.2f}, R={recall:.2f}; "
        f"expected 4 events, got {len(actual)}; "
        f"notes: {notes}"
    )


def _score(actual: list[ChangeEvent], expected: ExpectedEvents) -> tuple[int, list[str]]:
    """Count matched expected events and describe any discrepancy."""
    tp = 0
    notes: list[str] = []

    added = [e for e in actual if e.change_type == "ADDED" and e.after_path == expected.added_path]
    if added:
        tp += 1
    else:
        notes.append("missed ADDED")

    removed = [
        e for e in actual if e.change_type == "REMOVED" and e.before_path == expected.removed_path
    ]
    if removed:
        tp += 1
    else:
        notes.append("missed REMOVED")

    modified = [
        e
        for e in actual
        if e.change_type == "MODIFIED"
        and e.before_path == expected.modified_path
        and e.after_path == expected.modified_path
    ]
    if modified:
        tp += 1
    else:
        notes.append("missed MODIFIED")

    moved = [
        e
        for e in actual
        if e.change_type == "MOVED"
        and e.before_path == expected.moved_before_path
        and e.after_path == expected.moved_after_path
    ]
    if moved:
        tp += 1
    else:
        notes.append("missed MOVED")

    extras = len(actual) - tp
    if extras > 0:
        notes.append(f"{extras} extra event(s)")

    if tp == 4 and not extras:
        notes.clear()
    return tp, notes
