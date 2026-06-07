"""Shared fixtures and reporting for the alignment regression suite.

Discovers every ``*.md`` file under ``data/samples/`` and parametrises
the regression suite by fixture slug. A session-scoped reporter
collects per-fixture identity-pass and mutation-precision-recall data;
:func:`pytest_terminal_summary` renders the aggregate score table after
the test run completes so the demo-period CI log carries the alignment
health snapshot.

The table is ASCII-only, demo-presentable, and emitted whether or not
individual tests pass — failures still contribute their (possibly
degraded) scores so a regression's shape is visible without re-running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"


def discover_fixture_slugs() -> list[str]:
    """Return sorted fixture slugs (filename stems) under ``data/samples/``.

    ``README.md`` is excluded; everything else is a fixture. The slug
    is the filename without the ``.md`` suffix and doubles as the
    pytest test id and the RNG seed input for mutation synthesis.
    """
    slugs: list[str] = []
    for entry in sorted(SAMPLES_DIR.glob("*.md")):
        if entry.name == "README.md":
            continue
        slugs.append(entry.stem)
    return slugs


FIXTURE_SLUGS: list[str] = discover_fixture_slugs()


def portal_slug_for(fixture_slug: str) -> str:
    """Map a fixture slug to its portal slug (the ISO prefix)."""
    return fixture_slug.split("-", 1)[0]


@dataclass(slots=True)
class FixtureScore:
    """Per-fixture record assembled by the two regression tests."""

    slug: str
    identity_pass: bool | None = None
    """``None`` until the identity test has run; ``True`` on
    ``align(v1, v1) == []``; ``False`` on any other outcome."""

    identity_events: int = 0
    """Count of events emitted by ``align(v1, v1)``. Zero on pass."""

    mutation_ran: bool = False
    """``True`` when the mutation test ran (fixture was eligible)."""

    skip_reason: str = ""
    """Populated when the mutation test was skipped (e.g. fixture too
    small to synthesise four mutations)."""

    expected_count: int = 0
    actual_count: int = 0
    true_positives: int = 0
    notes: list[str] = field(default_factory=list[str])

    @property
    def precision(self) -> float | None:
        if not self.mutation_ran or self.actual_count == 0:
            return None
        return self.true_positives / self.actual_count

    @property
    def recall(self) -> float | None:
        if not self.mutation_ran or self.expected_count == 0:
            return None
        return self.true_positives / self.expected_count

    @property
    def f1(self) -> float | None:
        p = self.precision
        r = self.recall
        if p is None or r is None or (p == 0 and r == 0):
            return None
        return 2 * p * r / (p + r)


_SCORES: dict[str, FixtureScore] = {slug: FixtureScore(slug=slug) for slug in FIXTURE_SLUGS}


@dataclass(slots=True)
class SyntheticV2Score:
    """Per-fixture record for the synthetic_v2 gold-file suite.

    Distinct from :class:`FixtureScore` because the gold suite has a
    variable expected-event count per fixture (vs. the synthetic
    mutation suite's fixed four) and never reports an identity case.
    """

    slug: str
    expected_count: int = 0
    actual_count: int = 0
    true_positives: int = 0
    notes: list[str] = field(default_factory=list[str])

    @property
    def precision(self) -> float | None:
        if self.actual_count == 0:
            return None
        return self.true_positives / self.actual_count

    @property
    def recall(self) -> float | None:
        if self.expected_count == 0:
            return None
        return self.true_positives / self.expected_count

    @property
    def f1(self) -> float | None:
        p = self.precision
        r = self.recall
        if p is None or r is None or (p == 0 and r == 0):
            return None
        return 2 * p * r / (p + r)


_SYNTHETIC_V2_SCORES: dict[str, SyntheticV2Score] = {}


def record_identity(slug: str, *, events: int) -> None:
    """Record the identity-case outcome for ``slug``."""
    score = _SCORES[slug]
    score.identity_events = events
    score.identity_pass = events == 0


def record_mutation(
    slug: str,
    *,
    expected: int,
    actual: int,
    true_positives: int,
    notes: list[str] | None = None,
) -> None:
    """Record the mutation-case outcome for ``slug``."""
    score = _SCORES[slug]
    score.mutation_ran = True
    score.expected_count = expected
    score.actual_count = actual
    score.true_positives = true_positives
    if notes:
        score.notes.extend(notes)


def record_skip(slug: str, *, reason: str) -> None:
    """Record that the mutation case was skipped (with reason)."""
    score = _SCORES[slug]
    score.mutation_ran = False
    score.skip_reason = reason


def record_synthetic_v2(
    slug: str,
    *,
    expected: int,
    actual: int,
    true_positives: int,
    notes: list[str] | None = None,
) -> None:
    """Record one synthetic_v2 gold-file outcome for ``slug``."""
    score = _SYNTHETIC_V2_SCORES.setdefault(slug, SyntheticV2Score(slug=slug))
    score.expected_count = expected
    score.actual_count = actual
    score.true_positives = true_positives
    if notes:
        score.notes.extend(notes)


def _fmt(v: float | None) -> str:
    return "  -- " if v is None else f"{v:5.2f}"


def _ident_cell(score: FixtureScore) -> str:
    if score.identity_pass is None:
        return "  -- "
    if score.identity_pass:
        return "  ok "
    return f"FAIL ({score.identity_events})"


def _row_notes(score: FixtureScore) -> str:
    if not score.mutation_ran and score.skip_reason:
        return f"skipped: {score.skip_reason}"
    return ", ".join(score.notes)


def _render_report() -> str:
    width = max((len(s) for s in FIXTURE_SLUGS), default=10)
    header = f"{'fixture':<{width}}  ident   P      R      F1     notes"
    sep = "-" * len(header)
    lines = [header, sep]

    p_vals: list[float] = []
    r_vals: list[float] = []
    f_vals: list[float] = []
    ident_pass = 0
    ident_total = 0
    skipped = 0

    for slug in FIXTURE_SLUGS:
        score = _SCORES[slug]
        if score.identity_pass is not None:
            ident_total += 1
            if score.identity_pass:
                ident_pass += 1
        if score.precision is not None:
            p_vals.append(score.precision)
        if score.recall is not None:
            r_vals.append(score.recall)
        if score.f1 is not None:
            f_vals.append(score.f1)
        if not score.mutation_ran:
            skipped += 1
        lines.append(
            f"{slug:<{width}}  "
            f"{_ident_cell(score):<6}  "
            f"{_fmt(score.precision)}  "
            f"{_fmt(score.recall)}  "
            f"{_fmt(score.f1)}  "
            f"{_row_notes(score)}"
        )

    lines.append(sep)
    p_avg = sum(p_vals) / len(p_vals) if p_vals else None
    r_avg = sum(r_vals) / len(r_vals) if r_vals else None
    f_avg = sum(f_vals) / len(f_vals) if f_vals else None
    aggregate_note = ""
    if skipped:
        aggregate_note = f"{skipped} skipped (fixture too small)"
    lines.append(
        f"{'aggregate':<{width}}  "
        f"{ident_pass}/{ident_total:<4}  "
        f"{_fmt(p_avg)}  "
        f"{_fmt(r_avg)}  "
        f"{_fmt(f_avg)}  "
        f"{aggregate_note}"
    )
    return "\n".join(lines)


def _render_synthetic_v2_report() -> str:
    slugs = sorted(_SYNTHETIC_V2_SCORES.keys())
    width = max((len(s) for s in slugs), default=10)
    header = f"{'fixture':<{width}}  N    TP   P      R      F1     notes"
    sep = "-" * len(header)
    lines = [header, sep]

    p_vals: list[float] = []
    r_vals: list[float] = []
    f_vals: list[float] = []

    for slug in slugs:
        score = _SYNTHETIC_V2_SCORES[slug]
        if score.precision is not None:
            p_vals.append(score.precision)
        if score.recall is not None:
            r_vals.append(score.recall)
        if score.f1 is not None:
            f_vals.append(score.f1)
        lines.append(
            f"{slug:<{width}}  "
            f"{score.expected_count:<3}  "
            f"{score.true_positives:<3}  "
            f"{_fmt(score.precision)}  "
            f"{_fmt(score.recall)}  "
            f"{_fmt(score.f1)}  "
            f"{', '.join(score.notes)}"
        )

    lines.append(sep)
    p_avg = sum(p_vals) / len(p_vals) if p_vals else None
    r_avg = sum(r_vals) / len(r_vals) if r_vals else None
    f_avg = sum(f_vals) / len(f_vals) if f_vals else None
    expected_total = sum(s.expected_count for s in _SYNTHETIC_V2_SCORES.values())
    tp_total = sum(s.true_positives for s in _SYNTHETIC_V2_SCORES.values())
    lines.append(
        f"{'aggregate':<{width}}  "
        f"{expected_total:<3}  "
        f"{tp_total:<3}  "
        f"{_fmt(p_avg)}  "
        f"{_fmt(r_avg)}  "
        f"{_fmt(f_avg)}  "
    )
    return "\n".join(lines)


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter,
    exitstatus: int,  # noqa: ARG001 — required by the pytest hook signature
    config: pytest.Config,  # noqa: ARG001 — required by the pytest hook signature
) -> None:
    """Render the alignment-quality tables at the end of the run."""
    has_synth_mutation = any(
        score.identity_pass is not None or score.mutation_ran or score.skip_reason
        for score in _SCORES.values()
    )
    if has_synth_mutation:
        terminalreporter.write_sep("=", "alignment regression quality report")
        terminalreporter.write_line(_render_report())
    if _SYNTHETIC_V2_SCORES:
        terminalreporter.write_sep("=", "alignment quality on synthetic_v2 gold")
        terminalreporter.write_line(_render_synthetic_v2_report())
