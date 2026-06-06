# 2026-06-05 — WU1.2 corpus tables

Fourth session of the day. Shipped the corpus spine — `documents`,
`document_versions`, `clauses` — the read substrate the public API
and the ingestion worker will both depend on.

## What shipped

### WU1.2 — Corpus tables (`ebab30d`)

Three append-only aggregates:

| Table | What it holds | Mutability |
| --- | --- | --- |
| `documents` | stable identity for an upstream legal text (`id`, `jurisdiction`, `sector`, `lawstronaut_document_id`, `title`, `created_at`) | strict append-only via trigger |
| `document_versions` | time-stamped re-issues (`id`, `document_id`, `version_label`, `publication_date`, `effective_date`, `content_blob_container`, `content_blob_key`, `content_sha256`, `content_bytes`, `created_at`) | strict append-only via trigger |
| `clauses` | heading-anchored fragments of a version (`id`, `document_version_id`, `clause_uid`, `clause_path`, `text_content`, `ord`) | strict append-only via trigger |

UUIDv7 PKs via Postgres 18's native `uuidv7()`. `ON DELETE RESTRICT`
on both FKs (`document_versions → documents`,
`clauses → document_versions`) — corpus rows are reference data; the
absence of a delete path is the point.

Constraints:
- `documents.lawstronaut_document_id UNIQUE` — re-ingest is idempotent
  against the upstream key.
- `UNIQUE(document_id, version_label)` on `document_versions` — the
  natural identity for an ingester to be idempotent against.
- `UNIQUE(document_version_id, clause_path)` on `clauses` — one row
  per positional address per version.
- `CHECK(octet_length(content_sha256) = 32)` and
  `CHECK(content_bytes >= 0)` on `document_versions` reject malformed
  integrity values at the DB layer.

Indexes for the three product primitives (discovery / temporal /
differential — see `docs/RFC-1 product-questions.md`):
- `idx_documents_jurisdiction_sector` for subscription-scope filtering
  (every client query starts with "documents I'm subscribed to").
- `idx_document_versions_doc_effective` for "what was in force at time
  T for document X" — the temporal primitive's hot path.
- `idx_clauses_version_ord` for reading a version's clauses in
  document order.
- `idx_clauses_clause_uid` for cross-version clause-identity lookup —
  the differential primitive ("how did this clause change between
  versions?").

Grants follow the corpus-as-read-substrate pattern: `api_app` SELECT
only; `ingestion_worker` SELECT + INSERT (writes corpus, reads its own
prior work during the alignment pass that assigns `clause_uid`);
`admin_bypass` no static grants (reach via `SET LOCAL ROLE`). Neither
worker role gets UPDATE — the append-only triggers would reject it
anyway, but absent grants is the cheaper first layer.

### Q1–Q2 decisions

1. **Effective-date lag inference deferred to the ingester.**
   `effective_date` is just a nullable `timestamptz` in the schema; the
   per-jurisdiction default-lag table (publication + lag = effective —
   design doc 3 §Principles 3) lives with the ingester code in a later
   WU. Keeps WU1.2 a pure schema unit. Both dates are nullable since
   some upstream feeds omit one or both.

2. **Blob pointer is structured `(content_blob_container,
   content_blob_key)`, not a single URL.** Provider-agnostic: the
   storage host / account / endpoint stays in environment config, not
   row data. Demo target is Azure, so the cost is small either way —
   chose portability now because retro-fitting structure across a
   populated table is more painful than the converse.

### Append-only enforcement

Each of the three tables has its own `BEFORE UPDATE` trigger
(`documents_no_update`, `document_versions_no_update`,
`clauses_no_update`) that rejects every UPDATE outright. The shape
matches WU1.1's `subscription_scopes_no_update`: corpus rows are
immutable, corrections are a new row. The three reject functions are
trivially distinct so error messages name the offending table.

### Autogen drift check passes clean

Ran `alembic revision --autogenerate -m drift_check` against the
migrated container — produced an empty upgrade/downgrade. Models and
migration agree on TEXT vs String, comment, constraint name, and index
shape. The drift this catches is real (WU1.1 caught two of them last
session); doing the check before commit caught nothing this time, which
is the desired outcome — discipline carried over.

### Tests

11 new integration tests in `tests/test_corpus_tables_migration.py`,
testcontainers PG 18, sync (same Alembic / event-loop reason as WU1.1):

| Test | What it verifies |
| --- | --- |
| `test_corpus_tables_exist_with_expected_columns` | tables, columns, types, nullability across all three |
| `test_corpus_tables_owned_by_schema_owner` | DDL ownership |
| `test_expected_indexes_present` | all four documented indexes exist |
| `test_documents_uuidv7_default` | `uuidv7()` default fires on all three PKs; version byte is 0x7 |
| `test_document_versions_unique_label_rejects_duplicate` | `(document_id, version_label)` uniqueness |
| `test_clauses_unique_path_per_version_rejects_duplicate` | `(document_version_id, clause_path)` uniqueness |
| `test_documents_reject_update` | append-only trigger on `documents` |
| `test_document_versions_reject_update` | append-only trigger on `document_versions` |
| `test_clauses_reject_update` | append-only trigger on `clauses` |
| `test_per_role_grants_match_design` | api_app SELECT only; ingestion_worker SELECT+INSERT; admin_bypass nothing |
| `test_documents_lawstronaut_id_unique` | upstream-key uniqueness across (jurisdiction, sector) variants |

Full suite: **28 passed**, **24 integration**, no skips, no warnings.
Pre-commit (ruff + ruff-format + the whitespace / yaml / toml checks)
clean.

### Documentation

1. Extended `db/schema.md` with anchor-style sections for the three
   corpus aggregates (column tables, constraint rationale, index
   purpose, mutability). Updated the append-only enforcement section
   to list the three new triggers and the multi-tenant access section
   to reflect the corpus-grants shape.

2. Updated `db/roles.md`'s per-table grants table with the three corpus
   rows and added a paragraph explaining the corpus grant pattern
   (api_app reads, ingestion_worker reads+writes, no UPDATE for either).

Design docs (`docs/0–4.*.md`) untouched — WU1.2 is implementation of
design doc 3, not a design-level change.

## What's next

Per the run pointer (`manual-20260604T151127Z`), Track 1 still has
WU1.3 – WU1.9 ahead. Next obvious target is WU1.3 (RLS posture for the
tenancy tables — the second layer of defence-in-depth for client-
private state) or WU1.4 (subscription-scoped reads on the corpus
tables, the corresponding layer for corpus). Both depend only on what's
shipped today.

Branch protection on `main` requiring both CI lanes still needs
configuring in the GitHub UI — flagged across the last several
sessions and still outstanding (one-shot manual; not something a
workflow can do).
