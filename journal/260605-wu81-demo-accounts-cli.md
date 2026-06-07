# 2026-06-05 — WU8.1: demo-accounts CLI + runbook

*Last revised: 2026-06-05.*
*Path: journal/260605-wu81-demo-accounts-cli.md.*

Closes Track 8 unit 8.1. A small admin CLI provisions the three demo
accounts (`demo-uk@example.test`, `demo-eu@example.test`,
`admin-demo@example.test`) for the public showcase, with a `--reset`
teardown-then-recreate path and a per-account env-var override for
passwords. The runbook in `docs/runbooks/demo-accounts.md` covers
login, the pre-demo checklist, and the public-exposure caveats.

These accounts are distinct from the WU8.2 Playwright e2e fixtures —
those live under `@e2e.test` and are managed by
`packages/horizons-api/scripts/seed_e2e.py`. The two scripts share a
DSN-normalisation helper and a similar SQL shape, but they don't
overlap in account naming or the rows they touch.

## What shipped

### `packages/horizons-api/scripts/create_demo_accounts.py`

A standalone `argparse`-driven CLI. Configured by environment:

- `HORIZONS_DB_URL` — required. Accepts both `+psycopg` and `+asyncpg`
  forms (same env var the API uses); normalised to `+psycopg`
  internally.
- `HORIZONS_DEMO_UK_PASSWORD` / `HORIZONS_DEMO_EU_PASSWORD` /
  `HORIZONS_DEMO_ADMIN_PASSWORD` — optional. Defaults are baked into
  the script (not secret; see the runbook caveats).

Behaviour:

- Default run: create-or-skip. Existing accounts are left untouched.
- `--reset`: delete every `@example.test` row plus its dependants
  (watchlists, subscription_scopes, subscriptions), then create.
- Each client account also gets one `subscriptions` row and one
  `subscription_scopes` row at (UK, BANKING) / (EU, BANKING).
- The admin account has no subscription.

### `docs/runbooks/demo-accounts.md`

The runbook covers: account inventory, provisioning command-by-command,
dev defaults vs. env-var overrides, the curl login snippet, the
expected post-login SPA navigation, the pre-demo checklist, the
`--reset` between-runs path, and the public-exposure caveats spelled
out in CLAUDE.md (no real bank names, override the dev defaults in any
non-localhost environment, etc.).

## Decisions worth keeping

1. **Direct SQL, not `/v1/admin/subscriptions` HTTP.** The WU4.5 admin
   endpoint requires an admin bearer; bootstrapping the *first* admin
   bearer chicken-and-egg's through HTTP. Direct SQL writes are the
   documented bootstrap seam — `seed_e2e.py` already uses this seam
   for the e2e fixtures, and this script follows the same pattern.
   Future demos that need a programmatic flow (e.g. CI seeds the
   accounts then a separate test runs as the admin) can layer
   `/v1/admin/subscriptions` calls on top once the admin row exists;
   the WU8.1 CLI's only job is the bootstrap.
2. **`@example.test` (IETF-reserved TLD), not the project's own
   domain.** Reserves the namespace from real-customer collisions and
   marks every row as obviously synthetic in operations dashboards.
   The WU8.2 Playwright fixtures use `@e2e.test` for the same reason —
   distinct TLDs keep the two paths visibly separate.
3. **`--reset` deletes watchlists + scopes + subscriptions in
   dependency order, then users.** No `session_replication_role
   = 'replica'` superuser bypass is required because no append-only
   trigger covers DELETE on the touched tables (the append-only
   triggers only catch UPDATE). This is a cleaner teardown than the
   one in `seed_e2e.py`, which has to bypass `change_events`'s
   DELETE-rejecting trigger; demo accounts never produce
   `change_events`, so the seam isn't needed.
4. **Dev-default passwords are NOT secret.** They are visible in the
   script source and in the runbook. The public demo deployment
   overrides them via container env. The CLI uses `# noqa: S105` to
   silence Bandit's hard-coded-password warning at the defaults; the
   noqa is the explicit statement that "these are dev fixtures, not
   credentials".
5. **`hash_password` lives in `horizons_core.core.auth`.** Both this
   script and `seed_e2e.py` import it directly. The hashing scheme
   (Argon2-id with the WU4.0 parameters) is the same as the login
   path's verifier — there is no separate path for fixture-hashed
   passwords.
6. **The script does not refresh JWTs or set cookies.** Login is a
   live SPA / curl action; the script's job ends when the row exists
   in `users` + `subscriptions` + `subscription_scopes`. The first
   login through `/v1/auth/login` is what mints the access and
   refresh tokens.

## Status

- `uv run ruff check .` — clean.
- `uv run pyright` — 0 errors.
- `uv run pytest -m "not integration"` — **323 passed, 4 skipped** (no
  regressions from WU8.0 + WU8.1; same skip set as before).
- `uv run pre-commit run --all-files` — all hooks pass.
- Smoke: `uv run python packages/horizons-api/scripts/create_demo_accounts.py --help`
  emits the expected usage banner; `--reset` and the env-var
  documentation are visible.

## What's next

- **WU8.2** — Playwright e2e smoke. Already merged earlier in the
  session (per branch `main`). Worth re-running locally after WU8.0
  lands a different corpus shape to confirm no regression in the
  Playwright assertions.
- **WU8.3** — demo runbook covering the full demo flow (login → browse
  → diff → switch tenants → admin view → support view). Will reference
  this runbook (`demo-accounts.md`) for the login section.
- **WU8.4** — pre-demo wrap journal + CLAUDE.md `Commands` section
  update.
