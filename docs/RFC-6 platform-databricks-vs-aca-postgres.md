# Platform direction: Databricks as the default, and where to change

*Last revised: 2026-06-09.*
*Path: docs/RFC-6 platform-databricks-vs-aca-postgres.md.*

A decision matrix that takes **Databricks as the baseline platform** — the default "go-to" for both the ingestion (ETL) pipeline and the serving database — and asks, criterion by criterion, **where (if anywhere) a non-Databricks alternative is strong enough to justify a change.** This RFC does **not** pick a winner. It fixes the criteria, scores the alternatives honestly against the Databricks default, and surfaces the open questions the team must answer before weighting the criteria and deciding. The weighting *is* the decision; this doc supplies the inputs to it.

## Executive summary

**Context.** Two production choices are in play: the database the public API serves from, and the compute that ingests, parses, and aligns legal documents. Whoever owns this owns the **full lifecycle** — build, test, ship, deploy, operate. The presumptive owner is a **data-engineering team**: fluent in Databricks. So **Databricks is the assumed default**, one-vendor consolidation on it is the presumptive org direction, and any non-Databricks choice has to earn its place against it.

**Scope.** This is the *production direction*, not an immediate re-platform. The decision splits into two separable axes, scored independently: **Axis A** — the serving database (high-concurrency, latency-bound reads with strict per-tenant isolation); **Axis B** — the ETL/ingestion compute. Conflating them is the most likely route to a wrong answer. Everything below is judged against two **hard constraints** — *C1, OLTP-shaped serving latency*, and *C2, two-axis per-tenant isolation* — which the **Background** section defines in full before any later section relies on them.

**What the alternative actually runs on — containers, not Kubernetes.** The alternative to Databricks here is **containerised services**: a Docker image run on the Azure Container Apps' managed runtime — **not a self-run Kubernetes cluster.** The operational ladder runs: Databricks managed control plane → containerised service (Docker / ACA) → self-run cluster (AKS / full K8s). The alternative sits on the **middle rung**; it does not ask the team to run a cluster, control plane, networking, or operators — that K8s tier is explicitly out of scope. Pricing the alternative as if it were Kubernetes overstates its operational cost; pricing it as if it were as hands-off as Databricks understates it. The honest framing is: one rung more ops than Databricks, one rung less than AKS.

This RFC considers 11 constraints (C1 - C11). It evaluates the platform direction in the context of each constraint individually and then summarizes.

**Balance:**

- **Axis A** is where the Databricks default runs hardest into the product's non-negotiables. The two hard constraints — **C1 OLTP latency** and **C2 per-tenant RLS** — both favour Postgres, and neither is a tuning question; Delta-as-serving-DB is the weakest application of the default. To stay all-Databricks here without a product-strategy change, **Lakebase** (Databricks' managed Postgres) honours C1/C2 while keeping one vendor (§9).
- **Axis B** is the genuinely close contest. The *current* workload — a curated set, real-time, per-document-transactional, in-process Python alignment — suits the ACA worker (C3/C4/C5/C6). The default's wins (**C7** managed ops, **C11** team familiarity, **C9** batch scale) grow if ingestion volume grows orders of magnitude.
- **The decisive inputs are not technical scores but facts the team must supply:** who owns ingestion in production, whether the serving default means Delta or Lakebase, the enduring ingestion volume, and whether cross-corpus analytics (C10) becomes a product priority (§11).

**What this RFC does and doesn't do.** It fixes the criteria (C1–C13), scores each alternative honestly against the Databricks default, and surfaces the open questions. It deliberately **does not weight the criteria or pick a winner** — the weighting is the decision, and it belongs to the team in review, recorded afterward as a dated decision record (§10).

## Background: the product and its two hard constraints

This section makes the document self-contained: it states what the system does and defines the two non-negotiable constraints (**C1** and **C2**) that the rest of the document treats as fixed. Read it before the scoring sections.

