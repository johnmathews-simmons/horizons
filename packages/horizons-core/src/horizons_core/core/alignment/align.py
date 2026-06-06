"""Clause-tree alignment and change-event emission (WU2.3).

Composes the parser (WU2.0 / WU2.1) and the similarity primitives
(WU2.2) into the four-pass alignment pipeline documented in
``docs/RFC-2 clause-alignment.md``. The public entry point is
:func:`align`; the residual change events are emitted as
:class:`ChangeEvent` instances.

The alignment unit is **every clause node whose ``body_text`` is
non-empty** — leaves and intermediate containers alike. Pure
structural containers with no body of their own are skipped.

``clause_uid`` is stubbed ``None`` here: stable per-clause
identifiers belong to the ingestion version-transaction (a later
unit). The aligner returns the pairing; UID materialisation is
downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from horizons_core.core.alignment.similarity import (
    jaccard,
    lsh_candidates,
    minhash,
    shingle,
)
from horizons_core.core.alignment.tuning import (
    TuningConfig,
    default_tuning_config,
)

if TYPE_CHECKING:
    from horizons_core.core.alignment.clause import Clause

ChangeType = Literal["ADDED", "REMOVED", "MODIFIED", "MOVED"]

_PASS2_BASE_CONFIDENCE = 0.9
"""Floor confidence for pass-2 (heading + content) pairings — the
heading-equality signal is doing the work even when the underlying
shingle estimate is low."""


class ChangeEvent(BaseModel):
    """A single residual change event emitted by :func:`align`.

    Field-presence depends on ``change_type`` (validated by
    :meth:`_validate_field_presence`): ``ADDED`` carries only
    after-side fields, ``REMOVED`` only before-side, ``MODIFIED``
    both with differing text, ``MOVED`` both with identical text
    and distinct paths.

    ``alignment_confidence`` is a raw float in ``(0, 1]``: 1.0
    indicates a residual (unpaired) clause or a source-ID match;
    ``0.9`` is the floor for heading+content pairings; pass-3
    similarity pairings carry the underlying Jaccard estimate.
    """

    model_config = ConfigDict(frozen=True)

    change_type: ChangeType
    before_clause_uid: str | None = None
    after_clause_uid: str | None = None
    before_path: tuple[str, ...] | None = None
    after_path: tuple[str, ...] | None = None
    before_text: str | None = None
    after_text: str | None = None
    alignment_confidence: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_field_presence(self) -> Self:
        ct = self.change_type
        if ct == "ADDED":
            if (
                self.before_clause_uid is not None
                or self.before_path is not None
                or self.before_text is not None
            ):
                raise ValueError("ADDED events must not carry before-side fields")
            if self.after_path is None or self.after_text is None:
                raise ValueError("ADDED events must carry after_path and after_text")
        elif ct == "REMOVED":
            if (
                self.after_clause_uid is not None
                or self.after_path is not None
                or self.after_text is not None
            ):
                raise ValueError("REMOVED events must not carry after-side fields")
            if self.before_path is None or self.before_text is None:
                raise ValueError("REMOVED events must carry before_path and before_text")
        elif ct == "MOVED":
            if (
                self.before_path is None
                or self.after_path is None
                or self.before_text is None
                or self.after_text is None
            ):
                raise ValueError("MOVED events must carry before/after path and text")
            if self.before_text != self.after_text:
                raise ValueError("MOVED events require identical text on both sides")
            if self.before_path == self.after_path:
                raise ValueError("MOVED events require distinct paths")
        else:  # MODIFIED
            if (
                self.before_path is None
                or self.after_path is None
                or self.before_text is None
                or self.after_text is None
            ):
                raise ValueError("MODIFIED events must carry before/after path and text")
            if self.before_text == self.after_text:
                raise ValueError("MODIFIED events require differing text")
        return self


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A clause considered for alignment, together with its signature.

    ``has_shingles`` is ``False`` when the body is too short to
    produce any shingles at the configured ``k``; the signature is
    still computed (and equals the all-maximum signature MinHash
    produces for the empty set) but is not meaningful for
    similarity comparison.
    """

    clause: Clause
    signature: list[int]
    has_shingles: bool


