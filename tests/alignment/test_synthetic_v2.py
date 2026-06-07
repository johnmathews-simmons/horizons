"""Alignment quality on the hand-authored ``data/samples/synthetic_v2/`` pairs.

Complements :mod:`tests.alignment.test_fixtures` (synthetic-mutation
regression on every v1 fixture). Where that suite measures the aligner
on random leaf-level edits across 27 fixtures, this one measures it
against eight hand-authored v2 documents that mirror real legal
amendments: paragraph removals, content modifications, structural
additions, and section renumberings. The gold ``(change_type,
before_path, after_path)`` set lives at
``data/samples/synthetic_v2/expected_events.yaml``.

Scoring is the same shape as :func:`tests.alignment.test_fixtures._score`:

* **TP** — aligner emitted an event whose ``change_type`` and (where
  applicable) ``before_path`` and ``after_path`` match a gold entry.
* **FP** — any emitted event not matched against the gold. Cascading
  paragraph renumbers triggered by an insertion / removal show up here
  and rightly suppress precision: a legal-corpus user reads one edit
  as one alert, not as four reshuffles around it.
* **FN** — any gold entry the aligner did not emit.

The hard floor (TP_FLOOR) catches catastrophic regressions; full
precision / recall / F1 are reported via the session terminal summary
in :mod:`tests.alignment.conftest`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
import yaml
from horizons_core.core.alignment.align import align
from horizons_core.core.alignment.parser import parse
from horizons_core.core.alignment.portal_config import load_portal_config

from tests.alignment.conftest import (
    SAMPLES_DIR,
    aggregate_synthetic_v2_f1,
    portal_slug_for,
    record_synthetic_v2,
    synthetic_v2_score_count,
)

if TYPE_CHECKING:
    from pathlib import Path

    from horizons_core.core.alignment.align import ChangeEvent

SYNTHETIC_V2_DIR = SAMPLES_DIR / "synthetic_v2"
GOLD_PATH = SYNTHETIC_V2_DIR / "expected_events.yaml"

TP_FLOOR_RATIO = 0.5
"""Fixtures must match at least half of their gold entries.

The synthetic_v2 gold encodes real-amendment patterns the synthetic
mutation suite cannot — cross-paragraph renumbering, edits that drift
text enough to fall out of pass-2 heading equality. A 50% floor is
deliberately permissive at the per-fixture level so a single weak
fixture does not gate; the demo-period quality table is the calibration
surface, not the floor."""

AGGREGATE_F1_FLOOR = 0.65
"""Macro-F1 across the gold suite must not slide below this.

