# API sample data

Real document captures from the Lawstronaut v2 API, used as fixtures for the clause parser, alignment, and diff engine (see `docs/RFC-2 clause-alignment.md`).

## Layout

Per document: `<iso>-<document_id>-v<version>.md` (markdown content from `/v2/contents/markdown`) and `<iso>-<document_id>-v<version>.meta.json` (metadata record from `/v2/contents`, with a `_provenance` block prepended).

`fixtures.json` is a machine-readable index of every captured document — iso, portal, document_id, version, title, type of authority, language, and markdown size.

## How they were collected

Run `uv run scripts/fetch_fixtures.py` from the repo root. The script logs in to Lawstronaut using credentials from `.env`, discovers jurisdictions and portals, round-robins across jurisdictions to maximise diversity, and saves one document per portal until the target count is met.

Re-running adds new fixtures (existing slugs are skipped).

## Current capture (2026-06-04)

31 documents — the original Irish Statute Book Act plus 30 round-robin captures across 30 jurisdictions and ~30 different portals. Languages span Catalan, Arabic, English, German, French, Czech, Spanish, Greek, Croatian, Hungarian, Italian, Japanese, Korean, Latvian, Chinese, and others. Authority types include Acts, Regulations, Caselaw, Decrees, Practice Notes, and Notices.

Size spread (markdown bytes): minimum 0.7 KB (HR), p50 ~12 KB, p90 ~160 KB, maximum 3.8 MB (AL). This range is deliberate — the large outliers stress-test the parser and the blob-vs-row threshold question raised in `docs/RFC-3 database-design.md`.

See `fixtures.json` for the full inventory.
