# 2026-06-05 — WU4.6: OpenAPI + regenerated endpoints.md

Closes Track 4. The Horizons FastAPI app already exposed
``/openapi.json`` automatically; this WU formalises the doc surface
by regenerating ``docs/api/endpoints.md`` from the live spec and
gating drift via a pre-commit hook.

## What shipped

### Endpoint tagging

Every route in WU4.0–4.5 already carried a ``tag`` argument when its
router was constructed (``health`` / ``auth`` / ``me`` /
``watchlists`` / ``discovery`` / ``temporal`` / ``differential`` /
``admin``). The generated OpenAPI groups operations by tag without
extra wiring. No code touched.

### `scripts/regen_endpoints_md.py` (new)

- Lives at
  ``packages/horizons-api/scripts/regen_endpoints_md.py``.
- Bootstraps an ephemeral RSA keypair so ``create_app()`` succeeds
  without real config; sets ``HORIZONS_DB_URL`` to a non-routable
  default. The script never opens a Postgres connection — it
  constructs the app and reads ``app.openapi()`` only.
- Renders Markdown grouped by tag, anchor-style:
  - Per-operation header ``### METHOD /path``.
  - Parameter table (path / query / header / cookie).
  - Request body table from the ``application/json`` content schema.
  - Response table with the JSON schema shape per status code.
  - ``$ref`` resolution against ``components.schemas`` (one level —
    nested refs render by component name).
  - Nullable types render as ``T?``; arrays as ``array<T>``; JSON
    Schema ``format`` flows into the type column.
- Idempotent: running twice produces a byte-identical output (verified
  by ``cp /tmp/a && regenerate && diff``).
- ``--check`` mode returns 0 when ``docs/api/endpoints.md`` matches
  the regenerated output, 1 with a one-line stderr hint otherwise.

### Decision: regenerate, not hand-write

The plan's WU4.6 acceptance allows either. I picked regeneration
because the Horizons API surface gains 1–2 endpoints per WU; manual
docs drift the moment a new endpoint lands. The cost of regeneration
is one fast pre-commit hook (the script reads the OpenAPI in <1 s on
local hardware).

### File reshape: `endpoints.md` is now Horizons

The previous ``docs/api/endpoints.md`` was the **upstream Lawstronaut**
reference, captured 2026-06-04 from the dev portal. WU4.6 claims the
``endpoints.md`` slot for the Horizons surface (FastAPI we ship), so
the Lawstronaut reference moved to ``lawstronaut-endpoints.md`` via
``git mv`` — content unchanged, link target updated.

Inbound links:

- ``docs/api/README.md`` rewritten to clearly separate "Horizons (what
  we ship)" from "Lawstronaut (what we consume)". The README now
  points at ``endpoints.md`` for the Horizons reference and
  ``lawstronaut-endpoints.md`` for the upstream.
- ``CLAUDE.md`` "Read first" step 3 updated with the same split.

### Pre-commit hook

``.pre-commit-config.yaml`` gains a ``local`` repo with one hook,
``regen-endpoints-md``, that runs the script with ``--check``. The
hook fires only when files that can affect the OpenAPI change
(``packages/horizons-api/src/...`` or the script itself or
``docs/api/endpoints.md``). It runs from the workspace venv via
``uv run --no-sync`` so it does not re-resolve dependencies on every
invocation.

CI inherits the gate via the ``pre-commit run --all-files`` step
already in the workspace lint job. No new CI workflow needed.

## Design decisions worth keeping

1. **The script renders the wire shape, not the prose.** Long-form
   discussion (auth flow, scope discriminator, cache semantics) stays
   in sibling docs (``auth.md``, ``concepts.md``,
   ``horizons-primitives.md``) and the generated file's preamble
   links to them. Prose belongs where humans edit; the wire shape
   belongs in the generator.
2. **No nested ``$ref`` resolution.** Component schemas render by name
   (``Foo`` / ``Bar``). Readers cross-reference the component table at
   the end of the OpenAPI directly; the doc stays scannable and the
   generator stays simple.
3. **Ephemeral RSA keypair per run.** The script never depends on
   process env or a checked-in test key — it mints a throwaway key
   each invocation. The OpenAPI generation path never uses the
   provider, so the key is never exercised.
4. **``--check`` exit-1 wording matches the fix command.** Stderr
   carries the exact ``uv run …`` invocation a contributor needs to
   refresh the doc. Saves the next person the round-trip to the
   journal / script body.

## Status by suite (end of WU4.6)

- 515 passing (unchanged from WU4.5 — script + doc reshape add no
  test runs). Full pytest sweep + integration suite clean against
  the migrated Postgres testcontainer.
- ``ruff check`` / ``ruff format`` / pyright strict / ``pre-commit
  run --all-files`` (including the new ``regen-endpoints-md`` hook):
  clean.

## What's next

- WU7.2 (admin health endpoints) — unblocked by WU4.5 + WU7.0.
- WU7.4 (admin audit log surface) — unblocked by WU4.5.
- Both are separate session work per the WU4.5/4.6 prompt.

The Track-4 surface is now complete: auth (WU4.0–4.2), private state
(WU4.3), the three primitives (WU4.4), admin subscription writes
(WU4.5), and a self-refreshing endpoint reference (WU4.6).