**The product.** This is a regulatory-change intelligence service for large multinational banks. It watches public legal sources, ingests legal documents (laws, regulations, official guidance), and alerts customers to *upcoming* changes — text that has been published but has not yet taken effect — so customers have lead time to prepare. Each document is parsed into a tree of **clauses** (Part / Section / sub-section structure); successive **versions** of a document are **aligned** clause-by-clause so the system can show *which clause changed and how*. 

Our customers reach the system through a single **public (secured) REST API** that answers three query shapes ("the three primitives"): 

- **discovery** (what exists in my scope), 
- **temporal** (what changed, and when), 
- **differential** (how did this clause change between two versions). 

A separate **ingestion worker** polls an upstream legal-data API ("Lawstronaut"), parses each document into clauses, aligns it against the prior version, and writes the result. The API and the worker share one database but run as separate services.

Two of the product's requirements are **hard constraints** — not tuning knobs, and any candidate platform must satisfy both. They recur throughout as **C1** and **C2**, so they are defined here, once, before they are used.

### Constraint C1 — OLTP-shaped, latency-bound serving

The public API serves **interactive** reads, and two database access patterns matter. The industry shorthand is worth pinning down because the platform choice turns on it:

- **OLTP (online transactional processing)** — many small, indexed, low-latency operations at high concurrency: "fetch this document", "list the changes in my scope this week", single-row and small-range lookups. This is the API's hot path.
- **OLAP (online analytical processing)** — a few large scans or aggregations over the whole corpus: "what changed across every jurisdiction in the last six months". Rarer, and latency-tolerant.

The serving budget is **3 s p95 for every API query, with per-document point lookups under ~100 ms**, sustained across many concurrent tenants. That is an **OLTP** profile. A store optimised for OLAP scans can still *answer* these queries — the open question (referred to as **C1** throughout) is whether it can do so *within this latency budget at this concurrency*. "OLTP latency" in this document means exactly this constraint.

### Constraint C2 — two-axis, per-tenant isolation (enforced via RLS)

Every customer ("tenant") is isolated along **two independent axes**, and a breach on either is treated as severe a failure as a data leak:

1. **Cross-client privacy** — a client's private state (watchlists, alerts, saved queries, dashboards, subscriptions) must be invisible to every other client.
2. **Subscription scoping** — each client buys a **subscription**: a set of (jurisdiction × sector) pairs. A client may read only the **corpus** rows inside that subscription. A UK-only client must not see EU change events, even though both live in the same shared corpus.

The enforcement model is **per-end-user**, not per-application: customer end-users sit behind one API service, so isolation must key off the *individual caller*, not a coarse shared service identity. The chosen mechanism is database **Row-Level Security (RLS)** — policies inside the database that filter every row against the current user's identity and subscription, set per connection from the request's bearer token — backed by a repository layer, a lint ban on raw SQL, and multi-user integration tests (defence-in-depth). **"Two-axis per-tenant RLS"** throughout this document refers to this whole arrangement. It maps cleanly onto a row-secured SQL engine (such as Postgres) but *not* onto a warehouse whose access control keys off a governance principal rather than an end-user — the distinction that drives the C2 verdict in §5.

## 1. Status and framing

This is a **structured, team-wide comparison** of the platform options, written from a **Databricks-default posture**: the data-engineering team who will own this in production are fluent in Databricks and most productive on it, and one-vendor consolidation on a Databricks-centric data platform is the presumptive organisational direction. The burden of proof in this doc therefore runs the other way — an alternative has to *earn* its place against the Databricks default, not the reverse.

One honest caveat up front: **a non-Databricks stack already exists.** A working ACA + Postgres implementation is built, deployed to a non-production environment, and gated by an end-to-end test suite. That establishes one viable alternative — it does not establish the platform direction. Two earlier design decisions touched the choice only in passing:

- An earlier **database-design** decision named PostgreSQL as the default and set the lakehouse aside with a one-line rationale — taken before the data-eng team's input, not a scored comparison.
- An earlier **worker-architecture** decision chose a long-running asyncio container for ingestion, with reaction latency and local-dev ergonomics as the deciding drivers — again scored against a generalist owner, not the data-eng team.

