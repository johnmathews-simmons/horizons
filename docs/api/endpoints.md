# Horizons API — Endpoint reference

*Auto-generated from the live FastAPI OpenAPI spec by
[`scripts/regen_endpoints_md.py`](../../packages/horizons-api/scripts/regen_endpoints_md.py).
Do not hand-edit — the pre-commit hook fails if this file drifts from
the generated output.*

Sibling docs:

- [`README.md`](README.md) — overview of the docs/api directory.
- [`getting-started.md`](getting-started.md) — auth flow for the
  upstream Lawstronaut API.
- [`auth.md`](auth.md) — Horizons login / refresh / logout posture.
- [`horizons-primitives.md`](horizons-primitives.md) — design-of-record
  for the three primitives (`/v1/discovery` / `/v1/temporal` /
  `/v1/differential`).
- [`lawstronaut-endpoints.md`](lawstronaut-endpoints.md) — upstream
  Lawstronaut v2 reference (separate API; Horizons consumes this).
- [`operational-notes.md`](operational-notes.md) — Lawstronaut
  refresh cadence, pricing, MCP, and other facts that shape design.

Conventions:

- All Horizons endpoints live under `/v1/...`. The `/openapi.json`
  spec at the API root is the source of truth.
- Per-user endpoints (`/v1/me/*`, `/v1/auth/*`) carry
  `Cache-Control: private, no-store`.
- The admin surface (`/v1/admin/*`) returns 403 (not 404) for
  authenticated non-admin callers — the documented exception to the
  "404 not 403" rule, because the prefix is explicitly administrative.
- Type column annotations: `T?` denotes a nullable field;
  `array<T>` denotes a JSON array of `T`; `T (uuid)` /
  `T (date-time)` reflect a JSON Schema `format`.

## Table of contents

