"""Similarity primitives for clause alignment.

Pure functions that the alignment pipeline (WU2.3) composes. The four
public callables form a layered stack: a clause body is shingled into
overlapping word ``k``-grams; the shingle set is compressed into a
fixed-size MinHash signature; :func:`jaccard` estimates the similarity
between two such signatures in ``O(signature_size)`` time;
:func:`lsh_candidates` narrows the candidate-pair search to sub-linear
in corpus size via ``datasketch.MinHashLSH``.

The MinHash seed (:data:`MINHASH_SEED`) is part of the wire format —
change it and existing signatures stop matching. See
``docs/RFC-2 clause-alignment.md`` for the algorithmic rationale.

These functions take primitive ints / floats rather than a
``TuningConfig``; the alignment pipeline (and, later, the admin UI) is
the seam that reads the config and forwards the numbers in.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Iterator, Sequence
from typing import Any, cast

import numpy as np
from datasketch import MinHash, MinHashLSH

MINHASH_SEED = 1
"""Fixed seed for :class:`datasketch.MinHash`. Part of the wire format —
re-hashing the entire corpus is the only safe way to change this."""


def shingle(text: str, k: int) -> set[str]:
    """Return the set of word ``k``-shingles for ``text``.

    Tokenisation is :meth:`str.split` (any whitespace run separates
    tokens). Each shingle is ``k`` consecutive tokens joined by a single
    space. Returns the empty set when the token count is below ``k``,
    including for empty / whitespace-only input.
    """
    if k < 1:
        raise ValueError(f"shingle k must be >= 1, got {k}")
    tokens = text.split()
    if len(tokens) < k:
        return set()
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def minhash(shingles: Iterable[str], signature_size: int) -> list[int]:
    """Return a deterministic MinHash signature of length ``signature_size``.

    Same inputs (under :data:`MINHASH_SEED`) produce identical signatures
    across calls and across processes. An empty iterable yields the
    all-maximum signature MinHash produces for the empty set.
    """
    if signature_size < 1:
        raise ValueError(f"signature_size must be >= 1, got {signature_size}")
    m = cast("Any", MinHash(num_perm=signature_size, seed=MINHASH_SEED))
    for s in shingles:
        m.update(s.encode("utf-8"))
    hashvalues: np.ndarray[tuple[int, ...], np.dtype[np.uint64]] = m.hashvalues
    return [int(x) for x in hashvalues]


def jaccard(a: list[int], b: list[int]) -> float:
    """Estimate the Jaccard similarity of two MinHash signatures.

    The estimator is the fraction of positions where the two signatures
    agree. Both signatures must have the same length. Two empty
    signatures return ``1.0`` by convention (the empty set is identical
    to itself).
    """
    if len(a) != len(b):
        raise ValueError(
            f"signature lengths differ ({len(a)} vs {len(b)}); cannot compare",
        )
    if not a:
        return 1.0
    matches = sum(1 for x, y in zip(a, b, strict=True) if x == y)
    return matches / len(a)


def lsh_candidates[K: Hashable](
    signatures: Sequence[tuple[K, list[int]]],
    *,
    bands: int,
    threshold: float,
) -> Iterator[tuple[K, K]]:
    """Yield candidate pairs of similar signatures.

    Each entry in ``signatures`` is ``(id, sig)`` where ``id`` is any
    hashable caller-chosen identifier (e.g. a ``clause_path`` tuple).
    Each unordered pair is yielded at most once.

    Candidate enumeration uses :class:`datasketch.MinHashLSH` partitioned
    into ``bands`` bands; pairs sharing at least one band are candidates.
    Each candidate is then post-filtered against the exact MinHash
    Jaccard estimate so ``threshold`` is honoured tightly rather than
    approximately (vanilla LSH would let chance-band-collisions through).

    Preconditions: ``bands >= 1``; all signatures share the same length;
    that length is divisible by ``bands``; ``0.0 <= threshold <= 1.0``.
    """
    if bands < 1:
        raise ValueError(f"bands must be >= 1, got {bands}")
    if not (0.0 <= threshold <= 1.0):
        raise ValueError(f"threshold must be in [0, 1], got {threshold}")
    if not signatures:
        return

    signature_size = len(signatures[0][1])
    if any(len(sig) != signature_size for _, sig in signatures):
        raise ValueError("all signatures must have the same length")
    if signature_size % bands != 0:
        raise ValueError(
            f"signature_size {signature_size} is not divisible by bands {bands}",
        )
    rows = signature_size // bands

    lsh = cast("Any", MinHashLSH(num_perm=signature_size, params=(bands, rows)))
    arrays: list[np.ndarray[tuple[int, ...], np.dtype[np.uint64]]] = []
    for idx, (_, sig) in enumerate(signatures):
        arr = np.asarray(sig, dtype=np.uint64)
        arrays.append(arr)
        m = cast("Any", MinHash(num_perm=signature_size, seed=MINHASH_SEED))
        m.hashvalues = arr
        lsh.insert(str(idx), m)

    seen: set[tuple[int, int]] = set()
    for i in range(len(signatures)):
        probe = cast("Any", MinHash(num_perm=signature_size, seed=MINHASH_SEED))
        probe.hashvalues = arrays[i]
        raw_matches: list[str] = list(lsh.query(probe))
        for raw in raw_matches:
            j = int(raw)
            if j == i:
                continue
            lo, hi = (i, j) if i < j else (j, i)
            if (lo, hi) in seen:
                continue
            seen.add((lo, hi))
            est = jaccard(
                [int(x) for x in arrays[lo]],
                [int(x) for x in arrays[hi]],
            )
            if est >= threshold:
                yield (signatures[lo][0], signatures[hi][0])
