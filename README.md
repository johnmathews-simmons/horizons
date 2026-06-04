# Horizons

A regulatory-change intelligence demo: surface *upcoming* changes to laws, regulations, and official guidance — at the clause level — **before they take effect**, so customers have lead time to prepare.

Legal updates are typically published ahead of their effective date, sometimes by weeks, sometimes by years. Horizons watches public legal sources and flags those changes as soon as they appear, so a customer hears *"this clause is changing on date X — here is what it says now and what it will say"* while there is still time to act, rather than discovering the amendment after it has already landed. That's the *horizon* in Horizons: changes visible on the legal horizon, not changes already in force.

The productionised version is aimed at large multinational organisations whose compliance and legal teams today rely on manual horizon-scanning to keep up.

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
