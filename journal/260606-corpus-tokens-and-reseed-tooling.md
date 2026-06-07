# Corpus tokens, reseed tooling, parser disambiguation

*Last revised: 2026-06-06.*
*Path: journal/260606-corpus-tokens-and-reseed-tooling.md.*

## Reported symptom

After logging into the deployed staging SPA as `demo-uk@demo.example.com`
and navigating to `/changes`, the list rendered empty. Same on
`demo-eu@demo.example.com`. Question raised in parallel: are we hitting
Lawstronaut live during the demo or is everything pre-seeded?

## Diagnosis

Two things, hiding each other.

**Token mismatch.** `packages/horizons-api/scripts/create_demo_accounts.py`
writes subscription scopes `(UK, BANKING)` and `(EU, BANKING)`. The
seeded corpus, on the other hand, uses ISO-2 codes from
`data/samples/fixtures.json` (`GB`, `FR`, `IE`, …) and the
`financial-services` sector from `data/curated_set.yaml`. Subscription
filtering is an exact-match on `(jurisdiction, sector)` — no alias map,
no normalisation. So the UK scope matched zero rows. The EU scope's
only candidate document was `eu-31366184` (BEREC press item) tagged
`consumer-protection`, also zero matches.

**No live Lawstronaut.** The demo's headline diff comes from the
synthetic-v2 staging path: `data/samples/synthetic_v2/*.md` are
hand-authored v2 markdowns that `scripts/seed_curated_set.py
--stage-synthetic-v2` parses and aligns locally to produce
`change_events`. Live worker polls are parked at `next_poll_at =
2026-12-31` on those staged rows. So an expired Lawstronaut token has
zero demo impact — the demo runs entirely off seeded rows.

## Decision: align corpus tokens with demo scope tokens

Two paths:

1. Rename the demo subscription tokens to match the corpus
   (`(GB, financial-services)`, `(EU, consumer-protection)`).
2. Make the corpus tokens match the demo (relabel `GB` → `UK`,
   `financial-services` → `BANKING`).

Went with (2). Rationale: `packages/horizons-core/src/horizons_core/db/schema.md`
documents `BANKING` and `INSURANCE` as the canonical sector taxonomy,
and `packages/horizons-api/scripts/seed_e2e.py` already uses
`(UK, BANKING)` / `(EU, BANKING)` for the e2e fixtures. The curated
set's `financial-services` was the outlier, not the demo accounts.

Implementation:

- Added a `jurisdiction` per-doc override to the curated-set schema
  (`packages/horizons-ingestion/src/horizons_ingestion/seed.py`).
  Free-form — not validated against the top-level `jurisdictions`
  list, because that list is the fixture-iso filter, not the output
  taxonomy.
- `data/curated_set.yaml`: renamed `financial-services` → `BANKING`
  throughout the sectors taxonomy. Relabelled `gb-28914588` (Foat v
  DWP, the WU8.0 synthetic v2 demo headline) → `(UK, BANKING)` for
  demo-uk's clause-diff beat. Relabelled `fr-31702142` (ACPR /
  banque-france) → `(EU, BANKING)` for demo-eu — the FR ACPR
  participates in the EU banking-union supervisory chain, so the
  relabel is defensible for a public showcase. Caveat noted inline:
  not a real EU regulator decision; replace post-demo if a true EU
  banking v2 capture is added.
- Updated `docs/runbooks/{demo,seeding}.md`,
  `docs/api/horizons-primitives.md`, and
  `data/samples/synthetic_v2/README.md` to match the new tokens.
- Webapp test fixtures (`ChangesView.spec.ts`,
  `ChangeDetailView.spec.ts`) used `'financial-services'` as a
  cosmetic placeholder — bumped to `'BANKING'` for consistency.

Shipped as `b40949d`.

## Building the reseed pipeline

The `documents` table is append-only via trigger (`BEFORE UPDATE`,
defined in `packages/horizons-core/migrations/versions/0003_corpus_tables.py`).
Re-running the seed against an already-populated DB is a no-op —
existing rows keep their stale `financial-services` / `GB` tokens. To
make the new tokens visible on staging, I needed to wipe corpus
tables and reseed.

The reseed needs to run *against* the staging DB. Three options
considered:

1. From the laptop, punching a firewall hole through to staging
   Postgres.
2. From inside the worker container via `az containerapp exec`.
3. As a one-shot Container Apps Job, dispatched from the laptop.

Initially picked (2). Built `scripts/reseed_corpus.py` (in-container
orchestrator: pre-flight checks, transactional wipe, subprocess
calls to `seed_curated_set.py --stage-synthetic-v2` and
`create_demo_accounts.py --reset`, post-flight smoke) and
`scripts/reseed_aca.sh` (laptop wrapper around `az containerapp
exec`). Updated the worker `Dockerfile` to bake `scripts/` + `data/`
into `/app`, and `.dockerignore` to negate the previously-excluded
paths.

`az containerapp exec` failed. Returned `ClusterExecFailure: Cannot
attach to a container that is not running` regardless of the command,
even a plain `--command "ls"`. Container was healthy by every other
signal (replica state `Running`, `/healthz` returning 200 every 15s,
worker logs showing the asyncio loop ticking). Diagnosis:
`az containerapp` extension `1.3.0b4` + workers without HTTP ingress
is a known-bad combo.

