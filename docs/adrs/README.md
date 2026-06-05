# Architecture Decision Records

This directory holds the project's **Architecture Decision Records**
(ADRs) — short, numbered, immutable records of one decision each. ADRs
are the *granular* counterpart to the chapter-grained design-doc chain
in `docs/1.` through `docs/4.`. The framing was sanctioned upfront in
`docs/0. about-these-docs.md` (§"Architecture Decision Records (ADRs)
(the secondary frame)").

## Template

[MADR v4](https://adr.github.io/madr/) (Markdown Any Decision Records).
A decision with two-plus options and visible trade-offs is the case
MADR was designed for; the structured *Considered options* + *Pros and
cons* sections force an honest comparison. Use the format `0001`,
`0002`, … and pad to four digits.

Skeleton:

```markdown
# ADR NNNN — {short imperative title}

- Status: {proposed | accepted | rejected | deprecated | superseded by …}
- Date: YYYY-MM-DD
- Deciders: …

## Context and problem statement
## Decision drivers
## Considered options
## Decision outcome
### Consequences
### Confirmation
## Pros and cons of the options
### {option A}
### {option B}
## More information
```

## Lifecycle

- An ADR is **immutable** once accepted. If a decision is revisited,
  write a new ADR and set the old one's status to `superseded by NNNN`
  with a link.
- An ADR's status header is the source of truth — there is no separate
  state file. `proposed` ADRs can land in `main` (they document
  intent); future authors are expected to either accept or supersede,
  not edit silently.
- Spike code that supports an ADR may live in `spikes/wuXY/` at the
  introducing commit and be removed in a follow-up commit. Git history
  keeps the evidence; the working tree stays lean.

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-worker-shape.md) | Ingestion worker shape: long-running asyncio container | accepted |
