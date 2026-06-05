"""Deterministic mutation synthesis for the alignment regression suite.

For each fixture, :func:`synthesize_mutations` produces a v2 clause tree
carrying exactly one ADDED, one REMOVED, one MODIFIED, and one MOVED
change relative to v1. The mutation target set is selected by a stable
per-slug RNG (``zlib.crc32(slug)``) so failures reproduce from the slug
alone.

Constraints driving the target picks:

* The MODIFIED target needs body long enough that a small appended
  amendment keeps the post-mutation pair comfortably above the MinHash
  jaccard threshold — the 128-permutation estimator drifts ~0.03 from
  the exact value at threshold edges (see the WU2.3 journal). With the
  fixed amendment of ~7 tokens, requiring ``>= 200`` body chars
  (~40 tokens) puts the exact jaccard at ``~0.85`` and the estimator
  comfortably above the default 0.7 threshold.
* The MOVED target is a leaf clause whose body is identical across v1
  and v2 (jaccard 1.0) but whose ``path`` differs. The destination is
  the end of root.children so pass-3's monotonic-order DP has no
  crossings to negotiate.
* The ADDED clause is appended at root level with a distinctive,
  shingle-rich body chosen to never LSH-match anything in v1.
* The REMOVED target is dropped from its parent; its residual REMOVED
  emission depends on no other v2 clause being a close LSH match.
* All four targets are picked from the leaf set (no children), so no
  target is an ancestor of another.

When a fixture is too small to satisfy the constraints (very short
documents like the 721-byte HR fixture), :class:`UnsuitableFixture`
is raised; callers skip the mutation case for that fixture and record
the reason in the score report.
"""

from __future__ import annotations

import dataclasses
import zlib
from random import Random

from horizons_core.core.alignment.clause import Clause

MODIFIED_BODY_MIN_CHARS = 200
"""Floor on body length for the MODIFIED target. Keeps the
post-amendment exact jaccard above ~0.85, well clear of the default
similarity threshold (0.7) plus the MinHash estimator variance band."""

MOVED_BODY_MIN_CHARS = 60
"""Floor on body length for the MOVED target. The body is unchanged
across v1 and v2 (jaccard 1.0) so the only reason to require length is
to ensure pass-3 has shingles to work with when the leaf has no heading
anchor in pass-2."""

REMOVED_BODY_MIN_CHARS = 60
"""Floor on body length for the REMOVED target. Long enough to shingle
so its uniqueness can be assessed; short enough to keep most fixtures
eligible."""

MOVED_SEGMENT = "__wu24_moved__"
ADDED_SEGMENT = "__wu24_added__"

AMENDMENT_SUFFIX = " (as amended by the WU2.4 alignment regression suite.)"

ADDED_BODY = (
    "This clause was inserted by the WU2.4 alignment regression suite "
    "to exercise the ADDED change-type pathway against this fixture. "
    "It carries distinctive synthetic prose that should not pair with "
    "any pre-existing clause via heading equality or content similarity."
)


class UnsuitableFixture(Exception):
    """Raised when a fixture has too little content to mutate four ways."""


@dataclasses.dataclass(frozen=True, slots=True)
class ExpectedEvents:
    """The four expected change events for a synthesised v2 tree.

    Carries the paths the aligner is expected to emit on each side of
    each event. Use :meth:`describe` for a one-line note when reporting
    discrepancies.
    """

    added_path: tuple[str, ...]
    removed_path: tuple[str, ...]
    modified_path: tuple[str, ...]
    moved_before_path: tuple[str, ...]
    moved_after_path: tuple[str, ...]


def stable_seed(slug: str) -> int:
    """Deterministic 32-bit seed derived from the fixture slug."""
    return zlib.crc32(slug.encode("utf-8"))


def synthesize_mutations(
    tree: Clause,
    *,
    slug: str,
) -> tuple[Clause, ExpectedEvents]:
    """Return ``(v2, expected)`` for the given v1 ``tree``.

    Picks four distinct leaf clauses (modified, removed, moved) plus
    synthesises one new ADDED clause at root level. The RNG is seeded
    from ``slug`` for reproducibility.

    Raises :class:`UnsuitableFixture` if not enough eligible leaves
    exist; callers should skip the mutation case for that fixture.
    """
    leaves = [c for c in tree.walk() if not c.children and c.body_text.strip()]
    if len(leaves) < 3:
        raise UnsuitableFixture(f"only {len(leaves)} non-empty leaves in {slug}")

    modified_pool = [c for c in leaves if len(c.body_text) >= MODIFIED_BODY_MIN_CHARS]
    moved_pool = [c for c in leaves if len(c.body_text) >= MOVED_BODY_MIN_CHARS]
    removed_pool = [c for c in leaves if len(c.body_text) >= REMOVED_BODY_MIN_CHARS]
    if not modified_pool or not moved_pool or not removed_pool:
        raise UnsuitableFixture(
            f"{slug} lacks leaves long enough for all of MODIFIED/MOVED/REMOVED",
        )

    rng = Random(stable_seed(slug))

    modified_target = rng.choice(modified_pool)
    moved_pool_excluding = [c for c in moved_pool if c.path != modified_target.path]
    if not moved_pool_excluding:
        raise UnsuitableFixture(f"{slug} has only one leaf eligible for mutation")
    moved_target = rng.choice(moved_pool_excluding)
    removed_pool_excluding = [
        c for c in removed_pool if c.path != modified_target.path and c.path != moved_target.path
    ]
    if not removed_pool_excluding:
        raise UnsuitableFixture(f"{slug} has only two leaves eligible for mutation")
    removed_target = rng.choice(removed_pool_excluding)

    moved_new_path = (MOVED_SEGMENT,)
    added_path = (ADDED_SEGMENT,)
    moved_leaf_v2 = dataclasses.replace(moved_target, path=moved_new_path)
    added_clause = Clause(
        path=added_path,
        heading_text=None,
        body_text=ADDED_BODY,
        numbering_label=None,
        children=(),
    )

    drop_paths = {removed_target.path, moved_target.path}

    def rebuild(node: Clause) -> Clause:
        new_children: list[Clause] = []
        for child in node.children:
            if child.path in drop_paths:
                continue
            new_children.append(rebuild(child))
        # Root carries the synthesised ADDED + the relocated MOVED leaf
        # at the end of its children — the late position keeps pass-3's
        # monotonic DP from having to negotiate any crossings.
        if node.path == ():
            new_children.append(moved_leaf_v2)
            new_children.append(added_clause)
        if node.path == modified_target.path:
            return dataclasses.replace(
                node,
                body_text=node.body_text + AMENDMENT_SUFFIX,
                children=tuple(new_children),
            )
        return dataclasses.replace(node, children=tuple(new_children))

    v2 = rebuild(tree)
    expected = ExpectedEvents(
        added_path=added_path,
        removed_path=removed_target.path,
        modified_path=modified_target.path,
        moved_before_path=moved_target.path,
        moved_after_path=moved_new_path,
    )
    return v2, expected
