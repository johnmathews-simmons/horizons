# Demo accounts runbook (WU8.1)

The public demo (~2026-06-08) uses three pre-provisioned accounts under
the IETF-reserved `@example.test` TLD. This runbook is the source of
truth for: how to provision them, how to log in, what each one shows,
and how to reset between demo runs.

These accounts are distinct from the WU8.2 Playwright e2e fixtures,
which live under `@e2e.test` and are managed by
`packages/horizons-api/scripts/seed_e2e.py`. Do not cross-wire the two.

## Accounts

| Email | Role | Subscription scope |
|-------|------|--------------------|
| `demo-uk@example.test` | `client` | (jurisdiction=UK, sector=BANKING) |
| `demo-eu@example.test` | `client` | (jurisdiction=EU, sector=BANKING) |
| `admin-demo@example.test` | `admin` | none |

The UK and EU accounts have **disjoint** subscriptions: the demo
narrative leans on showing the same `/changes` view rendering different
data when the operator switches between them. The admin account is the
"support view" mode used to demonstrate the cross-tenant isolation
controls from the admin side.

## Provisioning

The script is `packages/horizons-api/scripts/create_demo_accounts.py`.
It writes directly to the database (mirroring `seed_e2e.py`); the WU4.5
`/v1/admin/subscriptions` endpoints are not used because the very first
admin account cannot be bootstrapped through an HTTP path that itself
requires an admin bearer. Direct SQL is the documented bootstrap seam.

### One-time setup

```bash
export HORIZONS_DB_URL="postgresql+psycopg://postgres:postgres@localhost:5432/horizons"

# Mandatory. The script refuses to provision unless all three are set.
# Use a long random string per account — these are the credentials
# anyone running the public demo can authenticate with for the duration
# of the showcase.
export HORIZONS_DEMO_UK_PASSWORD="<a long random string>"
export HORIZONS_DEMO_EU_PASSWORD="<a long random string>"
export HORIZONS_DEMO_ADMIN_PASSWORD="<a long random string>"

uv run python packages/horizons-api/scripts/create_demo_accounts.py
```

Re-running the script rotates the stored password hash to match the
freshly resolved env-var value — there is no silent "skip if exists"
path. Re-running with new env-var values rotates without `--reset`.

**No-downgrade guard.** If the resolved password for an account came
from `--allow-dev-defaults` (env var unset, bake-in fallback in effect)
but the existing row currently holds a real (env-var-sourced)
credential, the script refuses the run before any UPDATE and prints the
offending accounts. The operator's options at that point are: set the
missing env var(s) and re-run, or pass `--reset` to deliberately wipe
the row first. This closes the path where a stray
`--allow-dev-defaults` invocation could downgrade a production password
to the publicly-known bake-in default.

To delete watchlist state or otherwise rewind to a clean slate, pass
`--reset`:

```bash
uv run python packages/horizons-api/scripts/create_demo_accounts.py --reset
```

`--reset` removes the demo users, their subscriptions, their
`subscription_scopes`, and their watchlists. It does not touch any
non-demo row.

### Dev defaults (localhost only)

For local development the script can fall back to the dev-default
passwords baked into the source:

- `demo-uk@example.test` / `demo-uk-pass-not-secret`
- `demo-eu@example.test` / `demo-eu-pass-not-secret`
- `admin-demo@example.test` / `admin-demo-pass-not-secret`

The fallback is opt-in via `--allow-dev-defaults`:

```bash
uv run python packages/horizons-api/scripts/create_demo_accounts.py --allow-dev-defaults
```

These defaults are **never** acceptable for any environment exposed
beyond localhost. The admin account has cross-tenant read access via the
WU1.9 audit path; a known admin password on a publicly reachable host
during the 1–2 day demo window is a real risk. The opt-in is
intentional — operators must consciously decide to use the defaults
rather than have them silently applied.

## Logging in

### Web UI (the demo path)

1. Open the SPA at the demo URL — `https://<host>/login`.
2. Submit `demo-uk@example.test` plus the configured password.
3. On success the SPA navigates to `/changes` and renders the UK-scoped
   recent-change list.
