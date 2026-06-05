# Per-document poll transaction

The body that the WU3.3 claim loop invokes for every claimed schedule
row. Replaces `noop_poll`. Implements the design-doc-4 §"Ingestion
service" flow: fetch markdown via the WU3.2 Lawstronaut client, hash,
extend the live version's `valid_to` if unchanged, otherwise upload
the blob and write the new version + clauses + alignment +
change-events tuple in one transaction.

The blob upload is **outside** the Postgres transaction (the two
substrates can't share a transaction). The blob lands first under a
content-addressed key (`originals/<sha256>.md`); the version row
references that key only after the upload completes. Failed runs
leave at most one orphan blob, reclaimed by [`sweep.py`](sweep.md).

## Surface

```python
# horizons_ingestion/poll/poll.py
async def poll_document(
    conn: PoolConnection,
    document_id: uuid.UUID,
    *,
    client: LawstronautClient,
    blob_store: BlobStore,
    blob_container: str,
    tuning: TuningConfig | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None: ...
```

The seam in WU3.3 expects `Callable[[PoolConnection, UUID],
Awaitable[None]]`. The extra dependencies (`client`, `blob_store`,
`blob_container`, `tuning`, `clock`) are bound at startup by
`__main__.py` via `functools.partial` or a closure. `clock` is
injectable so tests don't need to freeze wall time;
`datetime.now(UTC)` is the default.

`tuning` defaults to `default_tuning_config()`. The alignment pipeline
reads `shingle_k`, `signature_size`, `lsh_bands`,
`similarity_threshold` from it; tests pass overrides when needed.

## Flow

1. **Fetch.** `doc = await client.get_markdown(document_id)`. If
   `None` (empty-`data` case), log and return. The schedule row gets
   bumped by WU3.3's outer `MARK_OK_SQL`; no parking. The caller
   (`tick`) wraps the call in `try/except LawstronautError` and treats
   any client error as a poll failure (`failure_count += 1`); we don't
   catch here — we let it bubble.
2. **Hash.** `sha = sha256(doc.markdown.encode("utf-8")).digest()`.
   Stored on `document_versions.content_sha256` as a 32-byte bytea
   (the schema CHECK).
3. **Look up the live version.** `SELECT id, content_sha256,
   version_no FROM document_versions WHERE document_id = $1 AND
   valid_to IS NULL ORDER BY version_no DESC LIMIT 1`. The
   `idx_document_versions_doc_valid_to` index satisfies this without a
   sort; the `valid_to IS NULL` filter narrows to the *current* live
   row when the worker is up to date and to zero rows on the very
   first poll for a never-seen document.
4. **Unchanged path.** If `live is not None and live.content_sha256
   == sha`: `UPDATE document_versions SET valid_to = $now WHERE id =
   $live.id`. One statement. Done. (The append-only trigger permits
   `valid_to`-only updates per migration 0007.)
5. **Changed path.**
   a. `await blob_store.put(sha.hex() + ".md", doc.markdown.encode())`.
      Idempotent: the impl no-ops if the blob already exists.
   b. Look up the document's `jurisdiction` / `sector` (so we can
      denormalise onto `change_events`) and the previous version's
      blob key (for re-parsing if needed). Single round trip:
      `SELECT jurisdiction, sector FROM documents WHERE id = $1`.
   c. Parse the new markdown into a clause tree via
      `horizons_core.core.alignment.parse(doc.markdown)`. The portal
      slug is not yet known (WU3.5 will widen the schedule to carry
      it); the default config covers our 31-fixture spread for now.
   d. Load the previous version's clauses from the `clauses` table
      (if any) and reconstruct a clause tree, or, on the very first
      poll, treat the predecessor as empty. We can't reuse
      `parse(previous_markdown)` without re-fetching the previous
      blob, so we rebuild a flat-list `Clause` tree from the stored
      rows: each row becomes a leaf with its stored `clause_path`,
      `clause_uid`, and `text_content`. The aligner walks
      pre-order; a flat list of leaves is a valid (if depth-1)
      substrate for it.
   e. Run `align(prev_tree, new_tree, tuning=tuning)` → `list[ChangeEvent]`.
   f. **Materialise clause UIDs.** The aligner returns pairings by
      position; UID assignment happens here. Walk the new clause tree
      and for each clause: if it was paired with a before-clause, take
      that before-clause's stored `clause_uid`; otherwise mint a fresh
      `uuid4()`. Carry the mapping into `change_events.after_clause_uid`
      and `before_clause_uid` simultaneously.
   g. Open a savepoint (or just sequence the writes — we're already
      inside WU3.3's tick transaction) and INSERT:
      - The new `document_versions` row with `valid_from = $now`,
        `valid_to = NULL`, `version_no = previous + 1` (or `1` if no
        predecessor), and the blob coordinates.
      - The new `clauses` rows (one per leaf in the parsed tree).
      - The `change_events` rows (one per `ChangeEvent`).
      - If a predecessor exists, `UPDATE document_versions SET
        valid_to = $now WHERE id = $prev.id`.
   h. WU3.3's tick `COMMIT` makes the whole thing atomic.
6. **Exception in the changed path.** The blob may have been uploaded
   already (orphan — `sweep.py` reclaims it on its next pass). DB
   writes roll back via WU3.3's tick-level transaction. The schedule
   row's `failure_count` is bumped by WU3.3's own error path.

## Version label

The `document_versions.version_label` column is set to `f"v{version_no}"` —
derived from our own monotonic `version_no`, not from Lawstronaut's
`MarkdownDocument.version` field. Two reasons: (1) `version_no` is the
counter we control and increments by construction, so the
`UNIQUE(document_id, version_label)` constraint can never fire spuriously;
(2) Lawstronaut's `version` is the upstream version stamp and may repeat
across changed polls (the docs don't promise it will vary). The upstream
value is still available on `MarkdownDocument.version` for forensics; it
just doesn't drive a database constraint.

## What goes to blob storage vs Postgres

- **Blob**: the original markdown bytes, content-addressed by
  `<sha256>.md` under the container (default `originals`). Treated as
  immutable. No mutation API; the sweep deletes orphans.
- **Postgres**: every queryable artefact. The version row carries the
  hash and byte count for integrity checks; clause text lives inline
  in `clauses.text_content` for diffing; change events carry inline
  before/after text for the differential primitive's payload.

## Hash storage shape

The schema declares `content_sha256` as `bytea` with `CHECK
(octet_length(...) = 32)` (migration 0003). We store the **raw 32-byte
digest**, not the hex string. The blob key uses the hex form
(`<sha256>.md`) because Azure Blob keys are textual. Tests assert
both shapes.

## Lawstronaut → `effective_date` provenance

`MarkdownDocument.publication_date` populates
`document_versions.publication_date` verbatim. Effective-date
inference (`publication + per_jurisdiction_default_lag`) is a future
unit's job; today `effective_date = publication_date` as a placeholder
when publication is known, else `None`. `change_events.effective_date`
inherits the same value from the new version row. Doc 3 §Principles 3
is the canonical spec; this unit stages the column, doesn't compute
the lag.

## Clause-UID identity rule

A paired clause inherits the before-clause's UID — that's the whole
point of `clause_uid`. An unpaired after-side clause gets a fresh
`uuid4()`. The mapping is built once per poll, in pre-order over the
new tree, so siblings get UIDs in a deterministic order across reruns
with the same input (modulo the `uuid4` source).

## Trade-offs accepted

- **Lock-hold during HTTP + parse.** WU3.3's tick holds the
  SKIP-LOCKED row lock for the entire poll body. At one replica and
  small batch sizes the lost concurrency is negligible. WU3.3's
  journal calls this out as the next-natural refactor if demo-time
  measurements show it.
- **Re-parsing the predecessor from `clauses` rows, not from its
  blob.** Two reasons: a blob fetch costs a network round trip and
  pulling the prior tree from rows we already own is cheaper; and the
  parser's exact output for the predecessor is what landed in
  `clauses` originally, so reconstructing from rows preserves
  whatever parser-config was in force at ingestion time.
- **No portal-aware parsing yet.** `parse(doc.markdown)` uses the
  default config; per-portal configs land when WU3.5 widens the
  schedule to carry portal slug.
- **Hash before fetch is wasteful.** We hash the markdown the API
  returned every poll. The alternative — sending `If-None-Match` with
  the previous version's ETag-equivalent — depends on Lawstronaut
  exposing one and is out of scope for the demo path.

## Tests

Unit tests (no Docker required), in
`packages/horizons-ingestion/tests/test_poll.py`:
- Each path (`unchanged`, `changed`, `fetch returns None`, `blob put
  fails`, `alignment raises`) drives the body via a stub
  `LawstronautClient` and an in-memory `BlobStore`. The DB layer is
  abstracted by a tiny `FakeConn` that records the SQL it was asked to
  execute — enough to assert the call shape without spinning Postgres.

Integration tests (testcontainers Postgres, marked `integration`), in
`tests/integration/test_poll_document.py`:
- Bootstrap a `documents` row + a schedule row, stub the client,
  invoke `poll_document` via WU3.3's `ClaimLoop`, and assert the full
  four-row tuple (`document_versions`, `clauses`, `change_events`,
  predecessor `valid_to`) committed atomically.
- The unchanged path inserts no new rows and extends `valid_to`.
- A failing alignment rolls back DB writes; the orphan blob remains
  for the sweep.
- A multi-version sequence (v1 → v2 → v3) round-trips clause-UID
  identity: a clause unchanged across all three versions carries the
  same UID end to end.
