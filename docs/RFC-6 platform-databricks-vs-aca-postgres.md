# Platform direction: Databricks as the default, and where to deviate

*Last revised: 2026-06-09.*
*Path: docs/RFC-6 platform-databricks-vs-aca-postgres.md.*

A decision matrix that takes **Databricks as the baseline platform** — the default "go-to" for both the ingestion (ETL) pipeline and the serving database — and asks, criterion by criterion, **where (if anywhere) a non-Databricks alternative is strong enough to justify deviating.** This RFC does **not** pick a winner. It fixes the criteria, scores the alternatives honestly against the Databricks default, and surfaces the open questions the team must answer before weighting the criteria and deciding. The weighting *is* the decision; this doc supplies the inputs to it.

## 1. Status and framing

This is the **first structured, team-wide comparison** of the platform options, written from a **Databricks-default posture**: the data-engineering team who will own this in production are fluent in Databricks and most productive on it, and one-vendor consolidation on a Databricks-centric data platform is the presumptive organisational direction. The burden of proof in this doc therefore runs the other way — an alternative has to *earn* its place against the Databricks default, not the reverse.

One honest caveat up front: **a non-Databricks stack already exists.** A working ACA + Postgres implementation is built, deployed to `horizons-nonprod`, and e2e-gated. That establishes one viable deviation — it does not establish the platform direction. The earlier design docs only touch the choice in passing:

- `RFC-3 database-design.md` §"Database choice direction" names PostgreSQL as a default and lists lakehouse under "Not chosen" with a one-line rationale — a direction taken before the data-eng team's input, not a scored comparison.
- `ADR-0001` chose a long-running asyncio container for the worker, with reaction latency and local-dev ergonomics as the deciding drivers — again, scored against a generalist owner, not the data-eng team.

This RFC re-opens both with Databricks as the thing to beat.

**Two things this RFC is *not* about:**

1. **It is not an immediate re-platforming.** The current ACA + Postgres stack stays in place as-is for now; nothing here calls for switching it out overnight. This RFC concerns the **production direction**, to be decided deliberately and acted on when the team is ready.
2. **It is not a single decision.** Two genuinely separable axes are in play, and they can be decided independently:
   - **Axis A — the serving database** (what the public API reads from under a 3 s p95 budget).
   - **Axis B — the ETL/ingestion compute** (what polls Lawstronaut, parses, aligns, and writes).

   The all-Databricks default is **Databricks for both axes.** The most likely coherent deviation is to keep the Databricks ETL but serve Axis A from Postgres — so the two are scored separately below. Conflating them is the most likely way to reach a wrong answer.

## 2. Terminology: what "Databricks for serving" means

For Axis B (ETL) the Databricks default is unambiguous — Jobs / Delta Live Tables / Spark. For Axis A (serving) "Databricks" splits into three readings, and which one we mean changes the comparison materially. Pin this down before weighting:

- **(a) Delta tables via SQL warehouse** — columnar Delta Lake tables queried by a Databricks SQL (serverless) warehouse; the classic lakehouse serving path.
  - _Effect:_ this is the substantive contest below. Delta is analytical (OLAP) storage; the open question is whether the Databricks default can serve OLTP-shaped API reads under the latency + isolation constraints, or whether Axis A is the one place a Postgres deviation is warranted.
- **(b) Lakebase** — Databricks' managed transactional **Postgres** (built on the Neon engine Databricks acquired in 2025), positioned as the OLTP companion to the lakehouse.
  - _Effect:_ it stays **inside the Databricks platform while being Postgres under the hood** — so RLS, OLTP latency, SQLAlchemy, and the existing schema/migrations mostly carry over. This is the strongest form of the all-Databricks story: one vendor, no OLTP/RLS sacrifice. See §9.
- **(c) External Postgres (the deviation)** — Azure Database for PostgreSQL Flexible Server, outside the Databricks platform.
  - _Effect:_ the alternative scored against the default in §5. Wins on engine fit; costs a second vendor and a second control plane.

The Axis A matrix in §5 scores the Databricks default as reading **(a) Delta-via-SQL-warehouse** against the **(c) external-Postgres** deviation, because that is the genuinely different architecture. If the team's Databricks-serving default is **(b) Lakebase**, jump to §9 — most of the §5 contest dissolves because the default *is already* Postgres.

## 3. Decision criteria

