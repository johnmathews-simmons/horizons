# Horizons

A regulatory-change intelligence demo: watch public legal sources and flag, at the clause level, when laws change.

The productionised version of this idea is aimed at large multinational organisations that need to know — quickly and precisely — when a statute or regulation they're exposed to is amended. The demo focuses on the headline experience: *"this clause changed, here is the before and after."*

## Status

Pre-code. This repository currently contains:

- `docs/api/` — local reference for the [Lawstronaut](https://lawstronaut.com) v2 API (auth, concepts, endpoints, operational notes), captured 2026-06-04.
- `data/samples/` — one real Irish Statute Book Act in markdown, used as a fixture for the clause parser and diff engine when those exist.
- `CLAUDE.md` — project context and architectural decisions for AI-assisted development.

No Python package, tests, or CI yet. Those are coming.

## Approach

A few decisions are already load-bearing:

- **Clause-level diff, not document-level.** Legal documents have structure (Part / Section / sub-section / (a) / (i)). A new *version* of an Act typically only touches a handful of clauses — that's what we want to surface.
- **Markdown is the content substrate.** The Lawstronaut `/v2/contents/markdown` feed preserves the structural anchors we need as clause boundaries; full-text and PDFs are weaker.
- **Heading-anchored clause IDs.** A clause keeps its identity across versions even when neighbours move or get renumbered.
- **Configuration over code for sources.** Adding a new jurisdiction or portal must not require a redeploy.
- **Azure Container Apps** as the deployment target — not Kubernetes (overkill at this scale), not Databricks (what an earlier iteration of this idea ran on).

## Repository layout

```
docs/api/        Lawstronaut v2 API reference (start at docs/api/README.md)
data/samples/    Real legal-document fixtures
CLAUDE.md        Project context for AI-assisted development
```

## License

Not yet licensed. Treat as all rights reserved until a `LICENSE` is added.