This RFC re-opens both with Databricks as the thing to beat.

**Two things this RFC is *not* about:**

1. **It is not an immediate re-platforming.** This RFC concerns the **production direction**, to be decided deliberately and acted on when the team is ready.
2. **It is not a single decision.** Two genuinely separable axes are in play, and they can be decided independently:
   - **Axis A — the serving database** (what the public API reads from under a 3 s p95 budget).
   - **Axis B — the ETL/ingestion compute** (what polls Lawstronaut, parses, aligns, and writes).

   The all-Databricks default is **Databricks for both axes.** The most likely coherent alternative is to keep the Databricks ETL but serve Axis A from Postgres — so the two are scored separately below. Conflating them is the most likely way to reach a wrong answer.

## 2. Terminology: what "Databricks for serving" means

For Axis B (ETL) the Databricks default is unambiguous — Jobs / Delta Live Tables / Spark. For Axis A (serving) "Databricks" splits into three readings, and which one we mean changes the comparison materially. Pin this down before weighting:

- **(a) Delta tables via SQL warehouse** — columnar Delta Lake tables queried by a Databricks SQL (serverless) warehouse; the classic lakehouse serving path.
  - _Effect:_ this is the substantive contest below. Delta is analytical (OLAP) storage; the open question is whether the Databricks default can serve OLTP-shaped API reads under the latency + isolation constraints, or whether Axis A is the one place a Postgres alternative is warranted.
- **(b) Lakebase** — Databricks' managed transactional **Postgres** (built on the Neon engine Databricks acquired in 2025), positioned as the OLTP companion to the lakehouse.
  - _Effect:_ it stays **inside the Databricks platform while being Postgres under the hood** — so RLS, OLTP latency, SQLAlchemy carry over. This is the strongest form of the all-Databricks story: one vendor, no OLTP/RLS sacrifice. See §9.
- **(c) External Postgres (the alternative)** — Azure Database for PostgreSQL Flexible Server, outside the Databricks platform.
  - _Effect:_ the alternative scored against the default in §5. Wins on engine fit; costs a second vendor and a second control plane.

The Axis A matrix in §5 scores the Databricks default as reading **(a) Delta-via-SQL-warehouse** against the **(c) external-Postgres** alternative, because that is the genuinely different architecture. If the team's Databricks-serving default is **(b) Lakebase**, jump to §9 — most of the §5 contest dissolves because the default *is already* Postgres.

## 3. Decision criteria

Drawn from the project's stated design priorities (**flexibility > visibility > ease-of-understanding**), the two hard constraints defined above (C1, C2), and the organisational realities the data-eng team has raised. Each criterion notes its origin and which axis it bears on.

| #   | Criterion                                                                                  | Origin                                                  | Axis |
| --- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------- | ---- |
| C1  | **API read latency** — 3 s p95 for every query; per-document point lookups sub-100 ms       | Hard constraint C1 (defined above)                      | A    |
| C2  | **Multi-tenant isolation** — two-axis, per-end-user, RLS as a load-bearing layer           | Hard constraint C2 (defined above)                      | A    |
| C3  | **Transactional ingestion** — per-document all-or-nothing poll transaction                 | Ingestion correctness requirement                       | B    |
| C4  | **Reaction latency** — "watch this change land in near-real-time"                          | Product / demo expectation                              | B    |
| C5  | **Local-dev ergonomics** — low bus-factor, runnable in ~30 s, no external scheduler        | Developer-experience priority                           | B    |
| C6  | **Code robustness/testability** — type-checked, unit + integration tested, code-reviewable | Engineering standard (strict typing, containerised tests) | A+B  |
| C7  | **Operational complexity** — moving parts, failure modes at 3am                            | Operability priority                                    | A+B  |
| C8  | **Cost** — idle and under load                                                             | Budget                                                  | A+B  |
| C9  | **Scale headroom** — low-millions of docs, bursty batch writes                             | Scale assumptions                                       | A+B  |
| C10 | **Future analytics** — cross-corpus near-duplicate clause search over MinHash signatures   | Possible future capability (currently out of scope)     | A+B  |
| C11 | **Team familiarity** — who maintains it, and what they already know                        | Data-eng team input                                     | A+B  |
| C12 | **Migration cost** — sunk investment in the already-built stack                            | Current state of the build                              | A+B  |
| C13 | **Config-over-code** — runtime-tunable params surfaced in the UI, no redeploy              | Flexibility priority                                    | A+B  |

