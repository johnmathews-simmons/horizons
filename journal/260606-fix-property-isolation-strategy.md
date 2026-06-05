# 2026-06-06 — Fix: property-isolation Hypothesis strategy under NOT NULL document_id

Surgical test-side fix. The WU1.8 property test
(`tests/isolation/test_property_isolation.py`) pre-dated migration 0009,
which made `watchlists.document_id` `NOT NULL REFERENCES documents(id)` with
a `UNIQUE (user_id, document_id)`. Routine `uv run pytest -m integration`
runs surfaced the gap because the test carries both `@pytest.mark.integration`
and `@pytest.mark.nightly` — the integration marker pulled it into a
selection it was never meant to be part of.

## Bug

Hypothesis was generating `ClientBlueprint(n_watchlists=N, doc_scopes=())`,
i.e. "this client owns N watchlists but zero documents." The test's
`_seed_watchlist` SQL only wrote `(user_id, name)` — no `document_id` —
and the strategy didn't constrain `n_watchlists` against `n_docs`.
Hypothesis shrank to the minimal counterexample `n_watchlists=1,
doc_scopes=()` and Postgres rejected the insert:

    sqlalchemy.exc.IntegrityError: (psycopg.errors.NotNullViolation)
    null value in column "document_id" of relation "watchlists"
    violates not-null constraint

A DB-level constraint catching a test-side strategy gap is the
defence-in-depth working — the schema is right, the test was lying.

## Fix

Three local edits in `tests/isolation/test_property_isolation.py`:

1. **Strategy** — `n_watchlists` is now drawn from
   `st.integers(min_value=0, max_value=n_docs)` after `n_docs` is fixed
   for that blueprint. The watchlist count can never exceed the document
   count for the same client, so every generated watchlist has a real
   doc to reference. With `n_docs == 0` the client just contributes zero
   private rows; the universal isolation invariant is shape-independent
   so the empty-client case is still exercised through the other clients
   in the plan.

2. **`_seed_watchlist`** — adds `document_id: uuid.UUID` and includes it
   in the INSERT. Now matches the shape `_make_watchlist` already uses in
   `tests/isolation/conftest.py` (the WU1.7 two-client gate was already
   updated alongside migration 0009; this property test was missed).

3. **`_apply_plan`** — reordered: seed docs first, then watchlists.
   `_seed_watchlist(..., docs[w].doc_id, ...)` for `w in range(n_watchlists)`.
   Distinct `w` → distinct `docs[w]` → distinct `document_id`, which
   satisfies `watchlists_user_document_unique`.

## Marker decision: drop `@pytest.mark.integration`

The test now carries only `@pytest.mark.nightly`. Rationale:

- The default `addopts = "-m 'not nightly'"` keeps it out of routine
  `uv run pytest`.
- `.github/workflows/nightly.yml` selects with `-m nightly`, so nightly
  CI is unaffected.
- The previous combined `@integration + @nightly` meant any developer
  running `uv run pytest -m integration` (the documented "Docker-backed
  integration tests only" command in `CLAUDE.md`) silently pulled in a
  slow Hypothesis test that was never meant for that selection. Dropping
  the integration marker restores the symmetry the doc string at lines
  23-25 already promises.

No change to CLAUDE.md, pyproject.toml, or nightly.yml — the
description of the markers there is still accurate.

## `.hypothesis/examples/` left in place

There are three test-keyed example directories. I don't know which
belongs to this test, and the user-side instruction was explicit: never
blanket-delete. Hypothesis replayed whatever was cached on the first
fixed-strategy run and the test went green, so any historical
counterexamples are either unreachable under the new strategy or now
pass on their merits.

## Verification

Full gate, in the `worktree-fix-property-isolation-document-id-strategy`
worktree:

- `uv run ruff check .` — All checks passed!
- `uv run pyright` — 0 errors, 26 pre-existing warnings (all
  `reportMissingTypeStubs` for `testcontainers.postgres`, unchanged).
- `uv run pytest` — 555 passed, 4 skipped (alignment fixtures too
  small), 1 deselected (the property test, as expected).
- `uv run pre-commit run --all-files` — every hook passed including
  `regen-endpoints-md (--check)`.

5x stability loop on the property test (the user-mandated gate):

    === run 1 ===  1 passed in 4.17s
    === run 2 ===  1 passed in 3.79s
    === run 3 ===  1 passed in 4.00s
    === run 4 ===  1 passed in 3.40s
    === run 5 ===  1 passed in 3.86s

Five consecutive green runs, ≈25 Hypothesis examples each × `N ∈ [2, 5]`
clients each, against testcontainers Postgres 18.

## Scope discipline

Touched files this WU:

- `tests/isolation/test_property_isolation.py` — strategy +
  `_seed_watchlist` + `_apply_plan` reorder + marker drop.
- `journal/260606-fix-property-isolation-strategy.md` — this file.

Did not touch: application code, migrations, other tests, the
`.hypothesis/examples/` directory, CLAUDE.md, pyproject.toml,
nightly.yml. Concurrent sessions P (api/scripts/auth/repos) and Q
(webapp) had no file overlap.
