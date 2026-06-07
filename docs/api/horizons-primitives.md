# Horizons public API — the three primitives

*Last revised: 2026-06-06.*
*Path: docs/api/horizons-primitives.md.*

*WU4.4. Doc-first; the OpenAPI-generated reference lands with WU4.6.*

The Horizons public API exposes the three primitives from
[`docs/RFC-1 product-questions.md`](../1.%20product-questions.md) as three
HTTP endpoints under `/v1/`. Each accepts the same **scope discriminator**;
the response shape differs by primitive.

| Primitive | Path | Returns |
|---|---|---|
| Discovery | `GET /v1/discovery` | identities + change locations (no body text at corpus scope by default) |
| Temporal | `GET /v1/temporal` | change-event timestamps |
| Differential | `GET /v1/differential` | before/after clause text |

All three require an authenticated bearer (`Authorization: Bearer <access_token>`)
and carry `Cache-Control: private, no-store`. RLS narrows visible
`change_events` to rows whose `(jurisdiction, sector)` is in the
caller's subscription scope — out-of-scope rows are silently invisible,
not 403.

## Scope discriminator

Every endpoint takes a `scope` query parameter, with scope-specific
filter parameters validated as a discriminated union:

```
?scope=corpus       [&jurisdiction=...&sector=...&since=...&until=...]
?scope=document      &document_id=<uuid>
?scope=clause        &clause_uid=<uuid>          [&document_id=<uuid>]
```

- **`scope=corpus`** — every change event the caller's subscription
  covers. Optional filters narrow further. `since` / `until` are
  ISO-8601 timestamps on `detected_at`. Subscription scope is enforced
  by RLS regardless of `jurisdiction` / `sector` query values — passing
  a jurisdiction the caller is not subscribed to returns an empty page,
  not 403.
- **`scope=document`** — all change events on one document.
  `document_id` is the Horizons UUID, not the upstream
  `lawstronaut_document_id`.
- **`scope=clause`** — all change events touching one `clause_uid` (the
  cross-version stable identity from
  [doc 2](../2.%20clause-alignment.md)). Optional `document_id` narrows
  to one document if the same `clause_uid` somehow appears in two
  (defensive — `clause_uid` is cross-version-stable but not
  cross-document-unique by construction).

Invalid combinations (e.g. `scope=document` without `document_id`)
return `422` with the Pydantic discriminated-union error body.

## Pagination — opaque keyset cursor

Corpus-scope responses are paginated by an opaque `cursor` field.
Document-scope and clause-scope responses are not paginated — the
result set per document or per clause is bounded by how often that
target has changed (small in practice).

Request:

```
GET /v1/discovery?scope=corpus&limit=50
```

Response envelope:

```json
{
  "items": [ ... ],
  "next_cursor": "eyJkdCI6IjIwMjYtMDYtMDRUMTI6MDA6MDBaIiwiaWQiOjQ4Mjd9",
  "has_more": true
}
```

The client passes `next_cursor` back verbatim:

```
GET /v1/discovery?scope=corpus&cursor=eyJkdCI6...
```

The cursor encodes the `(detected_at, id)` of the last row returned,
keyed on the composite index
`idx_change_events_scope(jurisdiction, sector, detected_at, effective_date)`.
The encoding is base64(JSON) and is **opaque** — clients must not
parse, generate, or compare cursors. `limit` defaults to 50 and is
capped at 200.

When the page is the last one, `next_cursor` is omitted and
`has_more` is `false`.

## `include_content` — body text guard

Differential responses include `before_text` and `after_text` only when
`include_content=true`. The default depends on scope, because the
asymmetry of cost is large:

| Scope | Default | Notes |
|---|---|---|
| `corpus` | `false` | `include_content=true` is **rejected with 422 when `limit > 10`** — a 50-event corpus differential can be MBs and is a footgun. |
| `document` | `true` | A document differential is bounded by the document's clause count. |
| `clause` | `true` | A clause differential is at most a handful of events. |

Discovery and Temporal never return body text — the parameter is
ignored if passed.

## Response shapes

### Discovery

