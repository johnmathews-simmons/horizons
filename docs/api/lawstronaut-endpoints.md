# Lawstronaut v2 — Endpoint Reference

*Last revised: 2026-06-05.*
*Path: docs/api/lawstronaut-endpoints.md.*

All endpoints:
- Base URL: `https://api.lawstronaut.com/v2`
- Auth: `Authorization: Bearer <token>` (see `getting-started.md`)
- Accept: `application/json`
- Method: **GET** for every documented endpoint (login / refresh-token are POST and live on `filerskeepersapi.co`)

Pagination convention (where supported): query params `limit` (default ~20) and `offset`. Response includes a `pagination` object: `{ total_count, limit, offset }`.

---

## Index

### Part 1 — Database structure and availability

1. [GET `/jurisdictions`](#1-get-v2jurisdictions)
2. [GET `/domains`](#2-get-v2domains)
3. [GET `/domain/{domain_id}/subdomains`](#3-get-v2domaindomain_idsubdomains)
4. [GET `/categories`](#4-get-v2categories)
5. [GET `/categories/{category_id}/subcategories`](#5-get-v2categoriescategory_idsubcategories)
6. [GET `/subcategories/{subcategory_id}/law-types`](#6-get-v2subcategoriessubcategory_idlaw-types)
7. [GET `/portals`](#7-get-v2portals)
8. [GET `/authority-types`](#8-get-v2authority-types)
9. [GET `/issuing-authority`](#9-get-v2issuing-authority)

### Part 2 — Content

10. [GET `/contents`](#10-get-v2contents)
11. [GET `/contents/full-text`](#11-get-v2contentsfull-text)
12. [GET `/contents/markdown`](#12-get-v2contentsmarkdown)
13. [GET `/content/{document_id}/file-url`](#13-get-v2contentdocument_idfile-url)
14. [GET `/search`](#14-get-v2search) *(Ireland only, testing)*
15. [GET `/content/{document_id}/{version}`](#15-get-v2contentdocument_idversion)

---

## 1. GET `/v2/jurisdictions`

List every jurisdiction available in the API (country or state).

**Parameters:** none documented.

**Response**

```json
{
  "data": [
    { "name": "Australia", "iso": "AU", "type": "country" },
    { "name": "Alabama",   "iso": "US_AL", "type": "state" }
  ]
}
```

Useful for: discovering newly added jurisdictions, syncing supported regions.

---

## 2. GET `/v2/domains`

List top-level legal domains.

**Query parameters**

| Name     | Example       | Description |
|----------|---------------|-------------|
| `limit`  | `20`          | Records per page |
| `offset` | `0`           | Pagination offset |
| `domain` | `Private law` | Filter by domain name |

**Response**

```json
{
  "pagination": { "total_count": 3, "limit": 20, "offset": 0 },
  "data": [
    { "domain_id": "A", "domain": "Private law" },
    { "domain_id": "B", "domain": "Public Law" },
    { "domain_id": "C", "domain": "Miscellaneous" }
  ]
}
```

---

## 3. GET `/v2/domain/{domain_id}/subdomains`

List subdomains under a domain.

**Path parameters**

| Name        | Example | Description |
|-------------|---------|-------------|
| `domain_id` | `B`     | Parent domain id |

**Query parameters**

| Name        | Example   | Description |
|-------------|-----------|-------------|
| `limit`     | `20`      | Records per page |
| `offset`    | `0`       | Pagination offset |
| `domain_id` | `B`       | (Also accepted as query filter) |
| `subdomain` | `General` | Filter by subdomain name |

**Response** (note: nested `data.data` in published example — verify against live API)

```json
{
  "data": [{
    "pagination": { "total_count": 2, "limit": 20, "offset": 0 },
    "data": [
      { "domain_id": "B", "subdomain_id": "B.1", "subdomain": "General" },
      { "domain_id": "B", "subdomain_id": "B.2", "subdomain": "Industry" }
    ]
  }]
}
```

---

## 4. GET `/v2/categories`

List categories in the taxonomy. Categories are jurisdiction-agnostic.

**Query parameters**

| Name           | Example         | Description |
|----------------|-----------------|-------------|
| `limit`        | `20`            | Records per page |
| `offset`       | `0`             | Pagination offset |
| `subdomain_id` | `B.1`           | Filter by parent subdomain |
| `category`     | `Property Law`  | Filter by category name |

**Response**

```json
{
  "data": [{
    "pagination": { "total_count": 81, "limit": 20, "offset": 0 },
    "data": [
      { "domain": "Private law", "subdomain": "Private International Law",
        "category_id": "A.1.1", "category_name": "General Private International Law" },
      { "domain": "Private law", "subdomain": "Private International Law",
        "category_id": "A.1.2", "category_name": "Specific Private International Law" }
    ]
  }]
}
```

---

## 5. GET `/v2/categories/{category_id}/subcategories`

List subcategories under a category.

**Path parameters**

| Name           | Example   | Description |
|----------------|-----------|-------------|
| `category_id`  | `B.1.1`   | Parent category id |

**Query parameters**

| Name                | Example                     | Description |
|---------------------|-----------------------------|-------------|
| `limit`             | `20`                        | Records per page |
| `offset`            | `0`                         | Pagination offset |
| `subcategory_name`  | `Enforcement and sanctions` | Filter by name |

**Response**

```json
{
  "pagination": { "total_count": 12, "limit": 20, "offset": 0 },
  "data": [
    { "category_id": "B.1.1", "category_name": "Accreditation",
      "subcategory_id": "B.1.1.1", "subcategory_name": "Designation of national accreditation bodies" }
  ]
}
```

---

## 6. GET `/v2/subcategories/{subcategory_id}/law-types`

List law types under a subcategory (explanatory only — Law Types are not used for tagging).

**Path parameters**

| Name              | Example   |
|-------------------|-----------|
| `subcategory_id`  | `B.1.1.1` |

**Query parameters**

| Name     | Example | Description |
|----------|---------|-------------|
| `limit`  | `20`    | Records per page |
| `offset` | `0`     | Pagination offset |

**Response**

```json
{
  "pagination": { "total_count": 112, "limit": 20, "offset": 0 },
  "data": [
    {
      "category_id": "B.1.1", "category_name": "Accreditation",
      "subcategory_id": "B.1.1.1", "subcategory_name": "Designation of national accreditation bodies",
      "law_type_id": 4320,
      "law_type": "Designation of national accreditation bodies Digital Credentials and Blockchain Law"
    }
  ]
}
```

---

## 7. GET `/v2/portals`

List the official portals through which laws and regulations are published.

**Query parameters**

| Name   | Example                | Description |
|--------|------------------------|-------------|
| `iso`  | `US`, `IE`             | Jurisdiction ISO code |
| `lang` | `English`              | Language |
| `name` | `OSHA`                 | Portal name filter |
| `tag`  | `General Legislation`  | Portal tag |

**Response**

```json
{
  "data": [
    {
      "name": "AVG-Helpdesk voor Zorg en Welzijn",
      "url": "www.avghelpdeskzorg.nl",
      "language": "Dutch",
      "jurisdiction": { "country": "Netherlands", "state": "" },
      "total_links": 10
    }
  ]
}
```

---

## 8. GET `/v2/authority-types`

List authority types (e.g. Regulation, Consultation, Guideline) within a jurisdiction.

**Query parameters**

| Name             | Example                  | Description |
|------------------|--------------------------|-------------|
| `iso`            | `NL`, `CA`, `IE`         | **Required.** Jurisdiction ISO code |
| `limit`          | `20`                     | Records per page |
| `offset`         | `0`                      | Pagination offset |
| `authority_type` | `Practice note`          | Filter by authority type |
| `portal_name`    | `Law Society of Ireland` | Filter by portal |

**Response**

```json
{
  "pagination": { "total_count": 97435, "limit": 2, "offset": 0 },
  "data": [
    { "iso": "NL", "authority_type": "Regulation",   "portal_name": "Law Society of Ireland" },
    { "iso": "NL", "authority_type": "Consultation", "portal_name": "Law Society of Ireland" }
  ]
}
```

---

## 9. GET `/v2/issuing-authority`

List the official bodies that issue legal instruments within a jurisdiction.

**Query parameters**

| Name                 | Example                          | Description |
|----------------------|----------------------------------|-------------|
| `iso`                | `NL`, `FI`, `DE`                 | **Required.** Jurisdiction ISO code |
| `limit`              | `20`                             | Records per page |
| `offset`             | `0`                              | Pagination offset |
| `issuing_authority`  | `Companies Registration Office`  | Filter by authority name |
| `portal_name`        | `Corporate Publications`         | Filter by portal |

**Response**

```json
{
  "pagination": { "total_count": 97435, "limit": 2, "offset": 0 },
  "data": [
    { "iso": "NL", "issuing_authority": "Court of Justice",    "portal_name": "Corporate Publications" },
    { "iso": "NL", "issuing_authority": "Helsingin hovioikeus", "portal_name": "Corporate Publications" }
  ]
}
```

---

## 10. GET `/v2/contents`

**Primary metadata endpoint.** Returns structured metadata records for laws/regulations across jurisdictions. Metadata only — use `/contents/full-text`, `/contents/markdown`, or `/content/{id}/{version}` to retrieve the actual text.

**Query parameters** (filters are AND-combined)

| Name                  | Example                              | Description |
|-----------------------|--------------------------------------|-------------|
| `iso`                 | `NL`, `CA`, `US_AL`                  | Jurisdiction ISO code |
| `portal`              | `wetten.overheid.nl`                 | Portal name or domain |
| `language`            | `English`                            | Content language |
| `title`               | `Access to Information`              | Title search (partial match) |
| `url`                 | `https://laws-lois.justice.gc.ca/...`| Exact source URL |
| `authority_type`      | `Regulation`, `Consultation`         | Nature of instrument |
| `issuing_authority`   | -                                    | Issuing body |
| `status`              | `new`                                | Internal status |
| `repealed`            | `true`, `false`                      | Repeal status |
| `publication_date`    | `2025-01-01`                         | Official publication date |
| `effective_date`      | `2025-02-01`                         | Effective date |
| `expiration_date`     | `2030-12-31`                         | Expiration date |
| `last_amendment`      | `2024-10-15`                         | Last amendment date |
| `crawling_date`       | `2025-01-23`                         | Date content was crawled |
| `last_updated`        | `2025-01-10`                         | Source last updated |
| `file_data_only`      | `true`                               | Only records with downloadable files |
| `limit`               | `20`                                 | Records per page |
| `offset`              | `0`                                  | Pagination offset |

**Record shape (selected fields)** — full record example below.

- `document_id` — stable across versions
- `version` — increments on amendment
- `title`, `section_title`, `summary`
- `jurisdiction.country`, `jurisdiction.state`
- `publication_date`, `effective_date`, `expiration_date`, `date_of_enactment`, `date_of_last_amendment`, `date_of_repealed`, `date_of_decision`
- `issuing_authority`, `type_of_authority`, `portal`, `portal_name`
- `language`, `legal_link`, `repealed`, `status`
- `source_identifier`, `source_secondary_identifier`
- `crawling_date`, `last_updated`
- `metadata.keywords`, `metadata.portal_tags`, `tags`, `source_keywords`
- `file_data` — `{ content_type, file_size, timestamp }` when a source file exists

**Example response**

```json
{
  "pagination": { "total_count": 2609108, "limit": 20, "offset": 0 },
  "data": [
    {
      "document_id": 12971971,
      "title": "SBCA et al. vs. City of Chicago",
      "section_title": "",
      "jurisdiction": { "country": "United States", "state": "" },
      "publication_date": "2021-01-11T00:00:00.000Z",
      "effective_date": "",
      "expiration_date": "",
      "date_of_enactment": "",
      "date_of_last_amendment": "",
      "issuing_authority": "Federal Communications Commission",
      "type_of_authority": "Declaratory Ruling",
      "language": "English",
      "legal_link": "https://docs.fcc.gov/public/attachments/DA-21-38A1.pdf",
      "repealed": null,
      "metadata": { "keywords": [], "portal_tags": null },
      "status": null,
      "version": 1,
      "source_identifier": "DA-21-38",
      "source_secondary_identifier": "20-284",
      "date_of_repealed": "",
      "date_of_decision": "",
      "summary": "FCC grants Declaratory Petition filed by SBCA et al. against City of Chicago",
      "tags": "",
      "source_keywords": "",
      "crawling_date": "2025-10-30T02:29:21.659Z",
      "portal": "www.fcc.gov",
      "portal_name": "Federal Communications Commission",
      "last_updated": "2025-10-30T02:29:21.659Z",
      "file_data": {
        "content_type": "application/pdf",
        "file_size": 125755,
        "timestamp": "2025-10-30T02:29:21.650682"
      }
    }
  ]
}
```

**Notes**

- The same `document_id` may appear multiple times with different `version` values.
- Filtering is AND-based.
- New metadata fields may be added without breaking changes.
- Some fields may be empty depending on source availability.

---

## 11. GET `/v2/contents/full-text`

Plain-text content for laws/legal documents in a jurisdiction. Text content only — no metadata.

**Query parameters** — same set as `/contents`, plus `show_all` (return all matching records — use with caution).

**Response**

```json
{
  "pagination": { "total_count": 16380, "limit": 20, "offset": 0 },
  "data": [
    { "document_id": 1018301, "full_text": "Deel 1. Algemene bepalingen\nHoofdstuk 1.1. Begripsbepalingen\n..." }
  ]
}
```

**Notes**

- Text is normalized and cleaned; structure preservation depends on source quality.
- Same document may appear across multiple versions.
- Documents can be extremely large — paginate.

---

## 12. GET `/v2/contents/markdown`

Markdown-formatted document content (preserves headings, sections, lists). Markdown only — no metadata.

**Query parameters** — same set as `/contents/full-text`. A `document_id` may be supplied as a path-style filter (the portal example shows it as a path parameter, but the listed URL pattern is the same query endpoint).

**Response**

```json
{
  "pagination": { "total_count": 16380, "limit": 20, "offset": 0 },
  "data": [
    { "document_id": 1018301, "markdown": "\n\nDeel 1\\. Algemene bepalingen\n----------------------------\n\n### Hoofdstuk 1\\.1\\. Begripsbepalingen\n..." }
  ]
}
```

**Use case for change detection:** preferred over `full-text` for diffing, because the structure (headings, articles, lists) is preserved.

---

## 13. GET `/v2/content/{document_id}/file-url`

Returns a **time-limited signed download URL** for the original source file (PDF / consultation paper / scanned text), when one exists.

**Path parameters**

| Name           | Example   |
|----------------|-----------|
| `document_id`  | `8337206` |

**Response**

```json
{
  "data": {
    "document_id": 8337206,
    "url": "https://lawstronaut-files.s3.eu-central-1.amazonaws.com/crawled_data/231/...pdf?X-Amz-Algorithm=...&X-Amz-Expires=3600&..."
  }
}
```

**Notes**

- URL expires automatically (1 hour observed in example).
- Accessing the URL itself does not require additional authentication.
- Not every document has a file. Empty result or `404` when none exists.
- `404` is also returned for unknown `document_id`; `401` for invalid/missing token.

---

## 14. GET `/v2/search` *(Ireland only, testing)*

Semantic search over Lawstronaut legal content. Designed for natural-language discovery — not for bulk retrieval.

**Availability:** currently `iso=IE` only.

**Query parameters**

| Name        | Example                          |
|-------------|----------------------------------|
| `iso`       | `IE` |
| `title`     | `Privacy` |
| `full_text` | `Data protection obligations` |
| `limit`     | `10` |
| `offset`    | `0` |

`title` and/or `full_text` may be supplied. When both are present, ranking uses combined semantic signals.

**Response** — ranked, relevance-ordered. Includes metadata, `content` (plain), and `content_markdown`:

```json
{
  "pagination": { "total_count": 20, "limit": 20, "offset": 0 },
  "data": [
    {
      "document_id": 8157275,
      "title": "Corporate Affairs Division - Privacy Notice",
      "jurisdiction": { "country": "Ireland", "state": "" },
      "publication_date": "2021-01-20T00:00:00.000Z",
      "date_of_last_amendment": "2025-11-27T00:00:00.000Z",
      "issuing_authority": "Department of Agriculture, Food and the Marine",
      "language": "English",
      "portal_name": "Government of Ireland",
      "content_markdown": "# Data Protection Notice\n\nGeneral Data Protection information applicable...",
      "content": "Data Protection Notice\n\nGeneral Data Protection information applicable..."
    }
  ]
}
```

**Notes**

- Semantic models and ranking may evolve; response shape may change.
- Use `/contents` + `/contents/full-text` for stable schemas and bulk pipelines.

---

## 15. GET `/v2/content/{document_id}/{version}`

Retrieve the full metadata **and** full content for a specific version of a document. This is the authoritative point-in-time read.

**Path parameters**

| Name           | Example   | Description |
|----------------|-----------|-------------|
| `document_id`  | `8157275` | Document id |
| `version`      | `1`       | Version number |

**Response**

```json
{
  "data": {
    "document_id": 23,
    "title": "Consolidated federal laws of Canada, Access to Information Act",
    "jurisdiction": { "country": "Canada", "state": "" },
    "publication_date": "2024-10-31",
    "effective_date": "2024-12-15",
    "expiration_date": "",
    "date_of_enactment": "",
    "date_of_last_amendment": "2024-10-15",
    "issuing_authority": "Department of Justice",
    "type_of_law": "Federal laws of Canada",
    "language": "English",
    "legal_link": "https://laws-lois.justice.gc.ca/eng/acts/A-1/page-1.html",
    "repealed": false,
    "content": { "full_text": "Previous Page Table of Contents Next Page Access to Information Act R.S.C., 1985, c. A-1..." },
    "metadata": { "keywords": [] },
    "status": "new",
    "version": 1,
    "crawling_date": "2025-01-23",
    "portal": "laws-lois.justice.gc.ca",
    "last_updated": "2024-10-31"
  }
}
```

**Versioning behavior**

- A `document_id` may have many versions, ordered chronologically.
- A version is **immutable** once created.
- New versions are created on amendment, consolidation, or material change.
- For the *latest* version, query `/contents` filtered to the `document_id` and pick the highest `version`. For historical analysis, request an older version number explicitly.

---

## Cross-reference: which endpoint to use

| Goal                                        | Endpoint                                |
|---------------------------------------------|-----------------------------------------|
| Discover available jurisdictions/portals    | `/jurisdictions`, `/portals`            |
| Browse taxonomy                             | `/domains`, `/categories`, `/subcategories/{id}/law-types` |
| Bulk metadata listing / structured filter   | `/contents`                             |
| Plain text for AI/NLP                       | `/contents/full-text`                   |
| Structure-preserving text for diffing       | `/contents/markdown`                    |
| Original source file (PDF, etc.)            | `/content/{id}/file-url`                |
| Authoritative point-in-time read            | `/content/{id}/{version}`               |
| Natural-language discovery (IE only)        | `/search`                               |
