# Product questions Horizons should be able to answer

The tool — through its API — should be able to answer the following questions about a document. These questions are the reference point for API shape and feature scope.

Three primitives, parameterised by *scope*, *reference point*, and *filter*.

## 1. Discovery — which documents have changed?

- **Scope:** the corpus (with filters), or a single document (boolean: "has this changed?").
- **Inputs:** filter (jurisdiction, sector) + time window.
- **Returns:** list of documents that changed, optionally with the locations of the changes within them.
- The customer query *"which financial laws have changed in the last 6 months"* is this primitive with `sector=finance, since=6mo`.

## 2. Temporal — when was this last changed?

- **Scope:** whole document or a single clause.
- **Returns:** timestamp and/or version number.
- Duration questions ("how long has this clause been in its current form?") fall out of this primitive — subtract the last-changed timestamp from today; no separate query needed.

## 3. Differential — what changed between two reference points?

- **Scope:** whole document or a single clause.
- **Reference points:** a prior version, a date, or "the last N changes."
- **Returns:** the affected clauses with before/after content.
- "Which parts changed?" is the location field of the result. "Has anything changed since date X?" is whether the result is non-empty.

## Filtering dimensions

Applies across all three primitives (most obviously to discovery):

- **Jurisdiction** — country, treaty area (e.g. EU), continent.
- **Sector** — finance, agriculture, employment law, etc.
- **Time** — absolute dates or relative windows ("last 6 months").

Sector and jurisdiction taxonomies come from the Lawstronaut feed (`taxonomy`, `jurisdiction`) — we expose what's there, we don't invent our own classification.

## Delivery channels

Same query semantics, multiple surfaces:

- Web portal (interactive).
- JSON over HTTP (programmatic).
- Email alerts — future, out of demo scope.

## API-shape implications

- A discovery endpoint keyed by `(filter, time_window)` returning `(document_id, version_jump, change_locations?)`.
- A timestamp/version lookup keyed by `(document_id, optional clause_id)`.
- A diff lookup keyed by `(document_id, optional clause_id, reference_point)` returning `(clause_id, before, after)` tuples.
