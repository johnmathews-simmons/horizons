# WU2.2 — Similarity stack

*Last revised: 2026-06-06.*
*Path: journal/260605-wu22-similarity-stack.md.*

*Session 2026-06-05. Branch `worktree-eng-wu2.2-similarity` → ff-merged to `main`.*

Third Track-2 unit. Builds the four similarity primitives the WU2.3 alignment pipeline will compose (`shingle`, `minhash`, `jaccard`, `lsh_candidates`) plus a `TuningConfig` Pydantic model and YAML loader for the four runtime knobs. No coupling to WU2.0/WU2.1 — the primitives take primitive ints and floats; the parser-side configs are unaffected.

## What shipped

1. `packages/horizons-core/src/horizons_core/core/alignment/similarity.py` — four pure functions over plain Python types. `MINHASH_SEED = 1` is declared part of the wire format. `lsh_candidates` is generic over a `K: Hashable` PEP-695 type parameter; uses `datasketch.MinHashLSH` for sub-linear candidate enumeration and post-filters each candidate against the exact MinHash Jaccard estimate so `threshold` is honoured tightly rather than approximately.
2. `packages/horizons-core/src/horizons_core/core/alignment/tuning.py` — frozen Pydantic `TuningConfig` with `Field(ge=…)` / `Field(gt=…, le=…)` per-field bounds plus a `model_validator(mode="after")` that asserts `signature_size % lsh_bands == 0`. YAML loader + lister mirror `portal_config.py` line-for-line.
3. `packages/horizons-core/src/horizons_core/core/alignment/tuning_configs/_default.yaml` — drift-detection snapshot; round-trips against `default_tuning_config()` (asserted by a test).
4. `packages/horizons-core/src/horizons_core/core/alignment/tuning_configs/_empty_test.yaml` — comments-only fixture; exercises the loader's `None → {}` normalisation branch for 100% coverage.
5. `__init__.py` re-exports the new public surface (`shingle`, `minhash`, `jaccard`, `lsh_candidates`, `MINHASH_SEED`, `TuningConfig`, `default_tuning_config`, `load_tuning_config`, `list_tuning_config_names`).
6. `packages/horizons-core/tests/test_similarity.py` (24 tests) — set-semantics, validators, statistical Jaccard bounds, LSH dedup + ordering, an engineered band-collision case to hit the post-filter rejection branch, and an end-to-end check that parses a real IE clause via the WU2.1 portal config and asserts `jaccard(original, single-word-mutation) > 0.7`.
7. `packages/horizons-core/tests/test_tuning.py` (15 tests) — defaults, frozen, validators, YAML drift, empty-YAML fallback.
8. `docs/RFC-2 clause-alignment.md` — added an *Implementation* section pointing at `similarity.py` and noting the `TuningConfig` knobs.
9. `packages/horizons-core/pyproject.toml` — added `datasketch>=1.6` as a runtime dep.

Final tally: 122 default-marker Python tests passing (was 84), 100% line + branch on `similarity.py` and `tuning.py`. Webapp lint/build/tests still green.

## Decisions resolved up-front

The session opened with four pinned questions (`AskUserQuestion` with previews) before any edit. Resolutions:

1. **Word shingles, not character shingles** — robust to whitespace/punctuation noise, ordered n-grams preserve `"shall not"` polarity. A future `TuningConfig.shingle_kind` can introduce character mode if the CZ / non-Latin substrates demand it.
2. **Generic `K: Hashable` ids on `lsh_candidates`** — WU2.3's alignment pipeline will pair clauses by their `clause_path` tuple, not by list position. PEP-695 syntax made this trivial.
3. **YAML loader now**, mirroring `portal_config.py` — chosen over "in-process default only" because the loader skeleton is two functions and gives WU2.3 + the admin UI a stable seam without a later refactor.
4. **Literature defaults** — `shingle_k=5, signature_size=128, lsh_bands=16, similarity_threshold=0.7`. Documented as "starting point, calibrate during WU2.4."

## What I considered and didn't do

1. **A no-cast pyright-clean variant.** datasketch ships no PEP-561 stubs, so pyright in strict mode flags `MinHash.update`, `m.hashvalues`, and `lsh.query` as partially unknown. Two options: wrap each call in `cast("Any", …)` at the boundary, or write a typed shim module. The cast pattern is local (4 sites, all in one file), so I stayed inline. A shim would have been pure ceremony.
2. **Computing `bands` from `threshold` via datasketch's `weights` optimisation.** Passing `params=(bands, rows)` explicitly to `MinHashLSH` makes both numbers load-bearing in `TuningConfig`. Using the threshold-driven optimiser would have hidden one of them. The CLAUDE.md "runtime-tunable, surfaced in the UI" line tilts toward explicit.
3. **Hypothesis property tests under the `nightly` marker.** The spec listed them as optional. The 39 deterministic tests already hit every line and every branch, plus the band-collision case and the IE-fixture round trip. Adding nightly property tests for `shingle` and `jaccard` would be defensible but is not load-bearing for WU2.3. Deferred.
4. **Caching the MinHash probe.** Reconstructing a `MinHash(num_perm=…, seed=…)` per probe is cheap (constructor allocates an ndarray). Pre-building one and re-pointing `hashvalues` would be a micro-optimisation that doesn't show up at WU2.2's scale.

## Gotchas captured

1. **`datasketch.MinHash` is seeded.** Without `seed=MINHASH_SEED` the hash permutations vary across processes and the "stable across calls" test fails. The seed is documented as wire-format and re-asserted by a test (`test_minhash_seed_constant_is_exposed`).
2. **`datasketch.MinHashLSH` `threshold` is approximate.** With explicit `params=(bands, rows)` the threshold arg is cosmetic; band-collisions can pair signatures whose estimate is below the asserted threshold. The post-filter via `jaccard()` is what makes the public `threshold` argument honest. The `test_lsh_candidates_post_filter_drops_band_collisions_below_threshold` test constructs a synthetic pair that exercises this exact path.
3. **Ruff TC006 quotes `cast` type expressions.** With `from __future__ import annotations`, `cast(Any, …)` lints — switch to `cast("Any", …)`. Ruff `--fix` does this automatically.
4. **Ruff UP047 prefers PEP-695 generics.** `def f[K: Hashable](…)` over the older `TypeVar("K", bound=Hashable)` form. Free upgrade — and the `TypeVar` import can go.
5. **Adding a runtime dep to a `uv` workspace member** is just an edit to `packages/horizons-core/pyproject.toml`; `uv sync` updates `uv.lock` at the workspace root.

## Pattern reused from WU2.1

The whole `tuning_configs/` directory layout is a deliberate copy of `parser_configs/` — same `_default.yaml` snapshot pattern, same `load_*` / `list_*` pair, same `_SUFFIX` constant, same drift-detection test. WU2.1's lesson was that hatchling's default file-inclusion ships the subpackage YAMLs as long as `__init__.py` is present; no `force-include` needed. Confirmed again here.

## Next: WU2.3 — alignment pipeline

The pieces are in place. WU2.3 composes them: parse v1 and v2 into clause trees (WU2.0/WU2.1), compute shingle + minhash per clause body, run `lsh_candidates` to surface candidate pairs, run a monotonic-ordering Needleman–Wunsch DP over the candidate pairs to choose the actual alignment, and emit `ChangeEvent` rows (ADDED / REMOVED / MODIFIED / MOVED) per `docs/RFC-2 clause-alignment.md`. `TuningConfig` is the seam the pipeline reads.
