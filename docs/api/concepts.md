# Lawstronaut — Domain Concepts

These are the conceptual building blocks behind the API. Understanding them
makes the endpoint reference much easier to read.

## Jurisdiction

A country (e.g. `US`, `NL`, `CA`) or a state within a country (e.g. `US_AL` for Alabama).

- ISO-style code: `iso` (two-letter country, or `<country>_<state>` for sub-national)
- `type`: `country` or `state`
- A country contains national/federal laws; a state contains state/provincial/county laws.

## Portal

An **official online publication platform** that makes primary legal sources publicly available — typically a government, regulator, or legislative body.

Examples: national gazettes, parliamentary websites, regulatory authority pages, judicial repositories.

Each portal has: name, URL, language, jurisdiction, and tags (e.g. `legislation`, `caselaw`).

## Taxonomy

A jurisdiction-agnostic five-level classification of legal topics:

```
Domain → Subdomain → Category → Subcategory → Law Type
```

| Level       | Example IDs | Notes |
|-------------|-------------|-------|
| Domain      | `A`, `B`, `C` (Private Law / Public Law / Miscellaneous) | Top-level legal field |
| Subdomain   | `B.1`, `B.2` | Coarse subdivision of a domain |
| Category    | `A.1.1`, `B.1.1` | Standardized legal subject; reusable across countries; flagged general or industry-specific |
| Subcategory | `B.1.1.1` | Narrower legal concept within a category |
| Law Type    | numeric `law_type_id` | Specific legal instrument or regulatory form |

**Tagging:** Portals (and, in future, legal documents) are tagged at the **Category** and **Subcategory** level. Law Types currently explain a subcategory but are not themselves used as tags.

## Authority Type vs Issuing Authority

These describe **how** and **who**, respectively:

| Dimension          | Answers              | Example          | Jurisdiction-specific? |
|--------------------|----------------------|------------------|------------------------|
| Authority Type     | *How* was it issued? | Act, Decree, Regulation, Consultation, Guideline, Decision, Order | Yes — names vary per country, often in local language |
| Issuing Authority  | *Who* issued it?     | Courts, ministries, regulators, commissions | Yes — same authority may have different names across countries |

## Document and Version

Every legal text returned by Lawstronaut is a **versioned record**.

- `document_id` — stable identifier across versions
- `version` — increments when:
  - amendments are issued
  - consolidations are updated
  - source content materially changes
- Each version is **immutable** once created. Older versions remain retrievable.
- The same `document_id` will appear in list endpoints once per version, ordered chronologically.

**This is the change-detection hook for our tool**: poll a document and watch for a new `version`, or compare two versions to surface what changed.

## Relationships at a glance

```
Jurisdiction (iso)
 └── Portals
       └── Authority Types
             └── Issuing Authorities
                   └── Documents (document_id)
                          └── Versions (1..N)
                                ├── /contents/full-text  (plain text)
                                ├── /contents/markdown   (structured markdown)
                                └── /content/{id}/file-url (PDF/source file)

Taxonomy (Domain → Subdomain → Category → Subcategory → Law Type)
 └── tags Portals (and, in future, Documents) at Category + Subcategory level
```
