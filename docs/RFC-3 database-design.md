# Database design priorities, constraints, and principles

*Last revised: 2026-06-04.*

The job of the database is to answer the questions in `1. product-questions.md` quickly and consistently across a large, growing, append-only corpus of legal documents. This doc captures the constraints, the principles that follow from them, and the database-shape implications. Schema details are deferred.

## Performance target

- **Max query time: 3 seconds**, p95, for any single API call that maps directly to one of the three primitives in `1. product-questions.md`.
- *Easy:* per-document queries (temporal / differential at document or clause scope) — sub-100 ms with normal indexes.
- *Tight but achievable:* heavy corpus queries (e.g. *"what changed in EU finance laws in the last 6 months"* — potentially hundreds of documents, thousands of clause-changes). Requires precomputed change events and pagination on large result sets.

## Scale assumptions

| Dimension | Estimate |
|---|---|
| Jurisdictions | ~300 (countries + treaty areas + sub-national bodies) |
| Documents per jurisdiction | 100s–10,000s, very long-tailed |
| Total active documents | low millions, plausibly |
| Versions per document | 1–10 over its lifetime; consolidated codes far more |
| Clauses per document | 10s–10,000s |
| Document size (markdown) | p50 ~150 KB; p99 ~5 MB; outliers (US Internal Revenue Code, EU REACH annexes, ACA) to 20+ MB |
| Write pattern | Bursty — ingestion runs when Lawstronaut publishes; can be batched |
| Read pattern | Continuous, latency-sensitive |
| History retention | Forever — legal corpus is by nature historical |

These are rough order-of-magnitude figures, not measurements. Re-estimate once we have real ingestion volumes.

## Principles

1. **Append-only / immutable history.** Document versions are never updated or deleted. "What did this clause say on date X" must always be answerable.
2. **Clause-level granularity is the working unit.** One row per `(document_version, clause_path)`. The differential primitive operates on clauses; the database should too.
3. **Change events are first-class precomputed records.** On ingesting version *N* of a document, align it against version *N–1* once (see `2. clause-alignment.md`) and write each clause change as a row in a `change_events` table indexed by `(jurisdiction, sector, detected_at, effective_date)`. Each row carries `change_type` in `{ADDED, REMOVED, MODIFIED, MOVED}` and an `alignment_confidence` score so the read side can surface or suppress low-confidence pairings. Never compute diffs at query time. **`effective_date` provenance.** Lawstronaut today surfaces only `publication_date` at the document level. For the demo, `effective_date` is populated as `publication_date + per_jurisdiction_default_lag` (a small lookup table — e.g. EU directives default to 20 days after publication, UK statutory instruments to next-quarter-day, etc., with `0` as the generic fallback). Overrides for individual documents can be added via the admin UI when a stated commencement date is known. This is a deliberate approximation: the "horizon" framing is *approximate* at demo time, and improved precision is a post-demo work item (parsing in-document commencement clauses per jurisdiction, or sourcing from official commencement registers).
4. **Stable clause identity, separate from positional label.** A clause has two distinct attributes: a `clause_uid` (assigned at ingestion, *carried across versions by alignment*, never user-visible) and a `clause_path` (the positional/heading label as it appears in this version, e.g. `Part_1/Section_3/2/a`, which renumbers freely). Diffs are taken over `clause_uid`, not over `clause_path`. A new section inserted mid-document produces one `ADDED` event and a series of `MOVED` events for shifted neighbours — not N spurious `MODIFIED` events. Mechanics and edge cases live in `2. clause-alignment.md`.
5. **Full markdown always lives in blob storage, not row cells.** Every document's full markdown goes to Azure Blob Storage; the Postgres row holds a content hash, byte length, and URL. Per-clause text stays inline in Postgres for query and diff. No size threshold — a single code path is simpler than a branching rule, blob storage is friendly to CDN-style serving for "show me the original," and Postgres rows stay slim and predictable. (Empirical sizing from the 2026-06-04 fixture pull: p50 ~12 KB, p83 ~100 KB, max 3.8 MB.)
6. **Configuration over code for taxonomies.** Jurisdictions and sectors come from the Lawstronaut feed (`taxonomy`, `jurisdiction`) and are loaded as data. Adding a new portal or jurisdiction must not require a schema change or redeploy.
7. **Read-heavy schema design.** Optimise for the three primitives' read paths. Write-side cost (ingestion, diff computation, index maintenance) is acceptable.
8. **Per-user state is segregated from the corpus and protected by Row-Level Security.** Alongside the corpus tables, the schema has private-state tables keyed by `user_id` — at minimum `watchlists`, `saved_queries`, `dashboards`, `alert_preferences`, and `subscriptions`. These tables and the corpus tables read under the `client` role are protected by Postgres RLS policies. On **private-state tables**, RLS is the primary guarantee: `user_id = current_setting('app.user_id')` on every row; the repository layer enforces the same predicate as belt-and-braces. On **corpus tables**, the **repository layer's subscription-scope join is primary** and RLS — via a security-definer function that returns the requesting client's subscription scope — is the second layer that catches missed joins. RLS is the database-side guarantee of the multi-tenant isolation principle named in `CLAUDE.md` and detailed in `4. services.md`; the full enforcement layering (RLS + repository pattern + lint-banned raw SQL + multi-user tests) lives in doc 4 §"Public API service / How".

## Database choice direction

**Default: PostgreSQL.** Relational schema for documents / versions / clauses / change events; JSONB for source-provided metadata that we don't want to flatten; `GIN` indexes on JSONB and on tsvector for full-text; range indexes on dates. Strong operational story on Azure (Azure Database for PostgreSQL — Flexible Server). Boring is good.

**Originals: Azure Blob Storage.** Full-document markdown and any source PDFs/HTML go here, referenced by hash + URL from the Postgres row.

**Search (later, if needed): a dedicated index.** Postgres FTS covers the demo. If full-text-search latency or relevance becomes a constraint, add Meilisearch or Azure AI Search as a read-side projection of the change-events and clauses tables — don't make it primary.

**Not chosen, and why:**
- *Document store (Mongo, Cosmos):* clause-level granularity wants relational joins; per-clause indexing in a doc store is awkward.
- *Graph database:* the cross-document relationships are weak — citations between Acts aren't load-bearing for the primitives we've defined. Overkill.
- *Databricks / lakehouse:* what the previous version of this project ran on; deliberately avoided here. Wrong shape for low-latency API serving.

## Implications for the three primitives

| Primitive | Hot path | What it reads |
|---|---|---|
| Discovery | `change_events` filtered by scope + time | identity columns only (`document_id`, `clause_uid`, `change_type`, timestamps) — no before/after content; indexed lookup |
| Temporal | `change_events` aggregated (most recent per document/clause) | identity + timestamp columns; indexed lookup |
| Differential | `change_events` joined to clause text on both sides of the diff | identity columns plus the before/after clause text; larger payload, paginated |

All three read primarily from `change_events`. That table is the load-bearing artefact.

## Open questions

- How fast does the change-events table grow in practice? Affects partitioning strategy and retention-tiering.
- Do we need read replicas for the demo, or is a single instance enough at demo-scale traffic?
- ~~What's the right `alignment_confidence` threshold below which a change event is flagged for review or hidden from default customer views?~~ **Resolved 2026-06-04:** starting value **0.6**, tunable via the admin UI. Re-evaluated against observed corpus behaviour during the demo period. See `2. clause-alignment.md` §"Tuning parameters".
