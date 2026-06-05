# WU6.5 — Expand-contract migration policy + PR template

*Session 2026-06-05. Branch `worktree-wu6.4-6.5-6.6-migrations-docs-drift` (Session D).*

Documentation-only. The runbook captures the expand-contract rule
that the WU6.3 deploy pipeline assumes; the PR template surfaces it as
a reviewer-facing checklist so the rule isn't enforced only by the
author's memory.

## What shipped

```
docs/runbooks/migrations.md          The expand-contract rule, RLS
                                     policy ordering, worked example
                                     against watchlists, when single-
                                     deploy is acceptable, full safety
                                     checklist.
.github/pull_request_template.md     New file. Summary, Migrations
                                     checklist (links to the runbook),
                                     Test plan.
```

No Python touched, no Bicep touched. (The runbook references the
`migration-job.bicep` from WU6.4 as the runtime; that module shipped
in the same branch.)

## Architectural decisions reflected

1. **Two-deploy default — and "when you can skip" is an explicit
   short list, not a vague exception.** The runbook spells out the
   three conditions (empty table, no RLS policies affected, no
   existing code path touches the columns) that let a single-deploy
   change be safe. Anything outside that list ships expand-contract.
2. **RLS direction rules.** Tightening reads → deploy before code;
   loosening reads → deploy after; add-only on a new column is
   exempt. The matrix in the runbook is intentionally tight — four
   rows, no prose — so reviewers don't have to read paragraphs to
   apply the rule.
3. **Reviewer checklist in the PR template, not in CONTRIBUTING.md.**
   The PR template is what GitHub actually surfaces inline at PR
   creation. A CONTRIBUTING.md note is invisible until somebody goes
   looking; the template is unmissable.

## Decisions inline that warrant a paper trail

- **Worked example targets `watchlists`, not a fictitious table.**
  The plan instruction called for "a hypothetical column add against
  `watchlists`" — the worked example uses `priority` as that
  hypothetical column. Grounding it on a real table (with real
  existing RLS shape from migration 0005) makes the example useful
  rather than abstract; the policy clause in the example is the same
  shape as `watchlists_owner_select` in 0005, so a reviewer can
  cross-check.
- **Raw-SQL policy pattern, not `alembic_utils.PGPolicy`, in the
  example code.** The locked-in plan §2 names PGPolicy as the
  intended shape, but the actual migration tree under
  `packages/horizons-core/migrations/versions/` uses `op.execute()`
  with raw CREATE POLICY SQL (see `0005_rls_spine.py`). The runbook's
  worked example matches reality and adds a single paragraph noting
  that the shipping order rule is identical under both forms — so
  when PGPolicy adoption lands, the runbook stays correct without an
  edit.
- **Counter-example is part of the doc, not a separate file.** A
  reader who skips straight to "what goes wrong if I get this
  wrong" needs to see the failure mode in the same scroll as the
  right answer. Splitting it into a separate "anti-patterns.md"
  hides it behind a click and people don't click.
- **No "migration approvers" section.** The plan's WU6.5
  acceptance criterion specifically asks for a reviewer checklist,
  not a separate approver-quorum rule. Demo-scale this is overkill;
  the checklist in the PR template is the gate.
- **PR template is the only one.** Some repos ship multiple
  templates under `.github/PULL_REQUEST_TEMPLATE/` for different
  PR types and let the author pick by query string. Single template
  with a "delete if not applicable" comment is enough at the demo
  scale and keeps every PR a consistent shape.

## Things deliberately deferred

- **CI-side enforcement of the migrations checklist.** A workflow
  that fails the PR if the migrations-touched && checklist-unchecked
  invariant doesn't hold is a follow-up; right now reviewer
  diligence is the gate.
- **Markdown lint on the runbook.** The repo doesn't have a markdown
  linter wired into pre-commit yet — when one lands (probably as
  part of a docs-quality WU), the runbook will pass through it.
- **Doc index update.** `CLAUDE.md`'s "Read first" list references
  the numbered design-doc chain by name; runbooks aren't yet a
  category in that list. Adding a "Runbooks" sub-section is cheap
  but is post-WU6.5 housekeeping rather than part of this WU.

## Verification gate

This WU touches only Markdown — the standard Python sweep doesn't
exercise these files. Manual checks:

```bash
# Files exist where expected.
ls docs/runbooks/migrations.md .github/pull_request_template.md
# Internal links resolve.
grep -n "migrations.md\|migration-job.bicep\|0005_rls_spine\|0009_watchlist" \
    docs/runbooks/migrations.md .github/pull_request_template.md
# Pre-commit hooks for trailing whitespace / EOL fixer / etc.
uv run pre-commit run --files docs/runbooks/migrations.md \
                              .github/pull_request_template.md
```

End-of-session full sweep runs in
`journal/260605-wu66-drift-check-workflow.md`.

## External verification

None. Documentation has no runtime to exercise. The first real
exercise of the rule is the first migration PR that uses the
template, which lands whenever WU3.x or WU4.x next touches the
schema.

## Next pickup

WU6.6 (drift-check workflow) is next in this session. Beyond this
WU, WU3.2+ migrations and any RLS-touching WU should reference this
runbook in their journal entry and tick the PR-template checkboxes.
