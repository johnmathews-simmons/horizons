# Clause alignment and change detection

*Last revised: 2026-06-04.*

How clauses keep their identity across versions of a legal document, so that the change events feeding the API (per `1. product-questions.md`) reflect *true* changes — added, removed, modified, or moved content — and not artefacts of renumbering or formatting drift.

It is a prerequisite to `3. database-design.md`, which stores and indexes the change events that fall out of alignment.

## What makes this hard

### Irregular structure

Heading conventions vary by jurisdiction, by publisher, and sometimes within a single document. Concretely:

- Some documents number every level (`Part 1 → Section 5 → (3) → (a) → (i)`), each level carrying text.
- Some have numbered structural levels but no text attached to higher levels (the section is a pure container).
- Some are mixed: numbered down to subsection, then unnumbered prose-style sub-headings below.
- Some use Roman numerals, alphabetic labels, or jurisdiction-specific conventions (`§`, `Art.`, `Reg.`, `cl.`).

We cannot rely on a single positional schema. The parser must build a **tree** from heading levels regardless of whether each level is numbered; numbering is captured as a label attribute on the node, not as the identifier. For unnumbered nodes, the path segment is derived from heading text (slugified) or, if heading text is absent, from ordinal-among-siblings.

### Renumbering under insertion

If v1 has sections 1–10 and v2 inserts a new section after section 3, v2 has sections 1–11. v1's section 4 is the *same clause* as v2's section 5; only one section is genuinely new. A naïve label-based diff would report 8 spurious "changes" and miss the real one.

Identity must survive renumbering. Positional labels cannot be the identifier.

## The design: identity vs. label

Every clause record carries two attributes:

- **`clause_uid`** — a stable database-assigned identifier, assigned the first time we see a clause and *carried across versions by alignment*. Not user-visible.
- **`clause_path`** — the positional/heading label as it appears in this particular version (`Part_1/Section_5`). Renumbers freely. This is what the customer sees.

The diff is computed over `clause_uid`. The renumbering example above produces:
- 1 `ADDED` event (the new section, with a new `clause_uid`).
- ~8 `MOVED` events (same `clause_uid` in both versions, different `clause_path`, identical content).
- 0 `MODIFIED` events from the shift.

The customer-facing UI defaults to suppressing `MOVED`-only events unless the user asks for them.

## Alignment pipeline at ingestion

When version *N* of a document arrives, we align its clause tree against version *N–1*. Alignment runs once, at ingestion, and writes a row per clause-pair (or unpaired clause) into `change_events`.

The pipeline tries cheaper, higher-confidence signals first; falls back to fuzzier ones:

### 1. Source-provided IDs

When the upstream source publishes stable per-clause identifiers (e.g. ELI — European Legislation Identifier — URIs, which encode hierarchy down to clause level for many EU and national publishers), use them directly. Two clauses with the same source ID are the same clause.

**Lawstronaut today:** does not surface per-clause identifiers. The API exposes `document_id`, `version`, `source_identifier` and `source_secondary_identifier` at the document level only; clause structure is implicit in the markdown. So source-provided IDs are a *future* lever, useful when Lawstronaut or successors expose them, not something we can rely on at demo time.

### 2. Heading-title match, corroborated by content

When source IDs are absent, pair clauses whose heading titles match exactly *and* whose content is sufficiently similar (similarity threshold from §3 below).

The content corroboration is important: heading text alone is not enough. Reused boilerplate titles like *"Definitions"*, *"Interpretation"*, or *"Short title"* appear in nearly every legal document; matching on title alone would happily pair the "Definitions" section of one Act with the "Definitions" section of an unrelated insertion. Requiring content similarity as well prevents this.

### 3. Content-similarity match, monotonic ordering

For clauses still unpaired, run a similarity match using one of the techniques in the next section, **constrained to non-crossing order**: if v1's clause at position *i* is paired with v2's clause at position *p(i)*, then for all *i < j*, *p(i) < p(j)*. This is the same ordering rule LCS-based diff (Unix `diff`, Myers) uses on lines — applied here to clauses, with fuzzy similarity in place of exact equality as the match criterion. Without it, the matcher would happily pair v1 clause 5 with v2 clause 100 just because the text overlaps — which is nearly always nonsense in ordered legal documents.

In practice this is a Needleman–Wunsch-style dynamic program with edit costs derived from clause similarity scores.

### 4. Residuals → change events

After alignment passes 1–3, every clause in v1 ∪ v2 falls into one of four buckets:

- **`ADDED`** — present in v2, no match in v1.
- **`REMOVED`** — present in v1, no match in v2.
- **`MODIFIED`** — matched, but content differs.
- **`MOVED`** — matched, identical content, different `clause_path`.