## 4. Baseline: the all-Databricks default

The default this RFC measures everything against:

- **Serving DB (Axis A):** Delta Lake tables served by a Databricks SQL (serverless) warehouse — reading (a) — or **Lakebase** (managed Postgres) — reading (b) — both inside the Databricks control plane and Unity Catalog governance.
- **ETL (Axis B):** Databricks Jobs / Delta Live Tables / Spark — scheduled pipelines polling Lawstronaut, parsing, aligning, writing Delta, with lineage / retries / alerting from the managed control plane.
- **Stack properties:** one vendor, one governance model (Unity Catalog), DBU-metered scale-to-zero compute, notebooks / asset bundles / databricks-connect as the dev surface, owned by the data-eng team who already live in it.

**The alternative that already exists** (what we are comparing against the default), for reference:

- **Serving DB:** Azure Database for PostgreSQL **Flexible Server**, currently a `Standard_B1ms` Burstable instance, 32 GB, PG 18 (production would cut over to General Purpose + HA). A **managed PaaS on burstable compute**, *not* serverless/scale-to-zero — see C8.
- **ETL:** a long-running asyncio worker in one ACA container, a `SELECT … FOR UPDATE SKIP LOCKED` claim loop, in-process alignment (shingling / MinHash / dynamic-programming), and a per-document Postgres transaction with the blob write kept outside it.
- **Stack properties:** plain Python 3.13 + SQLAlchemy + FastAPI + Alembic, strict static type-checking, a unit + containerised-integration test suite, Bicep IaC, OpenTelemetry + structured logging.
- **Operational tier:** **containerised services, not Kubernetes.** The same Docker image runs locally under `docker` / Compose and in the cloud on Azure Container Apps' managed runtime — there is no cluster, control plane, networking, or operators to run. ACA is one rung below a self-run AKS / Kubernetes cluster (explicitly out of scope — a full cluster is overkill at this scale) and one rung above Databricks' fully-managed control plane. This is the tier the C7 (ops complexity) and C11 (familiarity) scores below are about: more hands-on than Databricks, far less than Kubernetes.

---

## 5. Axis A — serving database

**Databricks SQL + Delta tables** (default, reading (a)) vs the **Postgres Flexible Server** alternative. Each criterion lists the default first, then the alternative, then the verdict — the alternative has to win decisively on a high-weight criterion to justify leaving the platform.

**C1 latency — Favours Postgres (hard constraint C1).**

- _Databricks SQL + Delta (default):_ columnar Delta optimised for analytical scans, not point lookups. Photon + caching help; serverless warehouse auto-resume adds seconds of cold-start; per-query overhead is higher. Meeting sub-100 ms point reads at API concurrency is unproven for this workload.
- _Postgres Flexible Server (alternative):_ OLTP engine; B-tree/GIN indexes; single-row and small-range lookups in single-digit ms; built for high-concurrency point reads.

**C2 isolation — Favours Postgres (load-bearing C2).**

- _Databricks SQL + Delta (default):_ Unity Catalog row filters / column masks exist, but key off the _Databricks principal_ (a governance identity), not API end-users behind one service identity. The per-tenant model defined in C2 does not map cleanly; isolation would move entirely into the app layer, losing the database-side belt-and-braces.
- _Postgres Flexible Server (alternative):_ Postgres **RLS** keyed off `current_setting('app.user_id')` set per connection from the bearer token — exactly the two-axis, per-end-user model defined in C2. Plus repository layer + lint-ban + multi-user tests.

**C6 testability — Favours Postgres.**

