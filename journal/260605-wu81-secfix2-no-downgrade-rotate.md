# 2026-06-05 — WU8.1 secfix #2: no-downgrade rotate guard

*Last revised: 2026-06-05.*
*Path: journal/260605-wu81-secfix2-no-downgrade-rotate.md.*

Closes the MEDIUM finding raised by the post-commit review of
`260605-wu81-secfix-demo-password-handling.md`. The first secfix
replaced the silent "skip if exists" idempotency with an unconditional
rotate. That fix introduced a new attack surface: if an operator
re-ran the script with `--allow-dev-defaults` (env vars unset), the
rotate path would UPDATE the production hashes to the publicly-known
bake-in defaults. The original "stale credentials" footgun was closed
at the cost of opening a "downgrade real credential" footgun.

This commit restores the missing invariant: a misused
`--allow-dev-defaults` cannot overwrite a real production credential
with a public default.

## What changed

### `_resolve_passwords` returns a third element

The helper now returns `(resolved, missing, from_dev_default)`. The
new `from_dev_default: set[str]` is the set of emails whose resolved
password came from the bake-in fallback rather than an env var.
Accounts not in this set either came from env vars or weren't
resolved at all.

### `_downgrade_candidates` — the pre-rotate gate

A new helper runs after the env-var check but before any
INSERT/UPDATE. For each account in `from_dev_default`, it fetches the
existing stored hash (if any) and calls `verify_password(plaintext,
existing_hash)`. If the verify fails, the account is added to the
blocked list. A non-empty blocked list aborts the run before any DB
mutation, with a message naming the offending accounts and pointing
the operator at either setting the missing env vars or passing
`--reset` to deliberately wipe.

The verify cost is ~100 ms per call (Argon2-id by design); with at
most three demo accounts the worst-case overhead is unmeasurable
against the rest of the script.

### `_create_or_rotate` now returns `unchanged` when the hash matches

In addition to `created` and `rotated`, the helper now reports
`unchanged` when the existing hash already verifies against the
resolved password. This is the natural pair to the no-downgrade guard:
an idempotent re-run under `--allow-dev-defaults` on rows already
holding the defaults reports `unchanged` rather than re-hashing and
UPDATE-ing for no reason. (Argon2-id is salted, so a fresh
`hash_password(same)` produces a different ciphertext every call;
skipping the UPDATE keeps the row stable in space and time.)

### New tests

Four new tests in `tests/test_create_demo_accounts.py` cover the
guard:

1. `test_downgrade_guard_blocks_dev_default_over_real_credential` —
   real hashes present in the DB + dev-default fallback in the
   resolution → all three accounts flagged.
2. `test_downgrade_guard_allows_dev_default_over_matching_row` —
   dev-default hashes present + dev-default fallback → empty
   blocked list (idempotent no-op rotate).
3. `test_downgrade_guard_ignores_env_var_sourced_accounts` — accounts
   whose resolution came from an env var are never blocked, even when
   the existing hash diverges (the operator explicitly set a new
   password, so the rotate is not a downgrade).
4. `test_downgrade_guard_skips_absent_accounts` — a fresh provision
   (no row) is a CREATE, not a downgrade.

The existing six tests still pass; the new helper signature
(`(resolved, missing, from_dev_default)`) is reflected in their
destructuring.

Total: **333 passed** (was 329 +4).

### Docs

- `docs/runbooks/demo-accounts.md` — adds a "No-downgrade guard"
  paragraph in the idempotency section explaining the new refusal
  path and the operator's options.
- Script docstring — rewritten in the "Idempotency rotates
  credentials" paragraph to spell out the invariant; the docstring is
  what users see via `--help` and source-read review.

## Decisions worth keeping

1. **Verify-against-cleartext, not literal-hash-equality.** Argon2-id
   uses a fresh salt per invocation, so `hash_password(default) ==
   hash_password(default)` is False. The verify path is what gives us
   the "does this row currently hold the dev default" answer cheaply
   and correctly.
2. **Block-by-account, abort-by-run.** A partial run that rotates
   some accounts but refuses others would be confusing (which got
   rotated? what's the recovery path?) and the operator would
   probably reach for `--reset` anyway. Refusing the whole run is
   simpler and matches how the env-var check already behaves.
3. **`unchanged` as a third outcome, not folded into `rotated`.**
   The operator-facing output reads "demo-uk: unchanged" vs.
   "demo-uk: rotated". The first tells you "your DB matches your
   inputs — nothing happened". The second tells you "the password
   you set is now in the DB, overwriting what was there". Both are
   safe; conflating them would hide the meaningful distinction.
4. **DSN-locality check NOT added.** An alternative fix the review
   suggested was "gate `--allow-dev-defaults` on a DSN host being
   localhost". I considered it and rejected it: localhost-only
   gating is bypassable (port forwards, container networks where
   localhost means the container), and doesn't address the deeper
   property — the verify-based check does. The runbook still
   describes the opt-in as localhost-only as operator guidance, but
   the substantive guard is the no-downgrade verify, not the host
   check.
5. **The guard does not protect against `--reset`.** `--reset`
   followed by a dev-default provision DOES wipe and re-provision
   with the public default. That's intentional: `--reset` is the
   operator's explicit "I want to wipe and start fresh" command. If
   it were also gated, there would be no escape hatch for the
   no-downgrade refusal. The guard is for the implicit-rotate path;
   `--reset` remains the explicit-wipe path.

## Status

- `uv run ruff check .` — clean.
- `uv run pyright` — 0 errors.
- `uv run pytest -m "not integration"` — **333 passed, 4 skipped**
  (was 329 +4 new no-downgrade tests).
- `uv run pre-commit run --all-files` — all hooks pass.
- Smoke: `--help` shows the same flag surface; no UX regression.

## What's next

- Both findings from the WU8.1 secfix thread are now closed. The
  control-regression is addressed at the deepest layer — verify
  rather than host-check — and the operator UX is unchanged on the
  happy path (env vars set → rotate freely).
- The WU8.0 fixture-gap follow-up (grow `fixtures.json` to ~50
  entries before the demo) is still outstanding.