def align(
    v1: Clause,
    v2: Clause,
    *,
    tuning: TuningConfig | None = None,
) -> list[ChangeEvent]:
    """Align two clause trees and return the residual change events.

    The four passes — source-ID match (stubbed), heading+content
    corroboration, content-similarity with monotonic DP, and
    residual classification — are described in
    ``docs/RFC-2 clause-alignment.md``.

    Identity rule: a paired clause whose path and body text are both
    identical is emitted as no event. This makes idempotent
    re-ingestion of an unchanged version a zero-row operation.

    Passing ``tuning=None`` (the default) is equivalent to passing
    :func:`default_tuning_config()`.
    """
    if tuning is None:
        tuning = default_tuning_config()
    v1_cands = _collect_candidates(v1, tuning)
    v2_cands = _collect_candidates(v2, tuning)

    paired_v1: set[int] = set()
    paired_v2: set[int] = set()
    pairs: list[tuple[int, int, float]] = []

    # Pass 1: source-provided IDs. No substrate we ingest today
    # exposes stable per-clause identifiers; the pass is kept as a
    # hook for future ELI / Lawstronaut integration. Intentionally
    # a no-op.

    # Pass 2: heading-title equality + content corroboration.
    pairs.extend(_pass_heading_match(v1_cands, v2_cands, tuning, paired_v1, paired_v2))

    # Pass 3: content similarity via LSH + monotonic DP.
    pairs.extend(_pass_content_monotonic(v1_cands, v2_cands, tuning, paired_v1, paired_v2))

    # Pass 4: emit events.
    return _emit_events(v1_cands, v2_cands, paired_v1, paired_v2, pairs)


def _collect_candidates(root: Clause, tuning: TuningConfig) -> list[_Candidate]:
    """Walk ``root`` and gather every clause whose body is non-empty."""
    result: list[_Candidate] = []
    for node in root.walk():
        if not node.body_text.strip():
            continue
        sh = shingle(node.body_text, tuning.shingle_k)
        sig = minhash(sh, tuning.signature_size)
        result.append(_Candidate(clause=node, signature=sig, has_shingles=bool(sh)))
    return result


def _pass_heading_match(
    v1: list[_Candidate],
    v2: list[_Candidate],
    tuning: TuningConfig,
    paired_v1: set[int],
    paired_v2: set[int],
) -> list[tuple[int, int, float]]:
    """Anchor-based pairing with content corroboration.

    Two anchors are considered:

    * **Heading anchor** — both clauses carry a non-``None``
      ``heading_text`` and the texts are byte-equal. Boilerplate
      titles like "Definitions" or "Short title" recur across
      documents, so heading equality alone is not enough.
    * **Path anchor** — both clauses carry ``heading_text = None``
      (typical for leaf sub-clauses such as ``(a)``, ``(i)`` that
      the parser doesn't synthesise a heading for) and their
      ``path`` tuples are equal. Path is renumberable in
      principle, but for the *unrenumbered* case it is a strong
      identity signal.

    Mixed pairs (one side heading-bearing, the other not) are not
    considered here — those flow through to pass 3.

    Content corroboration: jaccard estimate above the tuning
    threshold (long bodies) or exact body-text equality (short
    bodies, where shingling produces nothing). Greedy assignment
    by descending confidence keeps the strongest pair when
    multiple candidates collide on the same anchor.
    """
    triples: list[tuple[float, int, int]] = []
    for i, c1 in enumerate(v1):
        h1 = c1.clause.heading_text
        for j, c2 in enumerate(v2):
            h2 = c2.clause.heading_text
            if h1 is not None and h2 is not None:
                if h1 != h2:
                    continue
            elif h1 is None and h2 is None:
                if c1.clause.path != c2.clause.path:
                    continue
            else:
                continue
            conf = _pass2_confidence(c1, c2, tuning)
            if conf is None:
                continue
            triples.append((conf, i, j))

    triples.sort(reverse=True)
    result: list[tuple[int, int, float]] = []
    for conf, i, j in triples:
        if i in paired_v1 or j in paired_v2:
            continue
        paired_v1.add(i)
        paired_v2.add(j)
        result.append((i, j, conf))
    return result


def _pass2_confidence(c1: _Candidate, c2: _Candidate, tuning: TuningConfig) -> float | None:
    """Return pass-2 confidence for ``(c1, c2)`` or ``None`` if unmet."""
    if c1.has_shingles and c2.has_shingles:
        est = jaccard(c1.signature, c2.signature)
        if est < tuning.similarity_threshold:
            return None
        return max(_PASS2_BASE_CONFIDENCE, est)
    # Short-body fallback: heading alone is too weak, so require
    # exact body-text equality. Mixed (one short, one long) never
    # passes here.
    if c1.has_shingles or c2.has_shingles:
        return None
    if c1.clause.body_text != c2.clause.body_text:
        return None
    return _PASS2_BASE_CONFIDENCE


