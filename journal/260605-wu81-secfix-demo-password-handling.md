# 2026-06-05 — WU8.1 secfix: demo-account password handling

*Last revised: 2026-06-05.*
*Path: journal/260605-wu81-secfix-demo-password-handling.md.*

Closes two findings from the post-push security review of WU8.1
(`packages/horizons-api/scripts/create_demo_accounts.py`):

1. **HIGH** — hard-coded credential fallback for the admin account
   (which carries cross-tenant read access). The demo is publicly
   reachable for 1–2 days during the showcase; "operator forgot the
   env-var override" is a foreseeable production footgun.
2. **MEDIUM** — idempotency silently preserved stale credentials. An
   operator who ran the script with dev defaults, then set env vars and
   re-ran, would see `skipped` in the output but the DB would still
   hold the default hash.

## What changed

### Mandatory env vars by default

`HORIZONS_DEMO_UK_PASSWORD`, `HORIZONS_DEMO_EU_PASSWORD`, and
`HORIZONS_DEMO_ADMIN_PASSWORD` are now **required**. If any is unset
(or empty-string), the script aborts before any DB write and prints
which env vars are missing. The dev-default passwords baked into the
source are reachable only behind the new `--allow-dev-defaults` opt-in
flag, which is documented as localhost-only.

The opt-in is intentional. The defaults are not gone — they remain
convenient for first-time local setup and CI seeding — but the
operator must consciously decide to use them rather than have them
silently applied. The default refusal closes the "production deploy
without overrides" failure mode without changing the bootstrap seam
the script provides.

### Always-rotate on re-run

`_create_or_skip` is replaced by `_create_or_rotate`. The
"account exists" branch now UPDATEs `users.password_hash` to the
freshly resolved password instead of skipping. Re-running with
different env-var values rotates the stored hash; re-running with the
same values is a deterministic no-op effect-wise (same input → same
hash semantics).

This is the idempotency contract that survives operator forgetfulness:
the resolved password is what's in the DB, period. The previous
"create-or-skip" contract was technically idempotent but masked the
stale-credentials failure mode.

### Helper extraction

Password resolution is split into a pure `_resolve_passwords()`
helper that returns `(resolved, missing_env_vars)`. The CLI consults
`missing_env_vars` to decide whether to abort. The helper has zero
side effects, which makes it cheaply unit-testable; six tests in
`tests/test_create_demo_accounts.py` cover the matrix:

1. All three env vars unset + no opt-in → all three flagged.
2. Two set + one unset + no opt-in → the unset one flagged.
3. Empty-string env var treated as unset.
4. All three set → clean resolution, no missing.
5. All unset + `--allow-dev-defaults` → bake-in defaults.
6. One env var set + `--allow-dev-defaults` → env-var wins, others
   fall back.

The tests import the script as a module via `importlib.util` because
it lives under `scripts/` and isn't part of the package surface — the
same approach used elsewhere in the suite for CLI scripts.

### Runbook updates

`docs/runbooks/demo-accounts.md` rewritten to:

- present the env vars as mandatory (the example block exports all
  three before invoking),
- document the rotate-on-rerun behaviour (replacing the "silent skip"
  language),
- explain the `--allow-dev-defaults` opt-in and its localhost-only
  scope,
- restate the public-exposure caveat with the substantive guard
  language (default refusal) rather than the previous advisory
  language ("set env vars in production").

The pre-demo checklist's step 3 now includes the `export` calls
explicitly, so a fresh operator copy-pasting the checklist will not
hit the abort.

## Decisions worth keeping

1. **Default refusal, opt-in fallback.** The alternative — strict
   "no defaults, no opt-in flag, ever" — was tempting but would
   damage the local-dev ergonomics that justify the script existing
   in this form. `--allow-dev-defaults` keeps the localhost path
   convenient while making the production path safe-by-default.
2. **Always rotate, don't compare-and-rotate.** The security review's
   suggested fix included "detect that the stored hash equals
   hash_password(default) and refuse to skip". That works but is
   fragile: Argon2-id is salted, so two `hash_password(default)`
   calls produce different output, and the comparison would need a
   verify-against-cleartext step. Unconditional rotate is simpler,
   strictly stronger, and doesn't require any new auth-side
   primitives.
3. **No new migration.** The schema is unchanged. The UPDATE path
   uses the existing `password_hash` column; the `users` table has no
   append-only trigger that rejects column-level UPDATEs (only the
   tenancy ledger does, and `users` isn't part of it).
4. **`_create_or_rotate` returns "rotated", not "updated".** The
   outcome label is operator-facing in the script's final print, so
   the word should be unambiguous about what happened. "Rotated"
   reads as a security-positive event; "updated" is generic.
5. **Empty-string env var counts as unset.** A shell that exports
   `HORIZONS_DEMO_UK_PASSWORD=""` would otherwise silently succeed
   with an empty password, which the login flow's Argon2-id verifier
   would refuse but which would leave a confusing DB state. Belt and
   braces: empty string triggers the abort.

## Status

- `uv run ruff check .` — clean.
- `uv run pyright` — 0 errors.
- `uv run pytest -m "not integration"` — **329 passed** (was 323;
  +6 from `tests/test_create_demo_accounts.py`).
- `uv run pre-commit run --all-files` — all hooks pass.
- Smoke: `--help` shows the new `--allow-dev-defaults` flag and the
  refined `--reset` description.
- Smoke: invoking without env vars and without `--allow-dev-defaults`
  prints the abort message and exits 1 (verified with a sandbox
  ``HORIZONS_DB_URL`` pointing at no DB — the env-var check fires
  before the DB-connect).

## What's next

- The security review's two findings are now addressed; no further
  action on this thread.
- The WU8.0 fixture-gap follow-up (grow `fixtures.json` to ~50
  entries before the demo) is still outstanding.