Current baseline (2026-06-07) is ~0.78; the 0.13-point cushion absorbs
per-fixture noise (small-text-edit miss patterns sometimes flip
emission categories) but a real algorithmic regression would push
several fixtures' precision down at once and clear the floor. Raise
this whenever a tuning change demonstrably lifts the baseline by more
than the cushion."""


@dataclass(frozen=True, slots=True)
class _GoldEvent:
    """One expected change event as encoded in the gold YAML."""

    change_type: str
    before_path: tuple[str, ...] | None
    after_path: tuple[str, ...] | None


def _load_gold() -> dict[str, list[_GoldEvent]]:
    raw = yaml.safe_load(GOLD_PATH.read_text(encoding="utf-8"))
    out: dict[str, list[_GoldEvent]] = {}
    for slug, body in raw["fixtures"].items():
        events: list[_GoldEvent] = []
        for entry in body["events"]:
            before = entry.get("before_path")
            after = entry.get("after_path")
            events.append(
                _GoldEvent(
                    change_type=entry["change_type"],
                    before_path=tuple(before) if before is not None else None,
                    after_path=tuple(after) if after is not None else None,
                )
            )
        out[slug] = events
    return out


_GOLD: dict[str, list[_GoldEvent]] = _load_gold()
SYNTHETIC_V2_SLUGS: list[str] = sorted(_GOLD.keys())


def _parse_fixture(path: Path) -> object:
    portal = portal_slug_for(path.stem)
    text = path.read_text(encoding="utf-8")
    try:
        cfg = load_portal_config(portal)
    except KeyError:
        return parse(text)
    return parse(text, config=cfg)


def _event_matches_gold(event: ChangeEvent, gold: _GoldEvent) -> bool:
    if event.change_type != gold.change_type:
        return False
    if gold.before_path is not None and event.before_path != gold.before_path:
        return False
    return not (gold.after_path is not None and event.after_path != gold.after_path)


def _score(actual: list[ChangeEvent], expected: list[_GoldEvent]) -> tuple[int, list[str]]:
    """Count matched gold entries and describe discrepancies.

    Each gold entry can claim at most one actual event, and each actual
    event can satisfy at most one gold entry — duplicate emissions
    against the same gold entry count as one TP plus one FP.
    """
    consumed: set[int] = set()
    tp = 0
    notes: list[str] = []

    for g in expected:
        match_idx: int | None = None
        for i, e in enumerate(actual):
            if i in consumed:
                continue
            if _event_matches_gold(e, g):
                match_idx = i
                break
        if match_idx is None:
            label = g.change_type
            anchor = g.after_path if g.before_path is None else g.before_path
            notes.append(f"missed {label} {list(anchor) if anchor else ''}".strip())
        else:
            consumed.add(match_idx)
            tp += 1

    extras = len(actual) - tp
    if extras > 0:
        notes.append(f"{extras} extra event(s)")
    return tp, notes


@pytest.mark.parametrize("slug", SYNTHETIC_V2_SLUGS, ids=SYNTHETIC_V2_SLUGS)
def test_synthetic_v2_alignment(slug: str) -> None:
    """``align(v1, v2)`` must recover the gold edits for each pair.

    Per-fixture precision/recall/F1 land in the terminal summary table
    rendered by :mod:`tests.alignment.conftest`; this assertion is a
    catastrophe floor only.
    """
    v1 = _parse_fixture(SAMPLES_DIR / f"{slug}-v1.md")
    v2 = _parse_fixture(SYNTHETIC_V2_DIR / f"{slug}-v2.md")
    actual = align(v1, v2)  # type: ignore[arg-type]
    expected = _GOLD[slug]
    tp, notes = _score(actual, expected)
    record_synthetic_v2(
        slug,
        expected=len(expected),
        actual=len(actual),
        true_positives=tp,
        notes=notes,
    )
    precision = tp / len(actual) if actual else 0.0
    recall = tp / len(expected)
    floor = max(1, int(len(expected) * TP_FLOOR_RATIO))
    assert tp >= floor, (
        f"{slug}: only {tp}/{len(expected)} expected events matched "
        f"(floor {floor}); P={precision:.2f}, R={recall:.2f}; "
        f"expected {len(expected)} events, got {len(actual)}; "
        f"notes: {notes}"
    )


def test_zz_aggregate_f1_above_floor() -> None:
    """Aggregate macro-F1 across the gold suite must clear the floor.

    Named ``test_zz_…`` so it sorts last alphabetically and runs after
    the parametrized per-fixture tests have populated the score store.
    If the suite was run with ``-k`` filtering and only a subset of
    fixtures populated their scores, the assertion is skipped — the
    aggregate is only meaningful with the full set.
    """
    if synthetic_v2_score_count() < len(SYNTHETIC_V2_SLUGS):
        pytest.skip(
            f"only {synthetic_v2_score_count()}/{len(SYNTHETIC_V2_SLUGS)} "
            "fixtures scored — aggregate F1 is only meaningful "
            "with the full suite"
        )
    f1 = aggregate_synthetic_v2_f1()
    assert f1 is not None and f1 >= AGGREGATE_F1_FLOOR, (
        f"aggregate macro-F1 = {f1:.3f} below floor "
        f"{AGGREGATE_F1_FLOOR}; investigate per-fixture rows in the "
        "terminal-summary table"
    )