Pivoted to (3). Added
`infra/modules/reseed-corpus-job.bicep` mirroring
`seed-demo-accounts-job.bicep`. Reuses the worker image (where the
data + scripts are baked), runs `python /app/scripts/reseed_corpus.py
--yes`, gets the Postgres + demo passwords through the same
secret-binding pattern the demo-accounts seed already uses. Wired up
in `infra/main.bicep` as section 7e. Rewrote `scripts/reseed_aca.sh`
to call `az containerapp job start`, poll execution status, and tail
logs. Shipped as `324962a`.

## Connection-pool exhaustion blocked the first run

First Job execution failed at pre-flight:

```
psycopg.OperationalError: connection failed:
FATAL:  remaining connection slots are reserved for roles with the SUPERUSER attribute
```

`az containerapp revision list` showed ~25 active worker revisions and
~45 active API revisions, each running 1 replica, each holding a
SQLAlchemy connection pool. Postgres was out of non-superuser slots.

Root cause: both apps are in `revisionMode: Multiple` and nothing in
`.github/workflows/deploy.yml` deactivates the previous revision after a
successful traffic shift. Every push to `main` adds another active
revision; nothing prunes them.

Manually deactivated everything except the latest active revision on
each app (the bash loop is in the post-demo follow-up prompt).
Connection slots freed; the Job retry got past pre-flight.

Captured the follow-up as a self-contained prompt for a fresh session
to investigate post-demo. The two leading options are
`revisionMode: Single` on both apps, or an explicit cleanup step at
the end of `deploy.yml`. I lean toward the cleanup step because the
blue/green flow in `deploy.yml` (the pinned-PREV → SHIFT-traffic
dance) depends on having the previous revision still around for
rollback.

## Parser bug: duplicate-slug siblings

Second Job execution failed mid-staging:

```
psycopg.errors.UniqueViolation: duplicate key value violates unique constraint
"clauses_unique_path_per_version"
DETAIL:  Key (document_version_id, clause_path)=
  (019e9e3a-…, position-de-la-commission/#1) already exists.
```

The FR fixture `fr-31702142-v1.md` has **three** `# Position de la
Commission` headings — one under each Grief. The clause-tree parser in
`packages/horizons-core/src/horizons_core/core/alignment/parser.py`
slugifies each heading to `position-de-la-commission` and assigns the
same `parent.path + (segment,)` to all three. Inserting them into the
same document version then hits the unique constraint.

Quick scan of the other v2-staged fixtures showed `ie-8064194`
(`# Availability` ×2, `# Content` ×3 — per-platform sections) and
`it-26863` (four repeated section headings) had the same hazard.

Considered editing the fixtures to rename headings; user requested the
proper fix. Patched `_open_clause` in `parser.py` to call a new
`_disambiguate_segment` that scans existing siblings and appends
`-2` / `-3` / … to colliding segments. First occurrence keeps its
base slug, so existing fixtures with no duplicates are unaffected.
Stable for fixed input order — that's what the alignment pipeline
needs to pair v1↔v2 clauses across versions with the same
duplicate-heading structure.

Added two regression tests in `packages/horizons-core/tests/test_parser.py`
covering both the slugified-heading case (the FR scenario) and a
sibling-label collision (defensive against errata-style re-issues
sharing a structural marker). Full unit sweep: 345 passed
(previously 343).

Shipped as `5f1d473`.

## Status at end of session

- Code is on `main` at `5f1d473`. CI runs were red for the worker image
  build at one point (the `.dockerignore` exclusion of `data/` and
  `/scripts/` — fixed in `4b2f3df`); subsequent runs are clean.
- Staging is unblocked. One worker revision + one API revision active.
  The reseed Job has the parser fix in its image once the deploy lands.
- The dry-run of `scripts/reseed_aca.sh` is green. The `--yes` run is
  pending the CI deploy of `5f1d473` to roll into the Job's image.
- Demo on 2026-06-08 is on track once the final reseed completes and a
  manual login as both demo accounts shows non-empty `/changes`.

## Follow-ups

1. **Revision pileup** — captured as a paste-ready prompt for a fresh
   session. Post-demo.
2. **EU banking fixture** — the FR-ACPR relabel as `(EU, BANKING)` is a
   public-showcase compromise. Post-demo: capture a true EU banking
   document and v2 it, then drop the relabel.
3. **Worker connection pool sizing** — even after the revision pileup
   is fixed, the worker holds connections proportional to its pool
   size × replica count. If we ever need >1 worker replica, audit the
   SQLAlchemy pool config in
   `packages/horizons-core/src/horizons_core/db/session.py`.

## What I'd do differently

- Should have read the worker `Dockerfile` and `.dockerignore` before
  shipping the first reseed-tooling commit. The exclusion of `data/`
  and `/scripts/` was right there, and the CI failure on the next
  push was avoidable.
- The first reseed-tooling design (`az containerapp exec`) was chosen
  by analogy with kubectl-exec patterns I've used elsewhere. Checking
  whether ACA's exec actually works against no-ingress workers before
  building 100 lines of laptop wrapper would have saved a round-trip.
- Connection-pool exhaustion was diagnosable from a simpler check
  earlier: counting active revisions × pool size would have flagged
  the pileup before the first Job ran.
