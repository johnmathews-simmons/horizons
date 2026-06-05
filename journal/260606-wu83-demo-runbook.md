# 2026-06-06 — WU8.3: demo runbook

Worktree `wu8.3-demo-runbook`. Scope: write `docs/runbooks/demo.md`
covering the pre-demo checklist, the live demo script, recovery
steps, and the public-exposure caveats. Pure documentation work —
no code, no infra, no webapp, no other runbooks touched.

The runbook is the operator's single source of truth for the
2026-06-08 showcase. Demo-accounts.md covers provisioning; deploy.md
covers `deploy.yml` mechanics; this runbook covers *the showcase
itself* — what to do before, during, and when things go sideways.

## Structure of the runbook

```
docs/runbooks/demo.md
├── Header + companion-runbooks index
├── 1. Pre-demo checklist
│      9 independently verifiable boxes:
│        - Azure provider registrations
│        - Bicep what-if clean
│        - First end-to-end deploy via deploy.yml succeeded
│        - Migration ACA Job succeeded, Alembic at head
│        - Curated set seeded against deployed DB (with --stage-synthetic-v2)
│        - Demo accounts provisioned
│        - Playwright e2e green on main (operator pastes most-recent URL)
│        - Front Door endpoint resolves to SPA bundle
│        - Manual login as each demo account end-to-end
├── 2. Demo script
│      a. Setup (browser, projector, API warm-up)
│      b. UK client walk-through (login → /changes → MODIFIED
│         event → side-by-side ↔ unified → MOVED filter)
│      c. EU client → subscription scoping (disjoint /changes)
│      d. Admin view + support view — PLACEHOLDER for WU5.4
│         (skeleton 7-step beat, Session Q fills in wording)
│      e. Wrap (logout, terminal /login state)
├── 3. Recovery steps
│      6 failure modes, each as symptom → quick check → recovery:
│        - API cold-start
│        - Front Door cache stale after a redeploy
│        - Expired Lawstronaut token (note: not user-visible)
│        - Admin demo account locked out
│        - DB / API saturation
│        - Browser cache shows stale SPA bundle
└── 4. Public-exposure caveats
       Verbatim CLAUDE.md constraint + 5 operational guidelines:
         - pre-exposure grep for firm/bank names
         - curated-set sanity vs. demo memory
         - named-entity deflection script
         - claim discipline ("near-weekly" not "real-time")
         - closing the window (password rotation, FD disable)
```

## WU5.4 follow-up commit needed

Section 2.d ("Admin view + support view") contains a structural
skeleton with all the talking-point order locked in, but the exact
UI path strings, label wording, and audit-log columns are
Session Q's deliverable. The placeholder is marked with an HTML
comment for grep-ability — search the runbook for the literal
string:

```
<!-- WU5.4 follow-up: fill in from
     journal/260606-wu54-admin-views-support-view.md once Session Q
     merges. Keep the structural skeleton below; update the wording
     in-place once the exact UI paths and label strings are known. -->
```

When Session Q merges, the follow-up commit:

1. Reads `journal/260606-wu54-admin-views-support-view.md` to learn
   the actual route paths (`/admin/clients`, `/admin/audit`), the
   amber-banner copy string, and the tab-title prefix.
2. Updates the 7 numbered steps in section 2.d in place — the
   *structure* (admin login → list → click client → enter support
   view → amber banner → tab title prefix → exit → audit log) does
   not change.
3. Removes the HTML-comment placeholder.

The follow-up should not invent UI flow. If Session Q's journal
diverges from the skeleton's ordering (e.g. the audit log is
reached from a different surface), the WU8.3 author of record
should be looped in before rewriting the structure.

## Recovery scenarios considered but excluded

This runbook is bounded, not exhaustive. The following were
considered and dropped, with the reasoning written down so a
future on-call person knows the scope:

1. **Postgres replica failover.** Not in scope — the deployed
   Postgres is a single Flexible Server instance per the WU6 infra
   plan. If the DB falls over, the demo is over; there is no
   sub-minute recovery to document. Post-demo work item: stand up
   a read replica + document the failover, but only if Horizons
   moves past the demo into a productionised service.
2. **GitHub Actions outage during the demo window.** Out of the
   operator's control. The deployed state at demo-time is what
   matters; CI being down does not affect the running services.
   If GHA is the source of a missing late-breaking fix, the
   honest answer is "we'll fix it after the window closes".