4. To switch tenants: log out (top-right user menu → Logout), then log
   in as `demo-eu@example.test`. Confirm that `/changes` now renders the
   EU-scoped list with no UK entries visible.
5. The admin walkthrough uses `admin-demo@example.test`; the SPA's
   admin nav appears only for the admin role.

### Curl (sanity check before the demo)

The login endpoint is `POST /v1/auth/login`. The programmatic shape (no
`X-Client-Type: browser` header) returns both an access and a refresh
token in the JSON body.

```bash
curl -sS -X POST "$HORIZONS_API_BASE/v1/auth/login" \
  -H "content-type: application/json" \
  -d '{"email":"demo-uk@example.test","password":"'"$HORIZONS_DEMO_UK_PASSWORD"'"}' | jq .
```

Expected shape:

```json
{
  "access_token": "<jwt>",
  "refresh_token": "<opaque>"
}
```

Use the access token against the primitives:

```bash
ACCESS=$(curl -sS -X POST "$HORIZONS_API_BASE/v1/auth/login" \
  -H "content-type: application/json" \
  -d '{"email":"demo-uk@example.test","password":"'"$HORIZONS_DEMO_UK_PASSWORD"'"}' | jq -r .access_token)

curl -sS -H "authorization: Bearer $ACCESS" \
  "$HORIZONS_API_BASE/v1/me" | jq .
# Should show role=client and a subscription with scope UK/BANKING.

curl -sS -H "authorization: Bearer $ACCESS" \
  "$HORIZONS_API_BASE/v1/changes?limit=5" | jq .
# Should return UK-scoped change events only.
```

Repeat with `demo-eu` and confirm the same query returns disjoint EU
data. Repeat with `admin-demo` and confirm `/v1/admin/subscriptions`
endpoints respond 200 (or 422 for unbound calls) — they should NOT
respond 403 the way the client tokens do.

## Pre-demo checklist

Run the day-of, in order:

1. **DB migrated**: `uv run alembic upgrade head`.
2. **Curated set seeded** (WU8.0):
   ```bash
   uv run python scripts/seed_curated_set.py --stage-synthetic-v2
   ```
3. **Demo accounts provisioned** (this runbook). The three password env
   vars MUST be set; the script aborts before any DB write otherwise:
   ```bash
   export HORIZONS_DEMO_UK_PASSWORD="<random>"
   export HORIZONS_DEMO_EU_PASSWORD="<random>"
   export HORIZONS_DEMO_ADMIN_PASSWORD="<random>"
   uv run python packages/horizons-api/scripts/create_demo_accounts.py
   ```
4. **Smoke**: run the curl block above for each of UK / EU / admin and
   confirm the responses look right.
5. **Webapp smoke**: log in via `/login` as each account in a private
   browser tab to confirm cookies / tokens are clean.

## Reset between dry-runs

To rewind for a clean redo (e.g. after demo rehearsal generated
watchlist entries):

```bash
uv run python packages/horizons-api/scripts/create_demo_accounts.py --reset
```

This wipes only the `@example.test` rows. The corpus (documents,
versions, clauses, change_events) is preserved — those come from the
WU8.0 seed and are reusable across demo runs.

## Public-exposure caveats

- No real bank names, no client names, no firm names in any account or
  copy. The `@example.test` TLD is the IETF reserved domain; do not
  substitute a real domain.
- The dev-default passwords are visible in the script source and in
  this runbook. They are usable only behind `--allow-dev-defaults` and
  are NEVER appropriate for any environment exposed beyond localhost.
  The script's default refusal (env vars required) is the substantive
  guard; do not bypass it on production.
- The admin account has read access across every tenant's private state
  via the WU1.9 audit path. Use it sparingly during the demo and only
  for the "operator support view" beat.

## Cross-references

- WU4.5 endpoints (admin subscription PATCH / POST) used by the SPA's
  admin view — `docs/api/endpoints.md`.
- WU8.0 corpus + synthetic v2 staging — `data/samples/synthetic_v2/README.md`.
- Auth flow specifics (cookie vs. body, refresh) — `docs/api/auth.md`.