- _Databricks SQL + Delta (default):_ Spark/Delta and Databricks SQL are harder to test hermetically; no testcontainers equivalent; integration tests need a workspace. Type-checking SQL/notebook code is weaker than `pyright` over typed Python.
- _Postgres Flexible Server (alternative):_ testcontainers spins a real PG 18 per test; RLS policies and repo helpers are integration-tested in CI.

**C8 cost (idle) — Favours Databricks (default).**

- _Databricks SQL + Delta (default):_ serverless SQL warehouse **auto-suspends** — genuinely cheaper when the warehouse sits idle for much of the day.
- _Postgres Flexible Server (alternative):_ Burstable B1ms is sub-dollar/day but **always on** (no scale-to-zero).

**C8 cost (load) — Neutral (depends on query mix).**

- _Databricks SQL + Delta (default):_ DBU-metered; can be cheaper or pricier depending on query mix and warehouse sizing; harder to predict.
- _Postgres Flexible Server (alternative):_ predictable instance cost; scale up the SKU.

**C9 scale — Favours Databricks (default), at extreme scale only.**

- _Databricks SQL + Delta (default):_ Delta excels at petabyte scans and large aggregations; the "what changed across the whole corpus in 6 months" query (the corpus-wide analytical case) is its home turf at extreme scale.
- _Postgres Flexible Server (alternative):_ vertical + read replicas; low-millions of rows is comfortable for PG. Very large analytical scans are not its strength.

**C10 future analytics — Favours Databricks (default).**

- _Databricks SQL + Delta (default):_ lakehouse is the natural home for corpus-wide similarity analytics over persisted MinHash signatures (currently out of scope; see C10).
- _Postgres Flexible Server (alternative):_ cross-corpus MinHash near-duplicate search is awkward in PG at scale.

**C13 config-over-code — Neutral.**

- _Databricks SQL + Delta (default):_ equally achievable; no inherent advantage either way.
- _Postgres Flexible Server (alternative):_ tuning params already live as runtime config surfaced in the UI; no redeploy.

**Axis A summary.** This is the one axis where the Databricks default runs hardest into the product's non-negotiables. The two hard constraints defined up front — **C1 OLTP latency** and **C2 per-tenant RLS** — both favour the Postgres alternative, and neither is a tuning question. The Databricks default's genuine wins (C8 idle cost, C9/C10 analytical scale) are about *analytical* workloads and *future* capabilities, not the product's hot path. The honest read: **Delta-as-serving-DB is the weakest application of the Databricks default**, because the read path is OLTP-shaped, per-tenant, and latency-bound — *unless* future cross-corpus analytics (C10) is reweighted from "out of scope" to "primary," which would be a product-strategy change. If the team wants to stay all-Databricks on Axis A without that reweight, **Lakebase (§9) is the way to do it** — it keeps the platform while honouring C1/C2.

---

## 6. Axis B — ETL / ingestion compute

**Databricks Jobs / Delta Live Tables / Spark** (default) vs the **ACA asyncio worker** alternative.

**C3 transactional poll — Favours ACA worker.**

- _Databricks Jobs / DLT / Spark (default):_ Spark/Delta is not transactional across a multi-step per-row workflow in the same sense; idempotency + Delta MERGE patterns can approximate it but the all-or-nothing per-document guarantee is harder to express.
- _ACA asyncio worker (alternative):_ one Postgres transaction wraps the whole per-document poll — the new version row, its parsed `clauses`, and the `change_events` that record what moved since the prior version all commit together, or none of them do. This atomicity (the **A** in ACID) is load-bearing for a change-tracking product, not a nicety. Each poll is a multi-row write across linked tables, and the corpus's entire value is an accurate, gap-free history of *what changed and when*. Without an all-or-nothing guarantee, a crash, timeout, or mid-write retry can leave the corpus in a partial state that is silently wrong and expensive to detect: a version row with no clauses (a phantom version), clauses with no `change_events` (a change that happened but was never recorded), or `change_events` referencing a version that never fully landed (a change attributed to nothing). A retry that re-runs a half-applied poll is worse still — it can write the same `change_events` twice, double-counting a single change. Any of these corrupts the temporal and differential answers the product exists to give; and because each new version is aligned against the *prior stored state*, one corrupt poll propagates forward into every future diff of that document, so the error compounds rather than washing out. Transactional **isolation** (the **I** in ACID) adds the second guarantee: concurrent polls of different documents cannot interleave into a half-updated read. Postgres provides both per transaction with no extra code; approximating them on Spark/Delta via idempotency keys + MERGE is possible, but it is hand-built correctness the database would otherwise enforce for free.

