# Synthetic v2 documents (WU8.0)

Five demo fixtures with hand-authored "v2" revisions of documents already
held in `data/samples/`. Each pair (`v1`, `v2`) carries small, realistic
clause-level edits — one **add**, one **modify**, one **remove** — so the
alignment pipeline emits `ADDED`, `MODIFIED`, and `REMOVED` change events
when both versions are staged.

Synthetic, not collected from Lawstronaut. The v1 markdown is identical to
the file at `data/samples/<slug>-v1.md`; the v2 file is the v1 contents
with three targeted edits documented below.

The acceptance for WU8.0 calls for one document each from IE, GB, FR, DE,
and US. The current fixture inventory has no US capture, so we substitute
**IT** (a financial-services document) to keep the v2 spread broad. The
gap is captured as a follow-up in the WU8.0 journal entry.

## Inventory

| slug | jurisdiction | sector (per curated_set.yaml) | source | diff intent |
|------|-------------|-------------------------------|--------|-------------|
| `ie-8064194` | IE | corporate-governance | `data/samples/ie-8064194-v1.md` | see below |
| `gb-28914588` | GB | employment | `data/samples/gb-28914588-v1.md` | see below |
| `fr-31702142` | FR | financial-services | `data/samples/fr-31702142-v1.md` | see below |
| `de-20951816` | DE | employment | `data/samples/de-20951816-v1.md` | see below |
| `it-26863` | IT | financial-services | `data/samples/it-26863-v1.md` | see below |

## Diff intent per document

### `ie-8064194` — CRO Social Media Policy

- **REMOVED** — the "Following and Retweets" Twitter paragraph: the
  one-paragraph policy on follow-back behaviour and endorsement.
- **MODIFIED** — Twitter availability hours: "Monday to Friday" → "Monday to
  Thursday".
- **ADDED** — a new "BlueSky @cro.ie" section after the Twitter "Availability"
  heading, mirroring the structure of the existing per-platform sections.

### `gb-28914588` — Foat v Department of Work and Pensions

- **REMOVED** — paragraph 13 on aggravated and exemplary damages.
- **MODIFIED** — paragraph 9: ACAS uplift "2%" → "5%".
- **ADDED** — a new paragraph 9A clarifying that the ACAS uplift was
  applied after the deduction for past received state benefits.

### `fr-31702142` — ACPR Société Générale decision

- **REMOVED** — paragraph 43 on Société Générale's 2025 net banking
  product and equity figures.
- **MODIFIED** — paragraph 44: financial penalty "20 millions d'euros" →
  "25 millions d'euros".
- **ADDED** — a new paragraph 45 noting the aggravating factors that
  influenced the penalty quantum (limited cooperation, persistence of
  the breaches).

### `de-20951816` — Arbeitsmarkt Februar 2026 (BA)

- **REMOVED** — the closing line linking out to the
  "Statistikseite" monthly report.
- **MODIFIED** — opening statistics block: "3.070.000" → "3.105.000".
- **ADDED** — a new "Saisonbereinigung" section between "Kurzarbeit" and
  "Erwerbstätigkeit und Beschäftigung" explaining the seasonal-adjustment
  methodology.

### `it-26863` — MEF Update of the Economic and Financial Document 2016

- **REMOVED** — the opening paragraph of the "Public finance to support
  growth" section discussing the balance between growth and fiscal
  consolidation.
- **MODIFIED** — same section, net-borrowing target for 2017: "2.0
  percent" → "1.8 percent". The document title is also annotated with
  "(REVISED)" to signal the second iteration.
- **ADDED** — a new "Revision note" subsection at the top of the
  Introduction, explaining that this is a Cabinet-revised update of the
  original update.

## How the alignment pipeline consumes these

The WU8.0 seed staging path inserts both `v1` and `v2` rows in
`document_versions` for each pair, parses both, and runs
`horizons_core.core.alignment.align` to emit the change events directly.
See `docs/seeding.md` for the schema and `journal/260605-wu80-...` for
the WU8.0 staging trade-offs.
