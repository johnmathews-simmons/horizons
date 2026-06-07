# Horizons & Lawstronaut API references

*Last revised: 2026-06-07.*
*Path: docs/api/README.md.*

Two distinct surfaces live in this directory:

1. **Horizons public API** — the FastAPI service we ship (the
   `/v1/...` prefix). Customers and the Vue webapp talk to this.
2. **Lawstronaut API** — the third-party upstream we consume during
   ingestion. We watch it; we do not proxy it.

## 1. Files

**Horizons surface:**

- `endpoints.md` — auto-generated from the live FastAPI OpenAPI by
  [`scripts/regen_endpoints_md.py`](../../packages/horizons-api/scripts/regen_endpoints_md.py).
  Source of truth for the wire shape. Do not hand-edit.
- `horizons-primitives.md` — design-of-record for the three primitives
  (`/v1/discovery`, `/v1/temporal`, `/v1/differential`) — scope
  discriminator, opaque-cursor pagination, `include_content` rules.
- `auth.md` — Horizons login / refresh / logout posture and cookie
  semantics.

**Lawstronaut upstream (captured 2026-06-04 from the dev portal):**

- `getting-started.md` — auth flow (custom OAuth 2.0 / Bearer token),
  base URLs, HTTP status codes.
- `concepts.md` — domain model (jurisdiction, portal, taxonomy,
  document, version).
- `lawstronaut-endpoints.md` — every v2 endpoint: path, params,
  example request/response, notes.
- `operational-notes.md` — refresh cadence, deployment, pricing, MCP,
  and other facts that shape tool design.

## 2. Quick facts

- **API base URL:** `https://api.lawstronaut.com/v2`
- **Auth base URL:** `https://filerskeepersapi.co` (login + refresh-token live on the filerskeepers host, not on `api.lawstronaut.com`)
- **Auth scheme:** Bearer token (custom OAuth 2.0 flow — login returns a `refresh_token` that is used as the bearer; refresh via a separate endpoint when it expires)
- **Token lifetime:** `expires_in` in seconds (login example returns `1800` = 30 minutes)
- **Hosting:** AWS Frankfurt
- **Content refresh target:** ~weekly per source portal, but varies (some daily, some slower). **Not real-time.**
- **Versioning:** Each legal document has stable `document_id` + incrementing `version`. New versions are created on amendment, consolidation, or material change. This is the hook we use for change detection.

## 3. OpenAPI fetch — still pending

Token access is solved: the dev portal at `https://dev-portal.filerskeepersapi.co/dashboard/lawstronaut/home` displays a 30-min JWT for ad-hoc calls, and `scripts/fetch_fixtures.py` runs the full login + refresh flow against the live API.

What's still outstanding: fetching the OpenAPI spec itself. The portal returns **401** (not 404) for these paths even with a valid token attached during fixture runs — they may require a different scope or a different auth header shape:

- `https://api.lawstronaut.com/v2/openapi.json`
- `https://api.lawstronaut.com/v2/swagger.json`
- `https://api.lawstronaut.com/v2/docs`

When one of them does respond, save the result alongside this reference as `openapi.json` so we get a machine-readable schema for codegen and validation.

## 4. Tool scope

- Change detection operates at the **clause level**, not the document level.
- Legal documents have structure (Part / Chapter / Article / sub-article / sub-clause); a new `version` typically modifies only a few clauses.
- `/contents/markdown` is the preferred feed — markdown preserves the structural anchors that act as clause boundaries.
- We'll need a clause-aware parser that assigns stable identifiers (heading-anchored, not positional) so a clause survives reordering/insertion across versions.
- We'll need to keep prior parsed versions locally (or just their clause trees) to diff against.

## 5. Discrepancies / questions

[`operational-notes.md`](./operational-notes.md) is the canonical list — it covers field-name mismatches (`content_markdown` vs `markdown`), `document_id` string/number polymorphism, the `/v2/contents/markdown?document_id=X` → 400 and `/v2/content/{id}` → 403 quirks, the malformed-millis `publication_date` format, the silent `language=English` 400, and the `/v2/portals` enumeration gotcha.

A few items live only here because they affect URL choice rather than response parsing:

- The Getting Started page shows an example call against `https://filerskeepersapi.co/v3/jurisdictions`. Every other doc references `v2` on `api.lawstronaut.com`. Treat the `v3` mention as a documentation typo; use `api.lawstronaut.com/v2`.
- The refresh-token endpoint URL in the docs has a double slash: `https://filerskeepersapi.co//auth/refresh-token`. Treat as a typo; use single slash.
- `/v2/search` is in testing and **only available for `iso=IE`** (Ireland) at this time.