**C4 reaction latency — Favours ACA worker.**

- _Databricks Jobs / DLT / Spark (default):_ Jobs/DLT minimum scheduling granularity is coarse (minutes); the "watch it land in real time" framing weakens — the same argument that earlier rejected a scheduled-job shape for the worker applies more strongly to Spark.
- _ACA asyncio worker (alternative):_ claim loop ticks ~50 ms; a newly-due row is polled almost immediately.

**C5 local-dev — Favours ACA worker.**

> *Bus-factor* is the number of people who would have to be lost ("hit by a bus") before no one is left who can run or maintain the system — the standard measure of single-person dependency. **Bus-factor-zero** is the target state used here: the setup needs *no particular person's* knowledge to run, so losing any individual costs nothing. Concretely, any engineer can clone the repo cold and have the service running in ~30 s, with no tribal knowledge, no shared cloud workspace, and no scheduler to provision.

- _Databricks Jobs / DLT / Spark (default):_ databricks-connect / asset bundles / notebooks; heavier local loop; harder to hit bus-factor-zero in 30 s.
- _ACA asyncio worker (alternative):_ `python -m horizons_ingestion` + ctrl-C; no scheduler, ~30 s to running.

**C6 testability — Favours ACA worker.**

- _Databricks Jobs / DLT / Spark (default):_ Spark jobs are harder to unit-test and type-check; CI needs a workspace or local Spark.
- _ACA asyncio worker (alternative):_ plain async Python; unit-tested + testcontainers integration.

**C7 ops complexity — Favours Databricks (default).**

- _Databricks Jobs / DLT / Spark (default):_ Databricks owns scheduling, retries, lineage, alerting, observability **for free** — a managed control plane is fewer things we build and babysit.
- _ACA asyncio worker (alternative):_ worker owns SIGTERM drain, liveness probe, reconnect — real but small (~16 LOC).

**C8 cost (idle) — Favours Databricks (default).**

- _Databricks Jobs / DLT / Spark (default):_ Jobs scale to zero between runs.
- _ACA asyncio worker (alternative):_ one always-on replica (non-zero idle CPU).

**C9 scale (batch) — Favours Databricks (default).**

- _Databricks Jobs / DLT / Spark (default):_ Spark parallelism is built for large bursty batch ingestion at low-millions-of-docs scale — genuinely its strength if ingestion volume grows far beyond the curated set.
- _ACA asyncio worker (alternative):_ single-container claim loop; horizontally scalable via SKIP-LOCKED but bounded by what one Python process does. That bound costs little at the current cadence, though: ingestion runs on a slack schedule (e.g. a daily sweep), not a tight throughput SLA, and the product sells *lead time* on changes that are months from taking effect — so the wall-clock length of a run is not on any hot path. If the loop runs once a day, a process that takes an hour instead of ten minutes does not materially change what the customer sees. The C9 win is real but low-*value* until ingestion volume or cadence shifts the picture — which is exactly the open question the Axis B summary turns on.

**C11 familiarity — Favours Databricks (default).**

- _Databricks Jobs / DLT / Spark (default):_ the data-eng team is already fluent — they would ship and maintain Databricks pipelines faster and more confidently.
- _ACA asyncio worker (alternative):_ generalist Python; the data-eng team would be learning the worker idioms.