Drawn from the existing design priorities (`project-horizons-design-priorities`: flexibility > visibility > easy-to-understand), the hard constraints in RFC-3/RFC-4, and the org realities the data-eng team has raised. Each criterion notes where it is sourced and which axis it bears on.

| #   | Criterion                                                                                  | Source                                             | Axis |
| --- | ------------------------------------------------------------------------------------------ | -------------------------------------------------- | ---- |
| C1  | **API read latency** — 3 s p95 for every primitive; per-document lookups sub-100 ms        | RFC-3 §Performance target                          | A    |
| C2  | **Multi-tenant isolation** — two-axis, per-end-user, RLS as a load-bearing layer           | RFC-4 §Multi-tenant; RFC-3 §8                      | A    |
| C3  | **Transactional ingestion** — per-document all-or-nothing poll transaction                 | RFC-4 §Ingestion/How                               | B    |
| C4  | **Reaction latency** — "watch this change land in real time"                               | ADR-0001                                           | B    |
| C5  | **Local-dev ergonomics** — bus-factor-zero, runnable in ~30 s, no external scheduler       | ADR-0001; design priorities                        | B    |
| C6  | **Code robustness/testability** — type-checked, unit + integration tested, code-reviewable | CLAUDE.md (pyright strict, pytest, testcontainers) | A+B  |
| C7  | **Operational complexity** — moving parts, failure modes at 3am                            | ADR-0001 decision drivers                          | A+B  |
| C8  | **Cost** — idle and under load                                                             | ADR-0001 §Cost shape                               | A+B  |
| C9  | **Scale headroom** — low-millions of docs, bursty batch writes                             | RFC-3 §Scale assumptions                           | A+B  |
| C10 | **Future analytics** — cross-corpus near-duplicate clause search over MinHash signatures   | RFC-4 §Out of scope (future)                       | A+B  |
| C11 | **Team familiarity** — who maintains it, and what they already know                        | data-eng team input (new criterion)                | A+B  |
| C12 | **Migration cost** — sunk investment in the shipped stack                                  | current repo state                                 | A+B  |
| C13 | **Config-over-code** — runtime-tunable params surfaced in the UI, no redeploy              | CLAUDE.md; RFC-3 §6; RFC-4                         | A+B  |

Note that **C11 (team familiarity) is the criterion that motivates the Databricks-default posture** and was not weighted in RFC-3/ADR-0001. It is a real and possibly decisive factor; surfacing it explicitly is one of this RFC's contributions. The team must decide where it ranks against the hard constraints (C1, C2).

## 4. Baseline: the all-Databricks default

The default this RFC measures everything against:

- **Serving DB (Axis A):** Delta Lake tables served by a Databricks SQL (serverless) warehouse — reading (a) — or **Lakebase** (managed Postgres) — reading (b) — both inside the Databricks control plane and Unity Catalog governance.
- **ETL (Axis B):** Databricks Jobs / Delta Live Tables / Spark — scheduled pipelines polling Lawstronaut, parsing, aligning, writing Delta, with lineage / retries / alerting from the managed control plane.
- **Stack properties:** one vendor, one governance model (Unity Catalog), DBU-metered scale-to-zero compute, notebooks / asset bundles / databricks-connect as the dev surface, owned by the data-eng team who already live in it.

**The deviation that already exists** (what we are comparing against the default), for reference:

- **Serving DB:** Azure Database for PostgreSQL **Flexible Server**, currently `Standard_B1ms` Burstable, 32 GB, PG 18 (`infra/modules/postgres-flex.bicep`; prod cuts over to General Purpose + HA). A **managed PaaS on burstable compute**, *not* serverless/scale-to-zero — see C8.
- **ETL:** a long-running asyncio worker (`packages/horizons-ingestion`), one ACA container, `SELECT … FOR UPDATE SKIP LOCKED` claim loop, in-process alignment (shingling/MinHash/DP), per-document Postgres transaction with the blob write kept outside it (RFC-4 §Ingestion/How).
- **Stack properties:** plain Python 3.13 + SQLAlchemy + FastAPI + Alembic, `pyright` strict, `pytest` + testcontainers integration suite, Bicep IaC, OTel + structlog.

---

## 5. Axis A — serving database

**Databricks SQL + Delta tables** (default, reading (a)) vs the **Postgres Flexible Server** deviation. Each criterion lists the default first, then the deviation, then the verdict — the deviation has to win decisively on a high-weight criterion to justify leaving the platform.

**C1 latency — Favours Postgres (hard constraint C1).**

