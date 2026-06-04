# Operational Notes for the Change-Watching Tool

Facts about how Lawstronaut runs that should shape how we build the watcher.

## Refresh cadence (from "List of Prices and Conditions" v2025.3)

> Lawstronaut aims to refresh legal and regulatory content **on a weekly basis where possible**. However, actual update frequency depends on several external factors and may vary by source portal.

- Some sources update daily or near-real-time; others update infrequently.
- Lawstronaut does **not** guarantee a fixed refresh interval.
- Factors that affect refresh: source publication patterns, technical access constraints, significance/scope of updates, security/auth/structure changes on the source site.
- A source can be temporarily delayed when its website changes layout/auth/structure and needs to be re-onboarded.
- Manual recrawl can be requested for specific sources, subject to feasibility.

**Implication for our tool**

- **Don't poll more often than ~daily per document.** Weekly is usually enough; for fast-moving sources, daily is the practical ceiling.
- Schedule should be staggered — don't fetch all documents at once.
- The most efficient signal for "anything changed?" is **a new `version` appearing for a known `document_id`** — usable by filtering `/contents` on `document_id` and `last_updated` / `crawling_date`.

## No documented rate limits

The portal documentation does not publish rate limits. Treat conservatively:

- Backoff on `429` (if returned) and on `5xx`.
- Use `limit`/`offset` pagination with reasonable page sizes (~20–100).
- Cache the bearer token; refresh proactively before `expires_in` (currently 1800s) elapses.

## Hosting and data residency

- API hosted on AWS in **Frankfurt** (`eu-central-1`).
- Source files (`/content/{id}/file-url`) are served from an S3 bucket `lawstronaut-files.s3.eu-central-1.amazonaws.com`.
- Signed URL example shows `X-Amz-Expires=3600` — 1 hour.

## Commercial / access notes

- Access is subject to a non-disclosure agreement and an Order Confirmation between customer and Lawstronaut.
- Pricing model: **USD 1,000 per year per jurisdiction**, with a **USD 5,000 per year minimum**; or **USD 10,000 per year unlimited** per Developer's Client. Eligible for volume + prepaid-payment discounts. Prepayment based on good-faith forecast in year 1; based on prior-year actuals in subsequent years.
- Fast-track research is available: **USD 2,000** for one jurisdiction in 2 weeks, or **USD 500** for one portal in 3 working days.
- Quarterly reporting is required (within 10 business days of each quarter end): Developer Client name, subscription type, jurisdictions, dates.
- Annual list-price adjustments take effect on **1 February**.

These don't shape the *code*, but they shape the **scope of which jurisdictions and portals we can pull in the demo** without escalating cost.

## MCP

The portal references a Model Context Protocol page at `https://lawstronaut.com/mcp` (external — not gated by the dev portal login). If we want an MCP-flavoured integration later, that's where to start.

## Spec discovery (do this once we have a token)

These URLs returned **401** (not 404) when probed unauthenticated:

- `https://api.lawstronaut.com/v2/openapi.json`
- `https://api.lawstronaut.com/v2/swagger.json`
- `https://api.lawstronaut.com/v2/docs`

So the OpenAPI spec is very likely live at one of those, requires a bearer token, and is worth fetching for codegen / schema validation.

## Doc inconsistencies — confirmed against live API on 2026-06-04

These were verified by calling the live API (using a bearer token from the dev portal home page).

**Real-vs-docs differences found:**

- `/v2/contents/markdown` returns the markdown under **`content_markdown`**, not `markdown` as the docs say. The first row's `keys` are exactly `["document_id", "content_markdown"]`.
- In `/v2/contents/markdown`, `document_id` comes back as a **string** (e.g. `"34659134"`), while in `/v2/contents` it is a **number**. Treat as a string everywhere to be safe.
- `/v2/contents/markdown` does **not** accept `document_id` as a query parameter — `?document_id=X` returns **400**. The "path parameter" form in the docs (`/contents/markdown/{document_id}`) also wasn't tested as working; the only confirmed way to get markdown for a specific document is to filter via `iso`/`portal` etc. and paginate.
- `/v2/content/{document_id}` without a version returns **403 "Access denied"**, not 404.
- `/v2/content/{document_id}/{version}` returns **200 with an empty `{ "data": {} }`** for the recent documents I tried (e.g. `27732019/1`, `34659134/1`). Likely needs an exact version that actually exists; first version is not necessarily `1`. **Open question** — we'll need to figure this out before relying on it for change detection.
- `publication_date` values come back as `"2026-06-03T00:00:000Z"` — note the **three zeros** in the milliseconds field (invalid ISO 8601). Parsers must tolerate this.
- `/v2/portals` response includes `name_en`, a populated `portal_tags` array, and `total_links` is a **string** (e.g. `"26888"`). The docs example showed `total_links` as a number.
- `/v2/authority-types?iso=IE` returns 369 distinct values — many are highly specific (`"REDIII Amendment - Private Development"`, `"Treoir"`, etc.). Use the live list, not assumptions, when filtering on `authority_type`.

**Other doc bugs (still suspected, not all retested):**

- Getting Started's example call: `https://filerskeepersapi.co/v3/jurisdictions` — likely a typo; all other docs use `api.lawstronaut.com/v2`.
- Refresh-token URL in docs: `https://filerskeepersapi.co//auth/refresh-token` (double slash) — likely a typo.
- `/v2/domain/{domain_id}/subdomains` response example shows `data` nested twice (`data[0].data[]`); other list endpoints don't. Confirm shape on first real call.

## Clause structure (verified for IE statutes)

A real fetch of an Irish Statute Book Act (`27732019` — "Protection of Employees (Employers' Insolvency) (Amendment) Act 2026", 40.9 KB of markdown) shows the consistent clause hierarchy we will parse:

```
**PART N**
**Heading of section**
**N\.** (N\) <sub-section text>
        (a) <paragraph>
        (b) <paragraph>
            (i) <sub-paragraph>
            (ii) <sub-paragraph>
```

Markers in the markdown:

- `**PART N**` — top-level part divider.
- Bold heading lines (`**...**`) — descriptive section/clause titles.
- `**N\.**` — numbered section (backslash-escaped period, because the period would otherwise be lost in markdown rendering).
- `(N\)` — sub-section number.
- `(a)`, `(b)`, `(c)` — paragraph letters.
- `(i)`, `(ii)`, `(iii)` — sub-paragraph roman numerals.
- Inline links use absolute URLs to `irishstatutebook.ie` — useful for cross-reference detection, but we'll need to normalise them for diffing.

This is the basis for our clause-anchored IDs (e.g. `act/27732019/v1/part-1/sec-1/sub-2`).