```json
{
  "id": 4827,
  "document_id": "0192f30c-...",
  "document_version_id": "0192f30d-...",
  "jurisdiction": "IE",
  "sector": "BANKING",
  "change_type": "MODIFIED",
  "before_clause_uid": "0192...",
  "after_clause_uid":  "0192...",
  "before_path": "Part 2 / Section 4 / (a)",
  "after_path":  "Part 2 / Section 4 / (a)",
  "alignment_confidence": 0.92,
  "detected_at": "2026-06-04T12:00:00Z",
  "effective_date": "2026-09-01T00:00:00Z"
}
```

No `*_text` fields — discovery is the cheap polling primitive.

### Temporal

```json
{
  "id": 4827,
  "document_id": "0192...",
  "document_version_id": "0192...",
  "clause_uid": "0192...",
  "change_type": "MODIFIED",
  "detected_at": "2026-06-04T12:00:00Z",
  "effective_date": "2026-09-01T00:00:00Z"
}
```

`clause_uid` is the *after* uid for `ADDED` / `MODIFIED` / `MOVED`, the
*before* uid for `REMOVED`. The document scope returns all events for
the document; the clause scope returns the event history for one uid.

### Differential

Same shape as Discovery plus `before_text` and `after_text` when
`include_content` resolves to `true`. The `*_text` null rules follow
the `change_type`:

| change_type | `before_text` | `after_text` |
|---|---|---|
| `ADDED` | null | non-null |
| `REMOVED` | non-null | null |
| `MODIFIED` | non-null, different from `after_text` | non-null |
| `MOVED` | non-null, same as `after_text` | non-null |

## Single-event lookup — `GET /v1/differential/{event_id}`

A bounded, single-row variant of differential for the webapp's
clause-diff detail view (WU5.3). Same response shape as a
`DifferentialItem` (not a `*Page`). `include_content` defaults `true`
— one event is a bounded payload, no corpus-style payload guard
applies. Pass `?include_content=false` to omit body text.

Out-of-scope rows are invisible via RLS; the route returns `404` for
both truly-absent ids and out-of-scope ids — the caller cannot tell
the difference. `Cache-Control: private, no-store`.

## Errors

Standard FastAPI shape — `{"detail": "..."}`:

- `401` — missing / invalid / expired / wrong-kind bearer.
- `422` — invalid scope discriminator, invalid cursor, or
  `include_content=true` at corpus scope with `limit > 10`.
- `404` — `/v1/differential/{event_id}` only, for absent or
  out-of-scope ids (indistinguishable by design). The list endpoints
  never return 404: out-of-scope rows are silently absent from the
  page.

## Performance budget

Doc 3 §"Performance target" sets 3 s p95 for corpus-scope queries
against the seeded curated set. The composite index
`idx_change_events_scope(jurisdiction, sector, detected_at, effective_date)`
is the hot path; the keyset cursor preserves index order without an
ORDER BY sort. WU4.4 ships an inline integration test that asserts
this budget against the WU3.5 seed.

## `GET /v1/me/overview` — home dashboard summary

The home dashboard's data source. Returns the full corpus matrix
(every `(jurisdiction, sector)` pair present in `documents`, with
counts) plus a `subscribed` flag per jurisdiction and per sector
indicating whether the caller's subscription covers it.

Admin callers see every pair flagged `subscribed=true`; the body
also sets `is_admin=true`. The route reads corpus shape through the
unscoped `app_public.corpus_shape()` function (see migration 0013
and `db/roles.md`); per-row corpus content remains RLS-scoped on
every other route.

Response:

```json
{
  "is_admin": false,
  "totals": {
    "documents": 10,
    "jurisdictions": 8,
    "sectors": 5,
    "subscribed_jurisdictions": 1,
    "subscribed_sectors": 1
  },
  "jurisdictions": [
    { "code": "IE", "document_count": 1, "subscribed": false },
    { "code": "UK", "document_count": 1, "subscribed": true }
  ],
  "sectors": [
    { "code": "BANKING", "document_count": 5, "subscribed": true },
    { "code": "employment", "document_count": 2, "subscribed": false }
  ]
}
```

Lists are sorted by `code` ascending. `Cache-Control: private, no-store`.

Why this isn't `/v1/me`: keeping the dashboard view separate from the
identity payload means the home page can stale-cache the overview
independently of the principal, and `/v1/me` stays small for clients
that only need user identity.
