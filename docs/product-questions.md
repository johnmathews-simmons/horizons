# Product questions Horizons should be able to answer

The tool — through its API — should be able to answer the following questions about a document. These questions are the reference point for API shape and feature scope.

Two primitives, parameterised by *scope* (whole document or a single clause) and *reference point* (a prior version, a date, or "the last N changes"):

## 1. When was this last changed? — *temporal*

- **Scope:** whole document or a single clause.
- **Returns:** a timestamp and/or version number.
- "How long has this been in its current form?" is `today − this answer`.

## 2. What changed between two reference points? — *differential*

- **Scope:** whole document or a single clause.
- **Reference points:** a prior version, a date, or "the last N changes."
- **Returns:** the affected clauses with before/after content.
- "Which parts changed?" is the location field of the result. "Has anything changed since date X?" is whether the result is non-empty.

## API-shape implications

- A timestamp/version lookup keyed by `(document_id, optional clause_id)`.
- A diff lookup keyed by `(document_id, optional clause_id, reference_point)` returning `(clause_id, before, after)` tuples.
