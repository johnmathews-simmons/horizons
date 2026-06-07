# Retrospective — secfix commit pattern during the 2026-06-05 build sprint

*Last revised: 2026-06-06.*
*Path: journal/260605-secfix-pattern-retrospective.md.*

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
| E2E hotfix (WU8.2 stability) | ~~No~~ **Revised: Yes** | ~~No~~ **Revised: Yes** | See update section below — the hotfix found 5 bugs, 2 security-adjacent, reframing this as an integration surface |

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
- Validate the integration-surface extension (added in the update
  below) against any future WU that crosses ≥3 components. If
  named-adversary framing on integration surfaces also reduces the
  iteration count of diagnostic hotfixes, the technique generalises
  beyond security.

---

## Update — WU8.2 e2e hotfix (2026-06-05 evening)

Roughly 90 minutes after the original retrospective above, the WU8.2
e2e hotfix landed (`journal/260605-wu82-hotfix-e2e-cors.md`). It
materially changes the analysis. Summary in three points:

### What the hotfix actually found

The original diagnostic prompt assumed one CI timing race. Local
reproduction surfaced a chain of **five distinct latent bugs**, each
masking the next:

| # | Bug | Commit | Category |
|---|---|---|---|
| 1 | API CORS allow-list omitted `X-Client-Type` | `ac59003` | Security-adjacent — CORS is security infrastructure |
| 2 | CI e2e API step missing `+asyncpg` driver in DB URL | `385b532` | Integration / infra config |
| 3 | Pydantic email-validator rejected RFC-6761 `.test` TLD | `9a8d2e8` | Standards / library quirk |
| 4 | Webapp router didn't try refresh-from-cookie on cold SPA bootstrap | `9d03cd8` | Real app bug — F5 logged users out |
| 5 | Webapp Axios sent `Authorization` header to `/v1/auth/{login,refresh,logout}` | `05a64ff` | Security-adjacent — auth endpoints shouldn't accept bearer |

Two are security-adjacent (1 + 5); three are integration / standards.
**The pattern extends beyond security to any integration surface.**

### What the hotfix DIDN'T do (important meta-point)

Zero stability slack added: no navigation-timeout bumps, no
`test.slow()`, no retry-count change, no `waitForResponse` shims, no
extra workflow waits. The original WU8.2 timing was correct; failures
were never about timing. Recorded in the hotfix journal so a future
agent doesn't tighten back something that was never loosened.

This validates the diagnostic prompt's "minimal surgical changes"
framing — when the prompt forbids adding stability noise, the agent
is forced to find the real bug. That framing is worth keeping for
similar diagnostic prompts.

### Reframing Change 1 — extend to integration surfaces

The original Change 1 limited named-adversary framing to high-risk
*security* surfaces (auth, RLS, redirect, credentials). The hotfix
data shows similar bug density on integration surfaces — places where
≥3 components meet (browser ↔ webapp ↔ API ↔ DB ↔ infra). Bugs there
hide behind each other in chains, just like security review iterations
hide bugs from each other.

Updated rule:

> **Apply Change 1 (named-adversary framing) to both high-risk
> security surfaces AND integration surfaces.** An integration surface
> is any WU that wires together three or more independently-developed
> components for the first time. The "adversary" framing on an
> integration surface is: "I'm a chaos engineer trying to find the
> latent bug each component left for the next to discover."

### Application to the remaining WUs

| WU | Apply Change 1? | Apply Change 2? | Reasoning |
|---|---|---|---|
| WU5.4 (admin views + support view) | Yes | Yes | Security surface (impersonation + audit) AND integration surface (admin shell ↔ public API ↔ audit log) |
| WU8.3 (demo runbook) | No | No | Docs only |
| WU8.4 (wrap-up journal + CLAUDE.md update) | No | No | Docs only |
| (E2E hotfix) | (Already done) | (Already done) | Retroactively a successful application of Change 1 + 2 via local reproduction discipline |

### Three follow-ups the hotfix journal captured

Recording here for visibility (not blocking):

1. Regression unit tests for cold-bootstrap-refresh (Bug 4) and
   Authorization-suppression-on-auth-endpoints (Bug 5). Currently
   only the e2e would catch a regression — unit-level coverage
   would catch it on the next PR.
2. Cross-reference note in `docs/runbooks/demo-accounts.md` flagging
   the RFC-6761 `.test` TLD restriction so future demo-account
   generators don't repeat the trap.
3. Rename the now-overloaded `_skipAuthRefresh` Axios flag to
   something more accurate — the flag conflates "skip refresh
   retry" with "skip Authorization header attachment."

### Revised bottom line

The pattern is broader than security: **iterative review under-counts
both security categories and integration failure modes.** The fix is
the same — adversary framing + explicit second-review constraint — but
the scope of "high-risk surfaces" should include integration surfaces
as well as security surfaces. WU5.4 happens to be both, so it gets
both treatments. Most future WUs will be one or the other; check
which before scaffolding the prompt.
