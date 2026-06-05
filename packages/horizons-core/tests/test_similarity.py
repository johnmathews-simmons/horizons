"""Tests for the similarity primitives in :mod:`horizons_core.core.alignment.similarity`.

Coverage groups:

* ``shingle`` — set semantics, k=1 and k=len edges, sub-k input, validation.
* ``minhash`` — determinism across calls and processes, length contract,
  distinct-input distinctness (statistical), empty input, validation.
* ``jaccard`` — identical sigs land at 1.0; disjoint sigs land near 0.0
  (statistical, loose threshold); empty-pair convention; length-mismatch
  validation.
* ``lsh_candidates`` — identical sigs report, dissimilar sigs do not,
  each pair yielded once, deterministic order, validation paths.
* End-to-end against a real IE clause body from ``data/samples/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from horizons_core.core.alignment import parse
from horizons_core.core.alignment.similarity import (
    MINHASH_SEED,
    jaccard,
    lsh_candidates,
    minhash,
    shingle,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
IE_FIXTURE = REPO_ROOT / "data" / "samples" / "ie-27732019-v1.md"


# ---------------------------------------------------------------------------
# shingle
# ---------------------------------------------------------------------------


def test_shingle_word_3grams_simple() -> None:
    text = "the quick brown fox jumps"
    expected = {"the quick brown", "quick brown fox", "brown fox jumps"}
    assert shingle(text, k=3) == expected


def test_shingle_is_a_set_so_repeats_collapse() -> None:
    text = "a b a b a b"
    # With k=2 the 2-grams are "a b" and "b a", each appearing several times.
    assert shingle(text, k=2) == {"a b", "b a"}


def test_shingle_is_deterministic() -> None:
    text = "the quick brown fox jumps over the lazy dog"
    assert shingle(text, k=5) == shingle(text, k=5)


def test_shingle_k_equal_to_token_count_yields_one_shingle() -> None:
    text = "alpha beta gamma"
    assert shingle(text, k=3) == {"alpha beta gamma"}


def test_shingle_fewer_tokens_than_k_returns_empty_set() -> None:
    assert shingle("alpha beta", k=5) == set()


def test_shingle_empty_text_returns_empty_set() -> None:
    assert shingle("", k=3) == set()
    assert shingle("   \n\t", k=3) == set()


def test_shingle_k_must_be_at_least_one() -> None:
    with pytest.raises(ValueError, match="must be >= 1"):
        shingle("the quick", k=0)
    with pytest.raises(ValueError, match="must be >= 1"):
        shingle("the quick", k=-1)


def test_shingle_k_one_yields_unique_tokens() -> None:
    text = "alpha beta alpha gamma"
    assert shingle(text, k=1) == {"alpha", "beta", "gamma"}


def test_shingle_collapses_whitespace_via_split() -> None:
    # ``str.split`` with no arg collapses any whitespace run — so newlines
    # and tabs are treated identically to spaces.
    assert shingle("alpha  beta\tgamma\n\ndelta", k=2) == {
        "alpha beta",
        "beta gamma",
        "gamma delta",
    }


# ---------------------------------------------------------------------------
# minhash
# ---------------------------------------------------------------------------


def test_minhash_signature_length_matches_signature_size() -> None:
    sig = minhash(["alpha", "beta", "gamma"], signature_size=64)
    assert len(sig) == 64
    sig256 = minhash(["alpha", "beta", "gamma"], signature_size=256)
    assert len(sig256) == 256


def test_minhash_is_stable_across_calls() -> None:
    shingles = {"alpha", "beta", "gamma", "delta"}
    a = minhash(shingles, signature_size=128)
    b = minhash(shingles, signature_size=128)
    assert a == b


def test_minhash_returns_plain_python_ints() -> None:
    sig = minhash({"alpha", "beta"}, signature_size=32)
    assert all(type(x) is int for x in sig)


def test_minhash_empty_input_returns_signature_of_expected_length() -> None:
    sig = minhash([], signature_size=32)
    assert len(sig) == 32


def test_minhash_distinct_inputs_produce_distinct_signatures() -> None:
    a = minhash({"alpha", "beta", "gamma"}, signature_size=128)
    b = minhash({"theta", "iota", "kappa"}, signature_size=128)
    # Very loose check — totally-disjoint shingle sets should not collide
    # on every permutation.
    assert a != b


def test_minhash_signature_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="must be >= 1"):
        minhash(["alpha"], signature_size=0)
    with pytest.raises(ValueError, match="must be >= 1"):
        minhash(["alpha"], signature_size=-4)


def test_minhash_seed_constant_is_exposed() -> None:
    # Wire-format constant — guard against silent renames.
    assert MINHASH_SEED == 1


# ---------------------------------------------------------------------------
# jaccard
# ---------------------------------------------------------------------------


def test_jaccard_identical_signatures_score_one() -> None:
    sig = minhash({"alpha", "beta", "gamma"}, signature_size=128)
    assert jaccard(sig, sig) == 1.0


def test_jaccard_disjoint_signatures_score_near_zero() -> None:
    # MinHash Jaccard on totally-disjoint shingle sets should estimate
    # near zero. With signature_size=256 the standard deviation of the
    # estimator is well under 0.1, so a generous 0.1 bound is safe.
    a = minhash({f"alpha-{i}" for i in range(50)}, signature_size=256)
    b = minhash({f"omega-{i}" for i in range(50)}, signature_size=256)
    assert jaccard(a, b) < 0.1


def test_jaccard_two_empty_signatures_score_one() -> None:
    assert jaccard([], []) == 1.0


def test_jaccard_length_mismatch_raises() -> None:
    a = minhash({"alpha"}, signature_size=32)
    b = minhash({"alpha"}, signature_size=64)
    with pytest.raises(ValueError, match="signature lengths differ"):
        jaccard(a, b)


def test_jaccard_partial_overlap_lands_in_expected_band() -> None:
    # Two shingle sets with ~50% overlap should land roughly mid-range.
    shared = {f"shared-{i}" for i in range(50)}
    sig_a = minhash(shared | {f"a-only-{i}" for i in range(50)}, signature_size=256)
    sig_b = minhash(shared | {f"b-only-{i}" for i in range(50)}, signature_size=256)
    score = jaccard(sig_a, sig_b)
    # True Jaccard = 50 / 150 = 0.333... allow a wide statistical band.
    assert 0.2 < score < 0.5


# ---------------------------------------------------------------------------
# lsh_candidates
# ---------------------------------------------------------------------------


def _sig(shingles: set[str]) -> list[int]:
    return minhash(shingles, signature_size=128)


def test_lsh_candidates_yields_identical_pairs() -> None:
    shared = {f"shared-{i}" for i in range(50)}
    pairs = [("a", _sig(shared)), ("b", _sig(shared))]
    candidates = list(lsh_candidates(pairs, bands=16, threshold=0.7))
    assert candidates == [("a", "b")]


def test_lsh_candidates_omits_dissimilar_pairs() -> None:
    pairs = [
        ("a", _sig({f"alpha-{i}" for i in range(50)})),
        ("b", _sig({f"omega-{i}" for i in range(50)})),
    ]
    assert list(lsh_candidates(pairs, bands=16, threshold=0.7)) == []


def test_lsh_candidates_yields_each_pair_at_most_once() -> None:
    shared = {f"shared-{i}" for i in range(50)}
    pairs = [(f"id-{i}", _sig(shared)) for i in range(5)]
    candidates = list(lsh_candidates(pairs, bands=16, threshold=0.7))
    # 5 choose 2 = 10 unique pairs; deduplication must not double-count.
    assert len(candidates) == 10
    assert len(set(candidates)) == 10


def test_lsh_candidates_is_deterministic_across_runs() -> None:
    shared = {f"shared-{j}" for j in range(20)}
    pairs = [(f"id-{i}", _sig({f"a-{j}-{i}" for j in range(40)} | shared)) for i in range(6)]
    one = list(lsh_candidates(pairs, bands=16, threshold=0.4))
    two = list(lsh_candidates(pairs, bands=16, threshold=0.4))
    assert one == two


def test_lsh_candidates_empty_input_yields_nothing() -> None:
    assert list(lsh_candidates([], bands=16, threshold=0.7)) == []


def test_lsh_candidates_single_signature_yields_no_pairs() -> None:
    pairs = [("only", _sig({"alpha", "beta"}))]
    assert list(lsh_candidates(pairs, bands=16, threshold=0.7)) == []


def test_lsh_candidates_rejects_bands_below_one() -> None:
    pairs = [("a", _sig({"alpha"})), ("b", _sig({"alpha"}))]
    with pytest.raises(ValueError, match="bands must be >= 1"):
        list(lsh_candidates(pairs, bands=0, threshold=0.7))


def test_lsh_candidates_rejects_out_of_range_threshold() -> None:
    pairs = [("a", _sig({"alpha"})), ("b", _sig({"alpha"}))]
    with pytest.raises(ValueError, match=r"threshold must be in \[0, 1\]"):
        list(lsh_candidates(pairs, bands=16, threshold=1.5))
    with pytest.raises(ValueError, match=r"threshold must be in \[0, 1\]"):
        list(lsh_candidates(pairs, bands=16, threshold=-0.1))


def test_lsh_candidates_rejects_mismatched_signature_lengths() -> None:
    pairs = [
        ("a", minhash({"alpha"}, signature_size=64)),
        ("b", minhash({"alpha"}, signature_size=128)),
    ]
    with pytest.raises(ValueError, match="same length"):
        list(lsh_candidates(pairs, bands=16, threshold=0.7))


def test_lsh_candidates_rejects_indivisible_bands() -> None:
    pairs = [
        ("a", minhash({"alpha"}, signature_size=128)),
        ("b", minhash({"alpha"}, signature_size=128)),
    ]
    with pytest.raises(ValueError, match="not divisible by"):
        list(lsh_candidates(pairs, bands=17, threshold=0.7))


def test_lsh_candidates_post_filter_drops_band_collisions_below_threshold() -> None:
    # Construct two signatures that share an entire LSH band (8 of 128
    # positions, the first band with bands=16) but disagree everywhere
    # else. MinHashLSH reports them as candidates; the exact-Jaccard
    # post-filter on threshold=0.5 must drop them. This exercises the
    # band-collision-but-real-estimate-too-low path in lsh_candidates.
    sig_a = list(range(128))
    sig_b = list(range(8)) + [10_000 + i for i in range(120)]
    # By construction: 8 positions match (the shared first band), 120
    # disagree. The MinHash Jaccard estimate is 8/128 = 0.0625.
    assert jaccard(sig_a, sig_b) == pytest.approx(0.0625)
    pairs = [("a", sig_a), ("b", sig_b)]
    assert list(lsh_candidates(pairs, bands=16, threshold=0.5)) == []


def test_lsh_candidates_supports_tuple_keys() -> None:
    # WU2.3 will pair clauses by ``clause_path`` tuples — make sure the
    # generic id type round-trips through the LSH layer unmolested.
    shared = {f"shared-{i}" for i in range(40)}
    pairs: list[tuple[tuple[str, ...], list[int]]] = [
        (("PART 1", "1."), _sig(shared)),
        (("PART 2", "5."), _sig(shared)),
    ]
    candidates = list(lsh_candidates(pairs, bands=16, threshold=0.7))
    assert candidates == [(("PART 1", "1."), ("PART 2", "5."))]


# ---------------------------------------------------------------------------
# End-to-end: real IE clause, shingled and mutated
# ---------------------------------------------------------------------------


def _first_leaf_with_long_body(text: str) -> str:
    root = parse(text, portal_slug="ie")
    for node in root.walk():
        # Pick the first leaf clause with a body long enough that word
        # 5-shingles are meaningful — sub-50-token bodies don't stress
        # the MinHash estimator.
        if not node.children and node.body_text and len(node.body_text.split()) >= 50:
            return node.body_text
    raise AssertionError("no suitable clause body found in IE fixture")


def test_minhash_jaccard_round_trip_on_real_clause() -> None:
    text = IE_FIXTURE.read_text(encoding="utf-8")
    body = _first_leaf_with_long_body(text)
    original_sig = minhash(shingle(body, k=5), signature_size=128)
    # Mutation: change one word in the body.
    mutated = re.sub(r"\bof\b", "concerning", body, count=1)
    assert mutated != body
    mutated_sig = minhash(shingle(mutated, k=5), signature_size=128)
    # A single-word substitution should leave Jaccard high — the spec
    # bar in CLAUDE.md is "high Jaccard similarity"; 0.7 is comfortably
    # above the default similarity_threshold.
    assert jaccard(original_sig, mutated_sig) > 0.7