**Axis B summary.** This is the axis where the Databricks default earns its keep — the contest is close. The *current* workload (a curated set polled on a cadence, latency-sensitive, per-document ACID, in-process Python alignment) is small and transactional, which is exactly where the ACA alternative's strengths (C3/C4/C5/C6) bite and Spark's (C7/C9) don't yet pay off. **But** the two criteria that motivate the Databricks-default posture both land here: **C11 (the people who maintain it know Databricks)** and **C7 (a managed control plane is less to operate)**. As ingestion volume grows (C9), the balance tilts further to the default — and production volume *will* outgrow the demo set, realistically to at least ~10⁴ documents and beyond (§11). So the crux is not *whether* it grows but *how far and how fast*: at the low end of the expected band (~10⁴) one Python process still copes comfortably and the ACA alternative holds; toward the high end (10⁵–10⁶) Spark's batch parallelism pays off and the default gains. Where steady-state volume and cadence settle inside that band is what decides this axis.

**How much headroom is there, and can supply fill it?** Worth sizing, because the verdict hinges on where in the band ingestion lands. What the worker ingests *today* is the curated set — a few dozen documents (≈44 captured fixtures across ≈31 jurisdictions, plus a handful of staged second versions): order 10¹–10². The platform's stated scale assumption (C9) is "low-millions," i.e. order 10⁶. So one order of magnitude is hundreds of documents, four is hundreds of thousands — roughly **five orders of magnitude of headroom** between the demo corpus and the design ceiling. The question is whether upstream supply could ever fill it. 

It can, comfortably: Lawstronaut (the API arm of filerskeepers) draws on a catalogue its public materials describe as spanning **221 countries and 324-plus jurisdictions**, and filerskeepers alone exposes **270,000-plus *data-retention obligations*** — and that is one narrow legal domain. The full population of laws, regulations, caselaw, and guidance across those jurisdictions is plausibly **10⁵–10⁶ documents** (a single Netherlands query for one statute already returned 135–838 metadata records). The upstream catalogue therefore exceeds current ingest by three to five orders of magnitude: **supply is not the binding constraint.**

What *is* binding is not availability but **how many sources the operator chooses to watch** — which tracks commercial demand, since the corpus is populated to match what the business can sell, not the whole catalogue. Ingestion volume scales with the breadth of jurisdictions × sectors put under watch, not with Lawstronaut's total size. So the relevant question is not *whether* ingestion grows but *how far*: putting even a few hundred jurisdiction × sector cells under watch would push ingestion two to three orders past the demo set, and production is expected to reach at least ~10⁴ documents (§11). Commercial breadth sets the pace and how far up the 10⁴–10⁶ band it climbs — but the direction is not in doubt. That is the "enduring ingestion volume" open question (§11) on which this axis turns: the upstream corpus is comfortably large enough for Spark's C9 advantage to matter; the only question is how soon the watched-source breadth gets there.

---

## 7. Cross-cutting: maintainability and the bus-factor argument

The concern that originally favoured the alternative — *"hard to ship robust maintainable code quickly on Databricks"* — is real **and** double-edged, and a neutral doc must record both edges:

- **For the Databricks default:** "maintainable" is relative to *who maintains it*. If the long-term owners are a **data-engineering team who live in Databricks**, then Databricks pipelines are the *more* maintainable choice **for them**. The bus-factor-zero argument behind the original worker decision assumed a generalist reader who is not the actual owner — so it priced the learning-curve tax against the wrong person.
- **For the alternative:** plain typed Python + SQLAlchemy + a containerised integration suite is boring, hermetically testable, and code-reviewable under strict static typing. Boring is good. Maintainable *by a generalist engineer* — which matters if ownership is not, in fact, the data-eng team.

So C11 and C6 pull in opposite directions, and which dominates depends on a fact this RFC cannot settle: **who owns ingestion in production?** Under the Databricks-default posture the presumption is the data-eng team — but that presumption is exactly what the first open question below must confirm.

## 8. Migration cost (C12)

The current alternative (ACA + Postgres) is built, deployed, and e2e-gated. Adopting the Databricks default means net-new work. None of this is captured in the matrices above.