Each event row also stores an `alignment_confidence` score — high for source-ID and exact-title-plus-content matches, lower for content-similarity-only matches near the threshold.

## Similarity metrics

For passes 2 and 3 we need a way to ask *"how similar are these two clauses' contents?"* — a score in [0, 1], cheap to compute, robust to small edits, and scalable to documents with thousands of clauses.

Three building blocks, used in combination:

### Token overlap (Jaccard on bag-of-words)

Split each clause into tokens (words, lowercased, light normalisation). Compute `|A ∩ B| / |A ∪ B|`. Cheap and order-insensitive — *"the cat sat"* and *"sat the cat"* score identically. Fine as a coarse filter; weak when local word order matters (e.g. *"shall not"* vs. *"shall"*).

### Shingling (k-shingles)

Split each clause into overlapping *k*-grams of consecutive words — typically *k* = 3 or *k* = 5. *"The quick brown fox jumps"* with *k* = 3 yields the shingle set `{"the quick brown", "quick brown fox", "brown fox jumps"}`. Compare two shingle sets via Jaccard. Preserves local word order; the standard substrate for near-duplicate detection. This is our primary similarity signal.

### MinHash (+ LSH)

A probabilistic compression of shingle sets: hash each shingle with *k* independent hash functions and keep the minimum hash per function. The resulting fixed-size signature (e.g. 128 values) lets you estimate the underlying Jaccard similarity from `(signatures matching) / k`. Comparing two MinHash signatures is O(*signature size*) regardless of clause length.

Locality-sensitive hashing (LSH) groups signatures into bands so candidate-pair lookup is sub-linear in the corpus size — important when a long document has thousands of clauses and we want to find candidate moves without comparing every pair.

### How they fit together

- Shingling defines the underlying notion of "similar text."
- MinHash compresses shingle sets so individual comparisons are cheap.
- LSH narrows the candidate set so we don't run *n*² comparisons on a long document.
- The monotonic-ordering constraint then chooses the actual pairing from the candidate set via DP.

Plain token overlap is reserved for very-short clauses where shingling produces too few *k*-grams to be useful.

## Change types and confidence

Schema-side, every `change_events` row carries:

- `change_type ∈ {ADDED, REMOVED, MODIFIED, MOVED}`
- `alignment_confidence ∈ [0, 1]`
- The before/after `clause_uid`, `clause_path`, and content where applicable.

The API surfaces `change_type` to the customer; `alignment_confidence` is internal but powers UI affordances ("review flagged changes", "hide low-confidence matches"). Customer-facing reports default to filtering out `MOVED` and to filtering out below-threshold confidence.

## Known limitations

- Two clauses with very similar boilerplate content (recurring definitions, recurring penalty clauses) can be mis-paired. The monotonic constraint helps, but doesn't eliminate this. *Example:* in an Act where many sections end with the same standard penalty formula ("A person who contravenes this section commits an offence and is liable on summary conviction to a fine not exceeding €5,000 or to imprisonment for a term not exceeding 6 months, or both"), a section whose body is rewritten between v1 and v2 can score a higher similarity against an *adjacent* section with shorter body content — because the unchanged penalty boilerplate dominates the token overlap. Monotonicity blocks long-range mis-pairings, not this kind of local one.
- Boilerplate-rich large corpora produce **low precision** on modify-and-move mutations. The WU2.4 regression run (see *Calibration* below) found the AL (3.8 MB) and LV (58 KB) fixtures produce 38–42 spurious events on top of the four expected ones — every actual change pulls a chain of near-duplicate paragraphs into the unpaired pool and pass-3's LSH pairs them across versions. Recall stays at 1.0; precision collapses to ~0.1. Tractable via a tighter `similarity_threshold` for those portals (per-portal `tuning_configs/<slug>.yaml`) once we have a customer-side view of which noise is acceptable.
- Non-English fixtures whose body text is character-dense rather than word-dense (e.g. CN — Chinese) under-shingle: whitespace tokenisation produces too few k-grams from a body that *looks* substantial, so the MODIFIED corroboration jaccard drops below threshold and the leaf falls through to pass-3 where it may pair with an unrelated near-duplicate. Per-portal `shingle_k` drops (k=2 or k=3) are the seam; not done yet pending demo-period evidence that any CJK jurisdiction is actually in scope.
- Large-scale restructurings (a whole Part renamed and reordered) will produce noisy alignments. We accept this and lean on the confidence score plus manual override.
- Manual override / annotation of alignments is a later feature, not in demo scope.
- Tuning of the similarity threshold and confidence-suppression threshold is empirical — we set starting values, then adjust against observed corpus behaviour.

## Tuning parameters