def _pass_content_monotonic(
    v1: list[_Candidate],
    v2: list[_Candidate],
    tuning: TuningConfig,
    paired_v1: set[int],
    paired_v2: set[int],
) -> list[tuple[int, int, float]]:
    """Pair remaining clauses by content similarity, respecting tree order.

    Only candidates with non-empty shingles participate — short-body
    clauses with no meaningful signature would all collide at
    jaccard ``1.0`` against each other. LSH narrows the candidate
    set; a Needleman–Wunsch-style DP picks the maximum-weight
    non-crossing matching.
    """
    unpaired_v1 = [(i, c) for i, c in enumerate(v1) if i not in paired_v1 and c.has_shingles]
    unpaired_v2 = [(j, c) for j, c in enumerate(v2) if j not in paired_v2 and c.has_shingles]
    if not unpaired_v1 or not unpaired_v2:
        return []

    signatures: list[tuple[tuple[str, int], list[int]]] = []
    for i, c in unpaired_v1:
        signatures.append((("v1", i), c.signature))
    for j, c in unpaired_v2:
        signatures.append((("v2", j), c.signature))

    sim: dict[tuple[int, int], float] = {}
    for a, b in lsh_candidates(
        signatures,
        bands=tuning.lsh_bands,
        threshold=tuning.similarity_threshold,
    ):
        side_a, idx_a = a
        side_b, idx_b = b
        if side_a == side_b:
            # Near-duplicate clauses within the same version surface here
            # (e.g. two boilerplate paragraphs in v1). They are noise for
            # cross-version pairing — drop them.
            continue
        # The signatures list places every v1 entry before every v2
        # entry, so lsh_candidates' canonical (lo, hi) ordering
        # guarantees side_a == "v1" for any cross-side pair.
        sim[(idx_a, idx_b)] = jaccard(v1[idx_a].signature, v2[idx_b].signature)

    if not sim:
        return []

    seq_v1 = [i for i, _ in unpaired_v1]
    seq_v2 = [j for j, _ in unpaired_v2]
    m, n = len(seq_v1), len(seq_v2)

    dp = [[0.0] * (n + 1) for _ in range(m + 1)]
    trace: list[list[str]] = [[""] * (n + 1) for _ in range(m + 1)]

    for a in range(1, m + 1):
        for b in range(1, n + 1):
            best = dp[a - 1][b]
            move = "skip_v1"
            if dp[a][b - 1] > best:
                best = dp[a][b - 1]
                move = "skip_v2"
            pair_key = (seq_v1[a - 1], seq_v2[b - 1])
            if pair_key in sim:
                score = dp[a - 1][b - 1] + sim[pair_key]
                # Prefer pairing on ties — a tie means "skip wastes a
                # signal we could have captured".
                if score >= best:
                    best = score
                    move = "pair"
            dp[a][b] = best
            trace[a][b] = move

    result: list[tuple[int, int, float]] = []
    a, b = m, n
    while a > 0 and b > 0:
        mv = trace[a][b]
        if mv == "pair":
            i_v1 = seq_v1[a - 1]
            j_v2 = seq_v2[b - 1]
            result.append((i_v1, j_v2, sim[(i_v1, j_v2)]))
            a -= 1
            b -= 1
        elif mv == "skip_v1":
            a -= 1
        else:
            b -= 1

    for i, j, _ in result:
        paired_v1.add(i)
        paired_v2.add(j)
    return result


def _emit_events(
    v1: list[_Candidate],
    v2: list[_Candidate],
    paired_v1: set[int],
    paired_v2: set[int],
    pairs: list[tuple[int, int, float]],
) -> list[ChangeEvent]:
    """Turn pairings and residuals into the final :class:`ChangeEvent` list.

    Identity rule (Q4): a paired clause whose path and body are
    both unchanged contributes no event. MOVED is reserved for
    text-identical, path-different pairs; any text drift downgrades
    to MODIFIED with ``before_path != after_path`` carrying the
    move signal.
    """
    events: list[ChangeEvent] = []
    for i, j, conf in pairs:
        c1 = v1[i].clause
        c2 = v2[j].clause
        if c1.path == c2.path and c1.body_text == c2.body_text:
            continue
        if c1.body_text == c2.body_text:
            events.append(
                ChangeEvent(
                    change_type="MOVED",
                    before_path=c1.path,
                    after_path=c2.path,
                    before_text=c1.body_text,
                    after_text=c2.body_text,
                    alignment_confidence=conf,
                )
            )
        else:
            events.append(
                ChangeEvent(
                    change_type="MODIFIED",
                    before_path=c1.path,
                    after_path=c2.path,
                    before_text=c1.body_text,
                    after_text=c2.body_text,
                    alignment_confidence=conf,
                )
            )

    for i, c in enumerate(v1):
        if i in paired_v1:
            continue
        events.append(
            ChangeEvent(
                change_type="REMOVED",
                before_path=c.clause.path,
                before_text=c.clause.body_text,
                alignment_confidence=1.0,
            )
        )

    for j, c in enumerate(v2):
        if j in paired_v2:
            continue
        events.append(
            ChangeEvent(
                change_type="ADDED",
                after_path=c.clause.path,
                after_text=c.clause.body_text,
                alignment_confidence=1.0,
            )
        )

    return events