- _Databricks SQL + Delta (default):_ columnar Delta optimised for analytical scans, not point lookups. Photon + caching help; serverless warehouse auto-resume adds seconds of cold-start; per-query overhead is higher. Meeting sub-100 ms point reads at API concurrency is unproven for this workload.
- _Postgres Flexible Server (deviation):_ OLTP engine; B-tree/GIN indexes; single-row and small-range lookups in single-digit ms; built for high-concurrency point reads.

**C2 isolation — Favours Postgres (load-bearing C2).**

- _Databricks SQL + Delta (default):_ Unity Catalog row filters / column masks exist, but key off the _Databricks principal_ (a governance identity), not thousands of API end-users behind one service identity. The RFC-4 per-tenant model does not map cleanly; isolation would move entirely into the app layer, losing the database-side belt-and-braces.
- _Postgres Flexible Server (deviation):_ Postgres **RLS** keyed off `current_setting('app.user_id')` set per connection from the bearer token — exactly the two-axis, per-end-user model in RFC-4. Plus repository layer + lint-ban + multi-user tests.

**C6 testability — Favours Postgres.**

- _Databricks SQL + Delta (default):_ Spark/Delta and Databricks SQL are harder to test hermetically; no testcontainers equivalent; integration tests need a workspace. Type-checking SQL/notebook code is weaker than `pyright` over typed Python.
- _Postgres Flexible Server (deviation):_ testcontainers spins a real PG 18 per test; RLS policies and repo helpers are integration-tested in CI.

**C8 cost (idle) — Favours Databricks (default).**

- _Databricks SQL + Delta (default):_ serverless SQL warehouse **auto-suspends** — genuinely cheaper when the warehouse sits idle for much of the day.
- _Postgres Flexible Server (deviation):_ Burstable B1ms is sub-dollar/day but **always on** (no scale-to-zero).

**C8 cost (load) — Neutral (depends on query mix).**

- _Databricks SQL + Delta (default):_ DBU-metered; can be cheaper or pricier depending on query mix and warehouse sizing; harder to predict.
- _Postgres Flexible Server (deviation):_ predictable instance cost; scale up the SKU.

**C9 scale — Favours Databricks (default), at extreme scale only.**