Mechanism is settled even where values aren't. Experimental knobs are **runtime-tunable via the UI / config**, not code constants — so we can iterate on them during the demo without a redeploy.

| Parameter | Default | Notes |
|---|---|---|
| Shingling *k* | 5 | Standard for English prose. Drop to 3 for short-sentence or non-English corpora; per-portal override allowed. |
| MinHash signature size | 128 | Sub-linear similarity comparison; trades signature storage for estimation accuracy. |
| LSH band parameters | derived from signature size and target similarity threshold | Tune once corpus scale is known. |
| Persist per-clause MinHash signatures | on | Cheap (128 ints/clause); enables later cross-document near-duplicate detection. |
| Similarity threshold (pair / no-pair) | empirical | Set starting value, then adjust against observed corpus behaviour. |
| Confidence-suppression threshold for customer views | **0.6** (starting value) | Below this, change events are hidden from default client views and surfaced only in a "review flagged" pane. Empirical; tunable via admin UI per the "configuration over code for tuning parameters" principle. Will be re-evaluated against observed corpus behaviour during the demo period. |

## Output shape

- **Alignment confidence** is exposed in the API as a **raw float in `[0, 1]`** — no bucketed labels. 1.0 = source-provided ID match (perfect); high values = exact title plus content; lower values = content-similarity only near the threshold.

## Calibration

The aligner is exercised against every fixture in `data/samples/` by `tests/alignment/test_fixtures.py`. Per fixture, two cases:

- **Identity** — `align(v1, v1)` against the same parsed tree. Must emit zero events. Failure here means the no-change case is producing spurious diffs and the aligner is unsafe for idempotent re-ingestion of an unchanged version.
- **Four-mutation** — `align(v1, mutate(v1, seed=stable_seed(slug)))`. Synthesises exactly one ADDED, one REMOVED, one MODIFIED, and one MOVED change against a deterministically-chosen target set. Expected event count is four; the suite reports precision and recall against that expectation.

Per-fixture score line:

```
fixture            ident   P     R     F1    notes
-----------------  ------  ----  ----  ----  ----------------
ie-27732019-v1     ok      1.00  1.00  1.00
at-32061749-v1     ok      1.00  0.75  0.86  missed MOVED
```

The `notes` column carries any qualitative observation (mis-classified type, missing event); `ident` is a boolean pass/fail for the identity case. The aggregate line at the end averages P/R/F1 across the fixture set and counts identity failures.

The score report is emitted from a session-scoped pytest hook into the captured stdout, so it appears in `pytest -q` output and in CI logs. Format is tabular, ASCII-only, demo-presentable.

Mutation synthesis is deterministic and reproducible: the RNG is seeded by `zlib.crc32(slug)` so the same fixture always produces the same four mutations across runs, and the seed appears in any failure message so failures can be reproduced from the slug alone.

The four tuning knobs (`shingle_k`, `signature_size`, `lsh_bands`, `similarity_threshold`) start at their `_default.yaml` values for every fixture. Per-portal `tuning_configs/<portal>.yaml` overrides are added only when the regression run proves a portal needs a different starting point — react, don't anticipate. The score line is the seam between "this fixture is fine" and "this fixture warrants attention before the demo".

The suite is **not** a hard gate on mutation-case precision. The identity case (`align(v1, v1) == []`) is asserted strictly for every fixture — a non-empty result is a correctness bug and fails the build. The mutation case asserts only that **at least two** of the four expected events were matched (the floor catches algorithm-broken regressions while letting boilerplate-rich corpora report degraded precision honestly). The aggregate quality numbers in the score table are the demo-period diagnostic, not a CI threshold.

Fixtures whose total leaf set or body lengths are too small to support four distinct mutations (currently `ge-4446542-v1`, `hr-6339302-v1`, `jp-1771371-v1`, `kr-5412226-v1` — all under 5 KB) are skipped with a recorded reason; they still run the identity case.

## Implementation

The similarity primitives live in `horizons_core.core.alignment.similarity` and are pure functions over plain Python types:

- `shingle(text, k) -> set[str]` — word k-shingles (whitespace-tokenised). Returns the empty set when the token count is below `k`.
- `minhash(shingles, signature_size) -> list[int]` — deterministic MinHash signature; backed by `datasketch.MinHash` with a fixed seed (`MINHASH_SEED = 1`, part of the wire format).
- `jaccard(a, b) -> float` — the standard fraction-of-matching-positions estimator over two signatures of equal length.
- `lsh_candidates(signatures, *, bands, threshold) -> Iterator[tuple[K, K]]` — sub-linear candidate enumeration via `datasketch.MinHashLSH`, then a tight post-filter on the exact MinHash Jaccard estimate against `threshold`. `K` is any hashable id type the caller picks; the alignment pipeline uses positional ids.

