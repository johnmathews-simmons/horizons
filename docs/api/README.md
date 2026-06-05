# Lawstronaut API — Local Reference

This directory captures the Lawstronaut API (v2) as documented in the developer portal at
`https://dev-portal.filerskeepersapi.co/dashboard/lawstronaut`. It is the working reference
for designing the change-watching tool.

**Source captured:** 2026-06-04 (from the authenticated developer portal — API Docs, Endpoints, Getting Started, and List of Prices pages).

## Files

- `getting-started.md` — auth flow (custom OAuth 2.0 / Bearer token), base URLs, HTTP status codes.
- `concepts.md` — domain model (jurisdiction, portal, taxonomy, document, version).
- `endpoints.md` — every v2 endpoint: path, params, example request/response, notes.
- `operational-notes.md` — refresh cadence, deployment, pricing, MCP, and other facts that shape tool design.

**Horizons public API** (separate from upstream — we *expose*, we don't proxy):

- `horizons-primitives.md` — the three primitives (`/v1/discovery`, `/v1/temporal`, `/v1/differential`) at corpus / document / clause scope, scope discriminator, opaque-cursor pagination, `include_content` rules. WU4.6 will publish an OpenAPI-generated reference; this is the design-of-record until then.

## Quick facts

- **API base URL:** `https://api.lawstronaut.com/v2`
- **Auth base URL:** `https://filerskeepersapi.co` (login + refresh-token live on the filerskeepers host, not on `api.lawstronaut.com`)
- **Auth scheme:** Bearer token (custom OAuth 2.0 flow — login returns a `refresh_token` that is used as the bearer; refresh via a separate endpoint when it expires)
- **Token lifetime:** `expires_in` in seconds (login example returns `1800` = 30 minutes)
- **Hosting:** AWS Frankfurt
- **Content refresh target:** ~weekly per source portal, but varies (some daily, some slower). **Not real-time.**
- **Versioning:** Each legal document has stable `document_id` + incrementing `version`. New versions are created on amendment, consolidation, or material change. This is the hook we use for change detection.

## To-do once we have an API token

The portal returns **401** (not 404) for:
- `https://api.lawstronaut.com/v2/openapi.json`
- `https://api.lawstronaut.com/v2/swagger.json`
- `https://api.lawstronaut.com/v2/docs`

Once we have a token, fetch the real OpenAPI spec from one of those and save it alongside this reference as `openapi.json`. That gives us a machine-readable schema for codegen and validation.

## Tool scope (carried forward from user)

- Change detection operates at the **clause level**, not the document level.
- Legal documents have structure (Part / Chapter / Article / sub-article / sub-clause); a new `version` typically modifies only a few clauses.
- `/contents/markdown` is the preferred feed — markdown preserves the structural anchors that act as clause boundaries.
- We'll need a clause-aware parser that assigns stable identifiers (heading-anchored, not positional) so a clause survives reordering/insertion across versions.
- We'll need to keep prior parsed versions locally (or just their clause trees) to diff against.

## Discrepancies / questions

- The Getting Started page shows an example call against `https://filerskeepersapi.co/v3/jurisdictions`. Every other doc references `v2` on `api.lawstronaut.com`. Treat the `v3` mention as a documentation typo until confirmed; use `api.lawstronaut.com/v2`.
- The refresh-token endpoint URL in the docs has a double slash: `https://filerskeepersapi.co//auth/refresh-token`. Treat as a typo; use single slash.
- `/v2/search` is in testing and **only available for `iso=IE`** (Ireland) at this time.
