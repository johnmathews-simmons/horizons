# Horizons

Horizons is a regulatory-change intelligence service that watches public legal sources and surfaces **upcoming** legal changes — laws, regulations, and official guidance that have been published but have not yet taken effect — so downstream compliance and legal teams have lead time to prepare. The "horizon" refers to the forward-looking framing: changes visible on the legal horizon, not changes already in force. This repo is the early scaffolding of the demo build.

## Where to start

Design docs (read in order — each builds on the previous):

1. [About these docs](docs/0.%20about-these-docs.md) — how the chain is structured and why.
2. [Product questions](docs/1.%20product-questions.md) — the discovery / temporal / differential primitives the tool must answer.
3. [Clause alignment](docs/2.%20clause-alignment.md) — how clauses keep identity across versions; the alignment pipeline.
4. [Database design](docs/3.%20database-design.md) — performance target, scale assumptions, principles.
5. [Services](docs/4.%20services.md) — the three deployable services and their cross-cutting principles.

Reference material:

- [Lawstronaut v2 API reference](docs/api/README.md) — local capture of the upstream content API (auth, concepts, endpoints, operational notes).
- [Sample fixtures](data/samples/README.md) — 31 real legal documents in markdown, captured 2026-06-04, used as parser fixtures.

## Layout

`uv` workspace at the repo root with three Python members under `packages/horizons-{core,ingestion,api}`, plus a Vue 3 webapp at `packages/horizons-webapp` (npm-managed, not a `uv` member). Cross-package integration tests live at `tests/`.

For first-time setup, day-to-day commands, and the lint / type / test / pre-commit sweep, see the **Commands** section in [CLAUDE.md](CLAUDE.md#commands).

## Licensing

Closed-source. All rights reserved. The demo period (~2026-06-08, 1–2 days public) does not change that — viewing during the demo confers no license to use, modify, or redistribute. See [CLAUDE.md → Licensing](CLAUDE.md#licensing).
