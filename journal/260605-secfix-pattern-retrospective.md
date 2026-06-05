# Retrospective — secfix commit pattern during the 2026-06-05 build sprint

*Session 2026-06-05 (late evening). Not a work-unit entry — a meta-observation
captured for review after the 2026-06-08 demo.*

Across today's parallel-session build-out (Sessions A–O plus a handful of
solo WUs), six commits explicitly labelled "secfix" or with security-fix
intent landed as follow-ups to their parent WUs. This entry catalogues
them, asks whether the pattern is healthy, and proposes targeted prompt
changes for future work.

## The six secfix events

| Secfix commit(s) | Parent WU | Severity | Category |
|---|---|---|---|
| `2537537` enforce token kind at auth boundary | WU4.1 | Real auth bypass risk | Access/refresh/impersonation token-type conflation |
| `165773d` cookie-source binding + argon2 on miss + role re-read | WU4.2 | Real (3 issues) | Refresh-source confusion, timing leak, stale role propagation |
| `7ea4416` + `017ef3f` + `23a238f` open-redirect sanitisation (3 iterations) | WU5.0 | Real | URL sanitisation: input check → URL-parser whitespace bypass → output-side check |
| `3163e49` align active_scope_documents with current_scope() + server-side soft-delete clock | WU4.5 | Real defence-in-depth divergence | Reduction path used a different scope query than RLS |
| `47379bf` idempotence + smoke-test hardening | WU8.2 | Low (test infra hygiene) | E2E fixture cleanup |
| `d7c6d91` demo-account password handling | WU8.1 | Real | Credential storage in demo CLI |

Five of six are real security issues. One is hygiene. **Every issue was
caught before main merged** — none reached production or even a feature
branch's lifetime.

## Is this pattern good or bad?

**Mostly good, with one yellow flag.**

Good signals:
- Six issues across ~50 WUs is ~12% — not alarming for the surface areas
  touched (auth, RLS, redirect handling, defence-in-depth).
- Every issue was caught pre-merge.
- The categories are well-known OWASP-class — the engineering-team
  skill's review pass *recognises* the canonical risks.
- The fix commits are small and well-scoped — they don't rewrite
  anything, suggesting initial implementations were mostly right and
  the review was surgical.

The yellow flag:
- **WU5.0's three-iteration sequence on the same surface.** First commit
  added a sanitiser; second commit fixed a URL-parser whitespace bypass
  *in the sanitiser*; third commit added an output-side check. A review
  pass that needed three iterations to clear the surface is evidence
  the review itself is incremental, not exhaustive. The outcome was
  fine; the cost was noisier commit history.

## What this tells us about the workflow

- The **primary implementer** in the engineering-team skill writes code
  that is mostly correct but not security-first. Default mode is
  happy-path correctness.
- The **review pass** catches the well-known categories well but isn't
  exhaustive on a single iteration.
- Iteration *within* a secfix (WU5.0 case) indicates the review-step is
  itself iterative rather than comprehensive.
- Aggregate quality is high because review-and-fix lands before merge.
  Commit-history noise is the cost.

## Recommended prompt changes (targeted, not generic)

### Change 1 — pre-flight adversary framing for high-risk surfaces

Replace generic "implement secure X" guidance with named-adversary
framing. Example for redirect-handling surfaces:

> Approach the redirect parameter as an attacker trying to bypass your
> sanitisation. What classes of payloads (whitespace bypass, unicode
> normalisation, scheme confusion, IDN homograph, protocol-relative
> URLs) does your implementation block? Document each bypass class you
> considered and how it's handled.

Named-adversary framing has been shown empirically to produce more
thorough work than abstract "be secure" or "follow OWASP" instructions.
Apply ONLY to surfaces with known-high security risk:

- Authentication flows (token issuance, refresh, logout)
- Redirect parameters in any URL-returning flow
- RLS / defence-in-depth implementations
- Password storage / credential handling
- Admin impersonation paths

### Change 2 — explicit second-review constraint inside the implementation

Add to high-risk prompts:

> Before declaring done, you MUST do a self-review specifically asking:
> "would a second-pass adversarial review find a missed case?" If yes,
> fix and re-run the self-review. The session is not complete until a
> second-pass review would find nothing material.

This formalises iteration *before* the secfix commit pattern rather
than after. The WU5.0 three-pass shape would have collapsed to one
pre-merge pass.

## What NOT to change

- **Do not add generic OWASP checklists to every prompt.** Checklists
  get pattern-matched without thought. They produce performative
  compliance, not real review. The current review pass already catches
  the canonical categories; adding a checklist won't help.
- **Do not split out a separate "security agent" pass.** Latency cost
  with marginal benefit — our review-pass is already catching things.
- **Do not tighten unrelated prompts.** Demo data, curated-set bootstrap,
  CI workflow extensions don't need security-first framing. The pattern
  is justified only on auth / redirect / defence-in-depth / credential
  surfaces.
- **Do not change the engineering-team skill itself based on this
  retrospective.** The skill's review pass is working — adjust prompt
  scaffolding around it instead.

## Specific application to remaining work

| WU | Apply Change 1? | Apply Change 2? | Reasoning |
|---|---|---|---|
| WU5.4 (admin views + support view) | **Yes** | **Yes** | Admin impersonation is high-risk; support-view banner + audit-log surface need defence-in-depth thinking |
| WU8.3 (demo runbook) | No | No | Docs only |
| WU8.4 (wrap-up journal + CLAUDE.md update) | No | No | Docs only |
| E2E hotfix (WU8.2 stability) | No | No | Test stability, not security |

## Bottom line

The pattern is "well-functioning review catching real things" rather
than "implementations are buggy." Optimisation should target only
the high-risk surfaces with adversary-framed instructions, not
generalise to every WU. The cost of doing this for WU5.4 (the only
remaining surface that warrants it) is low and the benefit — a
cleaner commit history with fewer secfix follow-ups — is real.

## Post-demo follow-ups

- Re-evaluate this retrospective after the demo. If WU5.4's prompt
  changes produced zero secfixes, the technique is validated for
  future work. If it produced secfixes anyway, the underlying issue
  may be the review-pass's exhaustiveness, not the implementer's
  framing.
- Consider whether the engineering-team skill itself should encode
  "named-adversary framing" as a built-in posture for known-risky
  WU categories, rather than relying on individual prompt scaffolding.
- File-level audit: re-read every secfix commit's diff six weeks
  post-demo (~2026-07-20) to confirm no regression has reintroduced
  the original issue.
