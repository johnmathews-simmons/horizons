# Lawstronaut API — Overview

*Path: docs/lawstronauts-api-overview.md. Written 2026-06-09.*

A short orientation to the Lawstronaut API, written up from a worked example:
fetching the Dutch GDPR implementing law. For the authoritative endpoint
reference see `docs/api/lawstronaut-endpoints.md`; for auth see
`docs/api/getting-started.md`.

## How the API is structured

Two hosts. Auth lives on `filerskeepersapi.co`; data lives on
`api.lawstronaut.com/v2`.

- **Auth** — `POST https://filerskeepersapi.co/auth/login` with
  `{email, password}` returns a token in a field named `refresh_token` that is
  *itself* the bearer passed on every API call (`Authorization: Bearer <token>`).
  TTL is 30 min (`expires_in` seconds); refresh on 401. The dev portal also
  shows a copy-pasteable JWT for one-off testing.
- **Data** — every documented endpoint is a **GET**. Three layers:
  1. **Taxonomy / availability** — `/jurisdictions`, `/domains`, `/categories`,
     `/portals`, `/issuing-authority`, etc. Use these to discover what filter
     values exist.
  2. **Metadata** — `/contents` is the primary endpoint. It returns *metadata
     records only* (title, dates, authority type, portal, `document_id`,
     `version`, repeal status). Filters are AND-combined: `iso`, `portal`,
     `title` (partial match), `authority_type`, `publication_date`,
     `effective_date`, `repealed`, etc. Paginate with `limit` / `offset`; the
     response carries `pagination.total_count`.
  3. **Content** — `/contents/markdown`, `/contents/full-text`, and
     `/content/{id}/{version}` return the actual document text. Markdown is the
     preferred substrate because it preserves the heading/section structure we
     use as clause boundaries.

Key identity fields: `document_id` is stable across amendments; `version`
increments when the document changes. The same `document_id` can appear at
multiple versions.

## How I found the Dutch GDPR law

The goal was the Netherlands' national GDPR implementing statute — the
*Uitvoeringswet Algemene verordening gegevensbescherming* (UAVG).

1. **Naive search returns noise.** `/contents?iso=NL&title=...gegevensbescherming`
   returns 135–838 records, but nearly all are `Kamerstuk`, `Moties`,
   `Amendementen`, `Beslisnota` — the *parliamentary paper trail about* the law,
   sourced from `officielebekendmakingen.nl` and `tweedekamer.nl`. None of these
   is the statute itself.
2. **Filter by portal to get the law as in force.** The consolidated statute
   lives on the `wetten.overheid.nl` portal with
   `type_of_authority = "Wetten"`. Adding `portal=wetten.overheid.nl` to the
   query narrowed 135 records down to **exactly one**:
   `document_id 3925273`, *Uitvoeringswet Algemene verordening
   gegevensbescherming*, effective 2021-07-01, not repealed
   (`BWBR0040940`).
3. **Pull the text.** Re-run the same filter against `/contents/markdown` to get
   the ~89 KB markdown body (saved as `data/samples/nl-3925273-v1.md`).

**Takeaway for the change-watcher:** to retrieve "the law as currently in
force" rather than legislative chatter, constrain to the official consolidation
portal for the jurisdiction (`wetten.overheid.nl` for NL) and/or
`type_of_authority=Wetten`. Title-only search across all portals conflates the
statute with everything written about it.

## National implementing law vs. the EU regulation

What we fetched (UAVG) is the **Dutch national law that implements and
supplements** the GDPR — it sets national choices (e.g. the age of consent,
the supervisory authority, journalistic exceptions). Its Article 1 explicitly
defines *verordening* as **EU Regulation 2016/679** — the GDPR itself.

The **GDPR regulation text** is a separate document: an EU instrument, directly
applicable in every member state, retrievable under `iso=EU` rather than
`iso=NL`. So "the GDPR for the Netherlands" is really two distinct records — the
EU regulation (`iso=EU`, 2016/679) and the national implementing act (`iso=NL`,
UAVG). Pick based on whether you want the directly-applicable EU text or the
member-state-specific implementation.