- [admin](#admin)
- [auth](#auth)
- [differential](#differential)
- [discovery](#discovery)
- [health](#health)
- [me](#me)
- [temporal](#temporal)
- [watchlists](#watchlists)

## admin

### `GET /v1/admin/audit`

Search Admin Audit

Filtered, paginated reads of the admin audit log.

**Parameters**

| In | Name | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `query` | `action` | `AdminAccessMode?` | no | Restrict to 'operator' or 'impersonation' rows. |
| `query` | `admin_id` | `string (uuid)?` | no | Restrict to one admin's writes. |
| `query` | `limit` | `integer` | no | Page size cap. Silently clamped to 500. |
| `query` | `since` | `string (date-time)?` | no | Inclusive lower bound on granted_at; defaults to now - 24h. |
| `query` | `target_user_id` | `string (uuid)?` | no | Restrict to impersonation rows targeting this user id. |

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `AdminAuditResponse` | Successful Response |
| `422` | `HTTPValidationError` | Validation Error |


### `GET /v1/admin/health/api`

Api Health

Request rate / p95 / error rate over 1h and 24h.

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `ApiHealthResponse` | Successful Response |


### `GET /v1/admin/health/db`

Db Health

Connection count + replication lag + slow queries.

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `DbHealthResponse` | Successful Response |


### `GET /v1/admin/health/ingestion`

Ingestion Health

Overdue polls + recent incidents.

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `IngestionHealthResponse` | Successful Response |


### `GET /v1/admin/subscriptions`

List Subscriptions

List ``user_id``'s subscriptions and scope history.

Returns ``404`` if no such user exists. Returns an empty
``subscriptions`` list if the user exists but has none.

**Parameters**

| In | Name | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `query` | `user_id` | `string (uuid)` | yes | Target client user id |

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `SubscriptionsListResponse` | Successful Response |
| `422` | `HTTPValidationError` | Validation Error |


### `POST /v1/admin/subscriptions`

Create Subscription

Create a subscription for ``body.user_id``.

The target user must exist (404 otherwise). ``valid_from`` defaults
to ``now()`` UTC if omitted. The scope list must be non-empty
(Pydantic enforces); duplicate ``(jurisdiction, sector)`` pairs in
the request are deduplicated server-side before insert.

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `scopes` | `array<ScopePairBody>` | yes | — |
| `user_id` | `string (uuid)` | yes | — |
| `valid_from` | `string (date-time)?` | no | — |

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `201` | `SubscriptionOut` | Successful Response |
| `422` | `HTTPValidationError` | Validation Error |


### `PATCH /v1/admin/subscriptions/{subscription_id}`

Patch Subscription

Add and / or soft-delete scopes on ``subscription_id``.

Workflow:

1. Resolve the subscription; 404 if absent.
2. Reject UPDATEs touching no scopes (no-op PATCH is 422 — keeps the
   admin client honest about why they called us).
3. Detect overlap: a ``(jurisdiction, sector)`` cannot appear in
   both ``add_scopes`` and ``remove_scopes`` (422). Adds must not
   already exist as an active scope on this subscription (422).
   Removes must currently be active on this subscription (422).
4. Apply adds first, then removes (the order is irrelevant for
   correctness but happens to be how the trigger works — INSERT
   paths land before UPDATE paths).
5. Compute the user's post-reduction active scope set, derive the
   in-scope document ids, soft-hide every active watchlist for the
   user whose ``document_id`` is *not* in that set. The same logic
   runs even when only adds happened (cheap no-op: every active
   watchlist's document stays in scope).

**Parameters**

| In | Name | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `path` | `subscription_id` | `string (uuid)` | yes | — |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `add_scopes` | `array<ScopePairBody>` | no | — |
| `remove_scopes` | `array<ScopePairBody>` | no | — |

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `PatchSubscriptionResponse` | Successful Response |
| `422` | `HTTPValidationError` | Validation Error |


## auth

### `POST /v1/auth/login`

Login

Exchange email + password for an access / refresh token pair.

**Parameters**

| In | Name | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `header` | `x-client-type` | `string?` | no | — |

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string (email)` | yes | — |
| `password` | `string` | yes | — |

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `TokenPair` | Successful Response |
| `422` | `HTTPValidationError` | Validation Error |


### `POST /v1/auth/logout`

Logout

Revoke the active refresh token; clear the cookie when it was the source.

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `204` | `—` | Successful Response |


### `POST /v1/auth/refresh`

Refresh

Rotate the refresh token; mint a fresh access / refresh pair.

Liveness check + revoke happen here (not in the dep) so the dep
stays pure-crypto. A token whose ``jti`` is absent from
``refresh_tokens`` or already revoked is rejected with the uniform
401.

Two security points worth being explicit about:

1. Response shape is bound to the token *source* (cookie vs
   header), not to ``X-Client-Type``. See module docstring.
2. The caller's *current* role is re-read from ``users`` before
   issuing the new pair. Refresh is the boundary at which a role
   demotion (admin → client) or account removal takes effect; the
   stale claim in the refresh token is ignored. A missing user
   row returns 401 even though the refresh's signature was valid.

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `TokenPair` | Successful Response |


## differential

### `GET /v1/differential`

Differential

Before/after diff content for the scope.

`include_content` defaults true at document / clause scope, false
at corpus scope. At corpus scope, opting in with limit > 10
returns 422 to avoid multi-MB responses.

**Parameters**

| In | Name | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `query` | `clause_uid` | `string (uuid)?` | no | — |
| `query` | `cursor` | `string?` | no | — |
| `query` | `document_id` | `string (uuid)?` | no | — |
| `query` | `include_content` | `boolean?` | no | — |
| `query` | `jurisdiction` | `string?` | no | — |
| `query` | `limit` | `integer` | no | — |
| `query` | `scope` | `string` | no | — |
| `query` | `sector` | `string?` | no | — |
| `query` | `since` | `string (date-time)?` | no | — |
| `query` | `until` | `string (date-time)?` | no | — |

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `DifferentialPage` | Successful Response |
| `422` | `HTTPValidationError` | Validation Error |


### `GET /v1/differential/{event_id}`

Differential By Id

One change event by id, with before/after text by default.

A single bounded event — ``include_content`` defaults true (like
document and clause scope on the bulk endpoint). Out-of-scope rows
are invisible via RLS, so they map to 404 the same as truly absent
ids.

**Parameters**

| In | Name | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `path` | `event_id` | `integer` | yes | — |
| `query` | `include_content` | `boolean` | no | — |

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `DifferentialItem` | Successful Response |
| `422` | `HTTPValidationError` | Validation Error |


## discovery

### `GET /v1/discovery`

Discovery

Recent change events for the scope. No body text.

**Parameters**

| In | Name | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `query` | `clause_uid` | `string (uuid)?` | no | — |
| `query` | `cursor` | `string?` | no | — |
| `query` | `document_id` | `string (uuid)?` | no | — |
| `query` | `jurisdiction` | `string?` | no | — |
| `query` | `limit` | `integer` | no | — |
| `query` | `scope` | `string` | no | — |
| `query` | `sector` | `string?` | no | — |
| `query` | `since` | `string (date-time)?` | no | — |
| `query` | `until` | `string (date-time)?` | no | — |

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `DiscoveryPage` | Successful Response |
| `422` | `HTTPValidationError` | Validation Error |


## health

### `GET /healthz`

Healthz

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `object` | Successful Response |


## me

### `GET /v1/me`

Get Me

Return the user row + subscription summary for the verified bearer.

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `MeResponse` | Successful Response |


## temporal

### `GET /v1/temporal`

Temporal

When change events happened in the scope. No body text, no path.

**Parameters**

| In | Name | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `query` | `clause_uid` | `string (uuid)?` | no | — |
| `query` | `cursor` | `string?` | no | — |
| `query` | `document_id` | `string (uuid)?` | no | — |
| `query` | `jurisdiction` | `string?` | no | — |
| `query` | `limit` | `integer` | no | — |
| `query` | `scope` | `string` | no | — |
| `query` | `sector` | `string?` | no | — |
| `query` | `since` | `string (date-time)?` | no | — |
| `query` | `until` | `string (date-time)?` | no | — |

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `TemporalPage` | Successful Response |
| `422` | `HTTPValidationError` | Validation Error |


## watchlists

### `GET /v1/me/watchlists`

List Watchlists

Every watchlist the caller owns (RLS filters).

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `200` | `array<WatchlistResponse>` | Successful Response |


### `POST /v1/me/watchlists`

Create Watchlist

Add a watchlist for ``body.document_id``; validate scope first.

**Request body**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `document_id` | `string (uuid)` | yes | — |
| `name` | `string?` | no | — |

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `201` | `WatchlistResponse` | Successful Response |
| `422` | `HTTPValidationError` | Validation Error |


### `DELETE /v1/me/watchlists/{watchlist_id}`

Delete Watchlist

Remove one of the caller's watchlists, or 404.

**Parameters**

| In | Name | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `path` | `watchlist_id` | `string (uuid)` | yes | — |

**Responses**

| Status | Shape | Description |
| --- | --- | --- |
| `204` | `—` | Successful Response |
| `422` | `HTTPValidationError` | Validation Error |
