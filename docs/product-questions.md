# Product questions Horizons should be able to answer

The tool — through its API — should be able to answer the following questions about a legal document. These questions are the reference point for API shape and feature scope.

## The questions (as originally posed)

1. When was the last change to this document?
2. What was the last change to this document?
3. For how long has this part of the document been present in its current form? When was it last changed?
4. Does the latest version of a document contain any changes compared to the version of the document present on a given date?
5. Which parts of the document have changed?
6. What are the most recent changes in this document?

## The two underlying primitives

The six questions above collapse onto **two primitives**, parameterised by *scope* (whole document vs. a single clause) and *reference point* (a prior version, a date, or "the last N changes").

### 1. When was this last changed? — *temporal*

- **Scope:** whole document, a section, or a single clause.
- **Returns:** a timestamp (or version number, or both).
- Covers Q1 (document-level) and the timestamp half of Q3 (clause-level).
- The first half of Q3 — "for how long has this been in current form" — is just `today − this answer`, not a separate question.

### 2. What changed, between two reference points? — *differential*

- **Scope:** whole document or a specific clause.
- **Reference points:** a prior version, a date, or "the last N changes."
- **Returns:** the list of affected clauses with before/after content.
- Covers:
  - Q2 ("the last change") → N = 1
  - Q6 ("most recent changes") → N > 1
  - Q5 ("which parts changed") → the location field of the result
  - Q4 ("any changes since date X?") → boolean of `result is non-empty`

## Implications for API shape

- A timestamp/version lookup keyed by `(document_id, optional clause_id)`.
- A diff lookup keyed by `(document_id, optional clause_id, reference_point)` returning a list of `(clause_id, before, after)` tuples.
- Everything else — "is anything changed?", "for how long has X been current?", "what are the last N changes?" — is a projection or simple parameterisation of those two.

This is a *scope* document, not a contract. It tells us what questions a customer should be able to ask; the actual endpoint design will live in `docs/api/` once it exists.