## 9. The all-Databricks serving story: Lakebase

If the Axis A serving default is **Lakebase** (reading (b)) rather than Delta-via-SQL-warehouse, most of the §5 contest dissolves — Lakebase *is* Postgres:

- **C1, C2, C6** largely carry over to the default: OLTP latency, RLS, SQLAlchemy, the existing schema and Alembic migrations, testcontainers — all Postgres-native, now *inside* the Databricks platform.
- The remaining question is no longer engine fit but: **maturity** (Lakebase is newer than Flexible Server), **pricing model** (DBU-adjacent vs Azure instance pricing), and **operational ownership** (one vendor for both axes vs two).
- This is the **most coherent expression of the Databricks-default posture**: Databricks for Axis B (ETL, where the team is fluent and the control plane earns its keep) **+ Lakebase for Axis A** (Postgres engine, so C1/C2 are preserved). It keeps the all-Databricks, one-vendor, one-governance story *without* sacrificing the OLTP/RLS hard constraints — i.e. it gets the C11/C7 wins of the default without paying the C1/C2 cost that Delta-serving incurs.

If Lakebase is on the table, it is a good way to honour the Databricks default on both axes. Worth costing seriously.

## 10. Decision

**Left open by design.** This RFC scores the criteria against the Databricks default; it does not weight them. To decide, the team must, in review:

1. Resolve the open questions below (especially ownership and the Delta-vs-Lakebase reading of the serving default).
2. Assign weights to C1–C13 — in particular, rank C11 (team familiarity, the criterion behind the Databricks-default posture) against the hard constraints (C1, C2) and the existing priority order (flexibility > visibility > simplicity).
3. Decide each axis independently. The plausible outcomes are: **all-Databricks via Lakebase** (default honoured on both axes), **Databricks ETL + external Postgres serving** (change only on Axis A, where C1/C2 are decisive), or **retain the shipped alternative on both** (if ownership turns out not to be the data-eng team).

Record the outcome as a dated architecture decision record once weighted, and reconcile the earlier database-design and worker-architecture decisions with the scored rationale, whichever way it lands.

## 11. Open questions

- **Who owns ingestion in production?** The data-eng team (the Databricks-default presumption) or generalist engineers? This is the hinge for C11 vs C6 and may decide Axis B on its own.
- **Does the Databricks serving default mean Delta-via-SQL-warehouse or Lakebase?** Settles whether Axis A is a real engine contest (§5) or already a Postgres story inside the platform (§9).
- **What is the *enduring* ingestion volume?** It will be **far larger than the demo sample** — realistically at least ~10,000 documents and growing, upper-bounded by Lawstronaut's own catalogue (≈10⁵–10⁶ documents; see the sizing in §6). So the "stays at curated-set scale" case is effectively off the table; the live question is *where in the 10⁴–10⁶ band* it settles and at what re-poll cadence. That band straddles the point where C9 flips: at the low end (~10⁴) one Python process still copes comfortably and the ACA alternative holds; toward the high end (10⁵–10⁶) Spark's batch parallelism increasingly pays off and the default gains. Pinning the expected steady-state volume and cadence is what resolves C9 for Axis B.
- **Is cross-corpus analytics (C10) still "out of scope," or is it becoming a product priority?** This is the main lever that would justify the Databricks default even on the OLTP-shaped Axis A.
- **Is one-vendor consolidation an explicit goal?** The Databricks-default posture assumes yes. If the org genuinely wants a single Databricks-centric data platform, that is itself a high-weight criterion and should be stated as one — it is the strongest argument for Lakebase over external Postgres on Axis A.
- **What latency does Databricks SQL actually deliver on this query mix?** The C1 verdict for Delta is reasoned, not measured. A spike (the three primitives against a Delta-backed warehouse at target concurrency, measured against the 3 s p95) would replace assertion with data — the same measure-don't-assert discipline used to pick the worker shape originally. This is the measurement that could rescue the Delta-serving default and remove the need to change on Axis A.