- _Databricks SQL + Delta (default):_ Delta excels at petabyte scans and large aggregations; the "what changed across the whole corpus in 6 months" query (RFC-3's tight case) is its home turf at extreme scale.
- _Postgres Flexible Server (deviation):_ vertical + read replicas; low-millions of rows is comfortable for PG. Very large analytical scans are not its strength.

**C10 future analytics — Favours Databricks (default).**

- _Databricks SQL + Delta (default):_ lakehouse is the natural home for corpus-wide similarity analytics over persisted MinHash signatures (RFC-4 out-of-scope future).
- _Postgres Flexible Server (deviation):_ cross-corpus MinHash near-duplicate search is awkward in PG at scale.

**C13 config-over-code — Neutral.**

- _Databricks SQL + Delta (default):_ equally achievable; no inherent advantage either way.
- _Postgres Flexible Server (deviation):_ tuning params already live as runtime config surfaced in the UI; no redeploy.

**Axis A summary.** This is the one axis where the Databricks default runs hardest into the product's non-negotiables. The two hard constraints RFC-3/RFC-4 declared load-bearing — **C1 OLTP latency** and **C2 per-tenant RLS** — both favour the Postgres deviation, and neither is a tuning question. The Databricks default's genuine wins (C8 idle cost, C9/C10 analytical scale) are about *analytical* workloads and *future* capabilities, not the product's hot path. The honest read: **Delta-as-serving-DB is the weakest application of the Databricks default**, because the read path is OLTP-shaped, per-tenant, and latency-bound — *unless* future cross-corpus analytics (C10) is reweighted from "out of scope" to "primary," which would be a product-strategy change. If the team wants to stay all-Databricks on Axis A without that reweight, **Lakebase (§9) is the way to do it** — it keeps the platform while honouring C1/C2.

---

## 6. Axis B — ETL / ingestion compute

**Databricks Jobs / Delta Live Tables / Spark** (default) vs the **ACA asyncio worker** deviation.

**C3 transactional poll — Favours ACA worker.**

- _Databricks Jobs / DLT / Spark (default):_ Spark/Delta is not transactional across a multi-step per-row workflow in the same sense; idempotency + Delta MERGE patterns can approximate it but the all-or-nothing per-document guarantee is harder to express.
- _ACA asyncio worker (deviation):_ one Postgres transaction wraps the whole per-document poll (hash, version row, clauses, change_events) — all-or-nothing (RFC-4).

**C4 reaction latency — Favours ACA worker.**

- _Databricks Jobs / DLT / Spark (default):_ Jobs/DLT minimum scheduling granularity is coarse (minutes); the "watch it land in real time" framing weakens — the same argument that rejected the ACA-Job shape in ADR-0001 applies more strongly to Spark.
- _ACA asyncio worker (deviation):_ claim loop ticks ~50 ms; a newly-due row is polled almost immediately (ADR-0001).

**C5 local-dev — Favours ACA worker.**

- _Databricks Jobs / DLT / Spark (default):_ databricks-connect / asset bundles / notebooks; heavier local loop; harder to hit bus-factor-zero in 30 s.
- _ACA asyncio worker (deviation):_ `python -m horizons_ingestion` + ctrl-C; no scheduler, ~30 s to running.

**C6 testability — Favours ACA worker.**

- _Databricks Jobs / DLT / Spark (default):_ Spark jobs are harder to unit-test and type-check; CI needs a workspace or local Spark.
- _ACA asyncio worker (deviation):_ plain async Python; unit-tested + testcontainers integration.

**C7 ops complexity — Favours Databricks (default).**

- _Databricks Jobs / DLT / Spark (default):_ Databricks owns scheduling, retries, lineage, alerting, observability **for free** — a managed control plane is fewer things we build and babysit.
- _ACA asyncio worker (deviation):_ worker owns SIGTERM drain, liveness probe, reconnect (ADR-0001 consequences) — real but small (~16 LOC).

**C8 cost (idle) — Favours Databricks (default).**

- _Databricks Jobs / DLT / Spark (default):_ Jobs scale to zero between runs.
- _ACA asyncio worker (deviation):_ one always-on replica (non-zero idle CPU).

**C9 scale (batch) — Favours Databricks (default).**

- _Databricks Jobs / DLT / Spark (default):_ Spark parallelism is built for large bursty batch ingestion at low-millions-of-docs scale — genuinely its strength if ingestion volume grows far beyond the curated set.
- _ACA asyncio worker (deviation):_ single-container claim loop; horizontally scalable via SKIP-LOCKED but bounded by what one Python process does.

**C11 familiarity — Favours Databricks (default).**

- _Databricks Jobs / DLT / Spark (default):_ the data-eng team is already fluent — they would ship and maintain Databricks pipelines faster and more confidently.
- _ACA asyncio worker (deviation):_ generalist Python; the data-eng team would be learning the worker idioms.

**Axis B summary.** This is the axis where the Databricks default earns its keep — and the contest is genuinely close. The *current* workload (a curated set polled on a cadence, latency-sensitive, per-document ACID, in-process Python alignment) is small and transactional, which is exactly where the ACA deviation's strengths (C3/C4/C5/C6) bite and Spark's (C7/C9) don't yet pay off. **But** the two criteria that motivate the Databricks-default posture both land here: **C11 (the people who maintain it know Databricks)** and **C7 (a managed control plane is less to operate)**. If ingestion volume grows by orders of magnitude (C9), the balance tilts further to the default. The crux: is the current-scale, real-time, transactional shape the *enduring* shape of ingestion, or a temporary one that production volume will outgrow? If enduring, the ACA deviation is justified here; if transient, stay on the default.

---

## 7. Cross-cutting: maintainability and the bus-factor argument

The concern that originally favoured the deviation — *"hard to ship robust maintainable code quickly on Databricks"* — is real **and** double-edged, and a neutral doc must record both edges:

- **For the Databricks default:** "maintainable" is relative to *who maintains it*. If the long-term owners are a **data-engineering team who live in Databricks**, then Databricks pipelines are the *more* maintainable choice **for them**. The bus-factor-zero argument in ADR-0001 assumed a generalist reader who is not the actual owner — so the original docs priced the learning-curve tax against the wrong person.
- **For the deviation:** plain typed Python + SQLAlchemy + pytest/testcontainers is boring, hermetically testable, and code-reviewable with `pyright` strict. "Boring is good" (RFC-3). Maintainable *by a generalist engineer* — which matters if ownership is not, in fact, the data-eng team.

So C11 and C6 pull in opposite directions, and which dominates depends on a fact this RFC cannot settle: **who owns ingestion in production?** Under the Databricks-default posture the presumption is the data-eng team — but that presumption is exactly what the first open question below must confirm.

## 8. Migration cost (C12)

The current deviation (ACA + Postgres) is built, deployed, and e2e-gated. Adopting the Databricks default means net-new work: re-platforming the schema onto Delta (or re-pointing serving at Lakebase), reimplementing the claim loop and alignment in the Databricks idiom, rebuilding the RLS isolation model in the app layer (if Delta) or re-homing it on Lakebase, re-doing the IaC and CI. None of this is captured in the matrices above and all of it is real.

Read the other way: the sunk investment is in the *deviation*, not the default. Sunk cost is not a reason to keep a worse-fit architecture — but it *is* a real one-time cost to debit against the Databricks default's advantages, and it is the single biggest argument for treating the shipped ACA + Postgres stack as a sanctioned deviation rather than something to migrate on principle.

## 9. The all-Databricks serving story: Lakebase

If the Axis A serving default is **Lakebase** (reading (b)) rather than Delta-via-SQL-warehouse, most of the §5 contest dissolves — Lakebase *is* Postgres:

- **C1, C2, C6** largely carry over to the default: OLTP latency, RLS, SQLAlchemy, the existing schema and Alembic migrations, testcontainers — all Postgres-native, now *inside* the Databricks platform.
- The remaining question is no longer engine fit but: **maturity** (Lakebase is newer than Flexible Server), **pricing model** (DBU-adjacent vs Azure instance pricing), and **operational ownership** (one vendor for both axes vs two).
- This is the **most coherent expression of the Databricks-default posture**: Databricks for Axis B (ETL, where the team is fluent and the control plane earns its keep) **+ Lakebase for Axis A** (Postgres engine, so C1/C2 are preserved). It keeps the all-Databricks, one-vendor, one-governance story *without* sacrificing the OLTP/RLS hard constraints — i.e. it gets the C11/C7 wins of the default without paying the C1/C2 cost that Delta-serving incurs.

If Lakebase is on the table, it is the recommended way to honour the Databricks default on both axes. Worth costing seriously.

## 10. Decision

**Left open by design.** This RFC scores the criteria against the Databricks default; it does not weight them. To decide, the team must, in review:

1. Resolve the open questions below (especially ownership and the Delta-vs-Lakebase reading of the serving default).
2. Assign weights to C1–C13 — in particular, rank C11 (team familiarity, the criterion behind the Databricks-default posture) against the hard constraints (C1, C2) and the existing priority order (flexibility > visibility > simplicity).
3. Decide each axis independently. The plausible outcomes are: **all-Databricks via Lakebase** (default honoured on both axes), **Databricks ETL + external Postgres serving** (deviate only on Axis A, where C1/C2 are decisive), or **retain the shipped deviation on both** (if ownership turns out not to be the data-eng team).

Record the outcome as an ADR (`ADR-0002`) once weighted, and align RFC-3 §"Database choice direction" and RFC-4 §Ingestion with the scored rationale, whichever way it lands.

## 11. Open questions

- **Who owns ingestion in production?** The data-eng team (the Databricks-default presumption) or generalist engineers? This is the hinge for C11 vs C6 and may decide Axis B on its own.
- **Does the Databricks serving default mean Delta-via-SQL-warehouse or Lakebase?** Settles whether Axis A is a real engine contest (§5) or already a Postgres story inside the platform (§9).
- **What is the *enduring* ingestion volume?** If it stays near the curated-set scale, C9 never pays off for Spark and the ACA deviation stays defensible. If it grows orders of magnitude, C9 reinforces the default on Axis B.
- **Is cross-corpus analytics (C10) still "out of scope," or is it becoming a product priority?** This is the main lever that would justify the Databricks default even on the OLTP-shaped Axis A.
- **Is one-vendor consolidation an explicit goal?** The Databricks-default posture assumes yes. If the org genuinely wants a single Databricks-centric data platform, that is itself a high-weight criterion and should be stated as one — it is the strongest argument for Lakebase over external Postgres on Axis A.
- **What latency does Databricks SQL actually deliver on this query mix?** The C1 verdict for Delta is reasoned, not measured. A spike (the three primitives against a Delta-backed warehouse at target concurrency, measured against the 3 s p95) would replace assertion with data — the same discipline ADR-0001 used to pick the worker shape. This is the measurement that could rescue the Delta-serving default and remove the need to deviate on Axis A.