3. **Lawstronaut API outage during the demo.** Same shape as the
   "expired token" entry — the demo path reads from already-
   persisted corpus rows, so a Lawstronaut outage during the show
   has no user-visible impact. Worker logs would show 5xx; the
   demo continues. Not worth a separate recovery entry.
4. **Audience demand to see a non-curated document.** Deliberate
   omission. The demo flows through the curated set for a reason
   (controlled diff, controlled subscription scope, controlled
   public-safety). If asked "can you show me X?", the deflection
   under public-exposure caveats applies; do not attempt an
   ad-hoc query path the operator has not rehearsed.
5. **Mid-demo deploy.** Explicitly out of scope. The pre-demo
   checklist gates the deploy; once the audience is in the room,
   `deploy.yml` does not run. If a critical bug surfaces mid-demo,
   the answer is "we'll fix it after the showcase" — a blue/green
   shift mid-show is the kind of risk the demo discipline exists
   to avoid.
6. **JWT signing key rotation.** No path to do this safely during
   the 1–2 day public window — every existing access token would
   be invalidated. Rotation is a planned post-demo activity.
7. **TLS cert expiry on Front Door.** Front Door Standard manages
   the apex cert automatically; expiry during a 1–2 day window
   would require the cert to have been on the verge of expiry
   before the deploy. The Bicep what-if check in the pre-demo
   checklist would not catch this directly; a separate "cert TTL
   > 30 days" check could be added as a follow-up if a future
   showcase has a longer window.

The runbook covers the scenarios an operator can plausibly recover
from in seconds-to-minutes during the show. Everything else is
either a "demo's over" outcome or a post-demo work item.

## Cross-references

- [docs/runbooks/demo.md](../docs/runbooks/demo.md) — the runbook
  itself.
- [docs/runbooks/demo-accounts.md](../docs/runbooks/demo-accounts.md) —
  account provisioning, pre-demo checklist for the accounts.
- [docs/runbooks/deploy.md](../docs/runbooks/deploy.md) — `deploy.yml`
  mechanics; this runbook treats it as a black box.
- [docs/runbooks/migrations.md](../docs/runbooks/migrations.md) —
  expand-contract rule for the migration ACA Job.
- [journal/260605-fix-worker-staged-guard-and-env-validation.md](./260605-fix-worker-staged-guard-and-env-validation.md) —
  the `next_poll_at = 2026-12-31` staging guard referenced from the
  pre-demo checklist.
- [journal/260605-wu82-hotfix-e2e-cors.md](./260605-wu82-hotfix-e2e-cors.md) —
  the five-bug saga whose Bugs 4 + 5 (cold-bootstrap refresh + skip
  Authorization on auth endpoints) are silently load-bearing for
  the demo's cookie auth flow.
- [.claude/memory/project_horizons_demo_2026_06_08.md](../.claude/memory/project_horizons_demo_2026_06_08.md) —
  audience, public exposure, claim discipline; the source of
  truth this runbook's section 4 derives from.

## Other-runbook errors noticed (deferred)

Per the prompt: "if you notice an error in another runbook, surface
in the journal as a follow-up — do not fix it in this branch."

None found. `demo-accounts.md` and `deploy.md` cross-referenced
cleanly; the section anchors used in `demo.md` (`#provisioning`,
`#curl-sanity-check-before-the-demo`, `#reset-between-dry-runs`,
`#deploy-spa`, `#prerequisites-that-must-exist-before-the-first-deploy`,
`#what-a-healthy-run-looks-like`) all resolve.

## Cadence note

Worktree `wu8.3-demo-runbook`. Fast-forward merge into `main` per
[CLAUDE.md → "CI / merge cadence"](../CLAUDE.md#ci--merge-cadence)
after the local sweep is green. No code touched, so the local sweep
is essentially `pre-commit run --all-files` against the two new
markdown files; pytest / pyright / webapp gates have no relevant
input.

## Post-merge follow-ups

1. **WU5.4 placeholder fill-in.** Documented above. Grep-able by
   the HTML-comment string.
2. **Optional: a one-line "demo runbook" entry** at the top of
   the `docs/` README (if/when one is created) so the runbook is
   discoverable from a docs index. Not in scope for this WU.
3. **A `demo-dry-run` checklist tickbox** — operationally useful
   to run section 2 (the script) against the deployed staging
   environment 24 h before showtime as a rehearsal, treating any
   surprise as a pre-demo bug. Not codified here because the
   checklist itself is the rehearsal.
