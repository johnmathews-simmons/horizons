# Synthetic v2 documents (WU8.0)

Eight demo fixtures with hand-authored "v2" revisions of documents already
held in `data/samples/`. The original five pairs (WU8.0) each carry one
**add**, one **modify**, and one **remove**. The three WU8.6 additions
(`ie-27732019`, `au-2145602`, `eu-31366184`) carry varied two-edit
combinations that, taken together with the original five, exercise all
four change kinds — `ADDED`, `REMOVED`, `MODIFIED`, and `MOVED` — across
the demo corpus when both versions are staged.

Synthetic, not collected from Lawstronaut. The v1 markdown is identical to
the file at `data/samples/<slug>-v1.md`; the v2 file is the v1 contents
with three targeted edits documented below.

The acceptance for WU8.0 calls for one document each from IE, GB, FR, DE,
and US. The current fixture inventory has no US capture, so we substitute
**IT** (a BANKING document) to keep the v2 spread broad. The gap is
captured as a follow-up in the WU8.0 journal entry.

## Inventory

`gb-28914588` and `fr-31702142` are relabelled in `data/curated_set.yaml`
so the seeded `jurisdiction`/`sector` columns line up with the WU8.1 demo
accounts' subscription scopes. The fixture file on disk stays under its
captured iso; the seed library applies the override at write time. See
the `jurisdiction` per-doc override in `docs/runbooks/seeding.md`.

| slug | fixture iso | seeded as (jurisdiction, sector) | source | diff intent |
|------|-------------|----------------------------------|--------|-------------|
| `ie-8064194` | IE | (IE, corporate-governance) | `data/samples/ie-8064194-v1.md` | see below |
| `gb-28914588` | GB | **(UK, BANKING)** — demo relabel | `data/samples/gb-28914588-v1.md` | see below |
| `fr-31702142` | FR | **(EU, BANKING)** — demo relabel | `data/samples/fr-31702142-v1.md` | see below |
| `de-20951816` | DE | (DE, employment) | `data/samples/de-20951816-v1.md` | see below |
| `it-26863` | IT | (IT, BANKING) | `data/samples/it-26863-v1.md` | see below |
| `ie-27732019` | IE | **(UK, BANKING)** — demo relabel | `data/samples/ie-27732019-v1.md` | MOVED + MODIFIED |
| `au-2145602` | AU | **(UK, BANKING)** — demo relabel | `data/samples/au-2145602-v1.md` | ADDED + REMOVED |
| `eu-31366184` | EU | (EU, BANKING) | `data/samples/eu-31366184-v1.md` | MODIFIED + REMOVED |

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
- **Consistency fixes (c95911f):** paragraph 29 restated the old 2%
  uplift + £6,355.79 figure and was updated to 5% / the recomputed
  amount; paragraph 34 dangled on "aggravated and exemplary damages"
  after paragraph 13 was removed and had that phrase trimmed.

### `fr-31702142` — ACPR Société Générale decision

- **REMOVED** — paragraph 43 on Société Générale's 2025 net banking
  product and equity figures.
- **MODIFIED** — paragraph 44: financial penalty "20 millions d'euros" →
  "25 millions d'euros".
- **ADDED** — a new paragraph 45 noting the aggravating factors that
  influenced the penalty quantum (limited cooperation, persistence of
  the breaches).
- **Consistency fix (c95911f):** ARTICLE 1ER of the dispositif still
  read "20 millions d'euros" after paragraph 44 was modified and was
  brought into line with the 25 M€ quantum.

### `de-20951816` — Arbeitsmarkt Februar 2026 (BA)

- **REMOVED** — the closing line linking out to the
  "Statistikseite" monthly report.
- **MODIFIED** — opening narrative under "Arbeitslosigkeit, Unter­beschäftigung
  und Erwerbslosigkeit": "3.070.000" → "3.105.000". This was finished
  by c95911f — the original WU8.0 v2 updated the headline summary
  block at the top of the document but left this paragraph stale; the
  consistency fix completed it.
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
- **Consistency fixes (c95911f):** Tables I.1, II.2, and III.3 each
  carried a 2017 net-borrowing cell that still showed 2.0% and were
  updated to 1.8%; two further Chapter III narratives (the
  policy-scenario discussion at III.2 and the Table I.1 footnote (5))
  plus one cascade-shifted "Public finance to support growth" sibling
  restated the old 2.0% figure and were brought into line.

### `ie-27732019` — Protection of Employees (Employers' Insolvency) (Amendment) Act 2026

- **MOVED** — section 11 renumbered to section 11A; clause body
  byte-identical, parser path moves from `PART 2 / section 11` to
  `PART 2 / section 11A`. Framing: Law Reform Commission Revised Acts
  edition restores engrossed-bill numbering after a gazette misprint.
- **MODIFIED** — section 12(5A)(a) (Minister's order-making power to vary
  the section 4B notice period): upper bound widened from "not more than
  12 weeks" to "not more than 16 weeks".

### `au-2145602` — Social Security (AGDRP—Ex-Tropical Cyclone Alfred—NSW) Determination (No. 3) 2025

- **REMOVED** — sub-paragraph (iv) of the `major damage` (residence)
  definition: "sewage contamination of the interior of the residence;
  or". The remaining (i)–(iii) limbs cover most interior-damage cases.
- **ADDED** — new closing sentence in Schedule 1 fixing the LGA boundary
  reference date: "The areas listed in the table are determined by
  reference to the local government area boundaries in force in New
  South Wales on 4 March 2025."

### `eu-31366184` — BEREC: Digital Networks Act assessment public debriefing

- **MODIFIED** — debriefing date: "10 June 2026" → "17 June 2026" (event
  postponed by one week).
- **REMOVED** — closing paragraph of the "Registration and engagement"
  section: "The event will be livestreamed on the BEREC website. Online
  participants will have the opportunity to submit questions via a Q&A
  chat function." Framing: format reverted to in-person-only.
- **Consistency fix (c95911f):** the "held in hybrid format... in
  person and online" sentence contradicted the in-person-only framing
  once the livestream paragraph was removed, and was rewritten.

## How the alignment pipeline consumes these

The WU8.0 seed staging path inserts both `v1` and `v2` rows in
`document_versions` for each pair, parses both, and runs
`horizons_core.core.alignment.align` to emit the change events directly.
See `docs/seeding.md` for the schema and `journal/260605-wu80-...` for
the WU8.0 staging trade-offs.