The four tuning knobs (shingle `k`, signature size, LSH bands, similarity threshold) live on `horizons_core.core.alignment.tuning.TuningConfig` — a frozen Pydantic model with a validator that `signature_size % lsh_bands == 0`. YAML snapshots live alongside the parser configs (`tuning_configs/_default.yaml`) and load via `load_tuning_config(name)`. The similarity primitives themselves take primitive ints / floats so they stay trivially testable; the `TuningConfig` is the seam consumed by the alignment pipeline and (later) the admin UI.

The alignment pipeline itself lives in `horizons_core.core.alignment.align`:

- `ChangeEvent` — a frozen Pydantic model with `change_type ∈ {ADDED, REMOVED, MODIFIED, MOVED}`, optional before/after `clause_uid`, `clause_path`, and text, plus a raw float `alignment_confidence ∈ (0, 1]`. Field-presence is enforced by a model validator: ADDED has no before-side, REMOVED has no after-side, MODIFIED has both sides and differing text, MOVED has both sides with identical text and differing path.
- `align(v1: Clause, v2: Clause, *, tuning: TuningConfig = default_tuning_config()) -> list[ChangeEvent]` — runs the four passes against the two clause trees and returns the residual change events.

The alignment unit is **every clause node whose `body_text` is non-empty** — both leaf clauses and intermediate containers that carry their own preamble text. Pure structural containers with no body (`Part 1` in the IE fixture often, headings-only nodes) carry no alignable text and are skipped. The `path` on each event lets the consumer tell parent-vs-child events apart when both fire for the same semantic edit.

`clause_uid` is intentionally stubbed `None` in this unit. The design (above) calls for stable database-assigned UIDs that the alignment threads across versions, but UID assignment is owned by the ingestion version-transaction (a later work unit) — wedging deterministic-uid-from-path here would actively contradict the "identity survives renumbering" invariant. The `align()` return value is the *pairing*; UID materialisation is downstream.

The four passes:

1. **Source-provided IDs.** Stubbed — none of the substrates we ingest today expose stable per-clause identifiers. The pass is wired through the pipeline so future ELI / Lawstronaut work can land without re-shaping the call site.
2. **Anchor equality plus content corroboration.** Two anchors are considered:
   - *Heading anchor* — both clauses carry a non-`None` `heading_text` and the texts are byte-equal. Boilerplate titles (`"Definitions"`, `"Interpretation"`, `"Short title"`) recur across documents, so heading equality alone is not enough — content corroboration is mandatory.
   - *Path anchor* — both clauses carry `heading_text = None` (typical for leaf sub-clauses like `(a)`, `(i)` that the parser does not synthesise a heading for) and their `path` tuples are equal. Path is renumberable in principle, but in the unrenumbered case it is a strong identity signal and is required so unchanged unheaded leaves do not fan out into spurious REMOVED + ADDED pairs.

    Both anchor variants additionally require content corroboration: jaccard estimate above `tuning.similarity_threshold` for long bodies, or exact body-text equality for short bodies (where shingling produces nothing). Mixed pairs (one side heading-bearing, one not) are not considered here — those flow through to pass 3. Pairs emerging from this pass carry `alignment_confidence = max(0.9, jaccard_estimate)`.
3. **Content similarity with monotonic-order DP.** The remaining unpaired clauses go through `lsh_candidates` to surface candidate-pairs whose Jaccard estimate already meets the threshold; a Needleman–Wunsch-style DP over the unpaired sequences picks the maximum-weight monotonic matching from those candidates (so if v1 clause at position *i* is paired with v2 clause at position *p(i)*, then for all *i < j*, *p(i) < p(j)*). Confidence for these pairs is the underlying Jaccard estimate, capped at 0.9.
4. **Residual classification.** Every unpaired v1 clause becomes a `REMOVED` event; every unpaired v2 clause becomes an `ADDED` event. For paired clauses, the rules below apply.

Identity rule (`align(tree, tree)`): a paired clause whose path and body text are both identical is **not** emitted as an event — it is the no-change case. This makes idempotent re-ingestion of an unchanged version a zero-row operation rather than a noisy "every clause MOVED at confidence 1.0" sweep.

MOVED vs MODIFIED classification: MOVED is emitted **only when text is byte-identical and path differs**. Any text drift (even whitespace-only) above identity is `MODIFIED`. A clause whose path also changed still emits a single `MODIFIED` event — `before_path != after_path` on the event itself tells the consumer the clause also moved. This keeps the change-type ontology orthogonal to the path delta and avoids fan-out (one semantic edit → one event).
