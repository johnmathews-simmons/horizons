# 2026-06-07 — WU8.7 UK/EU cluster rework (genuine-origin pivot)

## 1. Problem

The UK demo subscription was populated by relabelling 9 foreign-origin
fixtures (AD, AE, BR, CN, CY, FJ, GE, plus IE-27732019 and AU-2145602)
onto `jurisdiction: UK`. Inspection of the seed catched on a Catalan-
titled Andorran *Edicte* and a Chinese-titled CN doc both labelled as
"UK". A demo viewer clicking into the UK list would immediately spot
the seam — "the tool looks broken." Same shape on the EU side: BE/AT/
ES/DK/FI/GR were relabelled to EU without being native EU-institution
sources.

## 2. Decision

Two of the foreign UK-labelled docs (IE-27732019, AU-2145602) carry
the demo's only MOVED + ADD/REMOVE change-event signals via their
synthetic v2 versions. Replacing them would mean re-authoring v2
edits the night before the demo. Trade-off chosen: keep those two in
the UK relabel slot, replace the other 7 UK fillers + 6 EU fillers
with fresh native-origin captures.

Final state:

- **UK (10)** — 8/10 natively GB, 2/10 non-UK relabels kept for v2.
- **EU (10)** — 7/10 native EU institutions, 3/10 EU-member-state
  regulators (FR/DE/IT) kept for v2.

## 3. What was done

1. `scripts/explore_uk_eu_portals.py` — wrote a Lawstronaut explorer
   that lists all portals available under iso=GB and iso=EU. Confirmed
   33 GB portals (all English) and 30 EU portals (all English),
   including FCA, BoE, PRA Rulebook, Bailii, PSR, Supreme Court,
   Parliament, Legislation UK, CAT on the UK side, and EBA, ECB, ECB
   Banking Supervision, SRB, ESMA, EIOPA on the EU side.
2. `scripts/fetch_uk_eu_fillers.py` — wrote a targeted fetcher that
   pulls the largest markdown body (≥2 KB) from each named portal,
   saving 13 fixtures into `data/samples/<iso>-<id>-v1.{md,meta.json}`
   and appending rows to `data/samples/fixtures.json`. Two of the
   originally-targeted UK portals (PRA Rulebook, Supreme Court)
   returned no usable markdown; substituted Competition Appeals
   Tribunal + Bailii.
3. `data/curated_set.yaml` — swapped the 7 UK fillers + 6 EU fillers
   for the 13 new captures. Updated the cluster comments to reflect
   the new sourcing.
4. Old foreign fixture files are left on disk (still referenced by
   `journal/260605-wu24-alignment-regression-suite.md`, two design
   docs, and one test in `test_portal_config.py`). They will continue
   to be seeded with their native ISO + default sector but won't
   appear under any demo client's subscription.

## 4. New UK (10)

| ID | Portal | Title (truncated) |
|---|---|---|
| 28914588 | caselaw.nationalarchives.gov.uk | Foat v DWP (v2-attached) |
| 27732019 | (IE Statute Book — relabel) | Protection of Employees (Insolvency) Act (v2-attached, MOVED) |
| 2145602 | (AU Federal Register — relabel) | Social Security Cyclone Alfred Determination (v2-attached) |
| 32416312 | www.fca.org.uk | FCA Handbook corrections |
| 37048477 | www.bankofengland.co.uk | External business of MFIs operating in the UK |
| 2136076 | www.psr.org.uk | A new regulator for a new world |
| 35840254 | www.legislation.gov.uk | Tobacco and Vapes Act 2026 |
| 37512048 | www.parliament.uk | What's on in the Lords 8-11 June |
| 36992553 | www.catribunal.org.uk | Roadget Business v Shein |
| 9787341 | www.bailii.org | Affirmative Finance v Pearson |

## 5. New EU (10)

| ID | Portal | Title (truncated) |
|---|---|---|
| 31366184 | berec.europa.eu | DNA assessment public debriefing (v2-attached) |
| 31702142 | (FR ACPR — relabel) | Société Générale prudential decision (v2-attached) |
| 20951816 | (DE BA — relabel) | Arbeitsmarkt Februar 2026 (v2-attached) |
| 26863 | (IT MEF — relabel) | EFD 2016 Update (v2-attached) |
| 28943439 | eba.europa.eu | Benchmarking of diversity practices in EU banking |
| 34656519 | www.ecb.europa.eu | Fiscal policy transmission through production networks |
| 37071316 | www.bankingsupervision.europa.eu | Navigating risk, cutting complexity |
| 21435108 | www.srb.europa.eu | From resolution planning to operational readiness |
| 31915825 | www.esma.europa.eu | Report on quality and use of data 2025 |
| 28263756 | www.eiopa.europa.eu | Final Report on revised Guidelines |

## 6. Next session

- `scripts/reseed_aca.sh --yes` once the post-commit `build-and-push`
  + `deploy` workflows finish for the new image. The reseed-corpus
  Job needs the new fixture files baked in.
- Post-demo: revisit IE-27732019 / AU-2145602 — either drop them
  from UK or re-author their v2 edits onto a native-UK substrate so
  the MOVED + ADD/REMOVE signals stay in scope without the
  relabel.
