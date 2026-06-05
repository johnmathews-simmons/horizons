# 2026-06-05 — WU1.1 tenancy tables

Third session of the day. Shipped the tenancy spine — `users`,
`subscriptions`, `subscription_scopes` — and turned `env.py` into an
autogenerate-capable Alembic environment.

## What shipped

### WU1.1 — Schema for users + subscriptions + subscription_scopes (`416b162`)

The three aggregates of the tenancy spine:

| Table | What it holds | Mutability |
| --- | --- | --- |
| `users` | account identity (`id`, `email`, `password_hash`, `role`, `created_at`) | mutable; password / email rotation allowed |
| `subscriptions` | time-bounded entitlements (`id`, `user_id`, `valid_from`, `valid_to`, `created_at`) | append-only via trigger; one allowed UPDATE shape (see below) |
| `subscription_scopes` | jurisdiction × sector coverage per subscription | strict append-only (no UPDATE) |

Cancelling a subscription is the only UPDATE the database permits on
`subscriptions`: `valid_to` moves from `NULL` to a non-`NULL` timestamp,
every other column unchanged. The trigger `subscriptions_no_update`
rejects everything else. Revival (NULL → ts → NULL) is rejected; a
reactivation is a *new* `subscriptions` row with the same `user_id`.
`subscription_scopes_no_update` rejects every UPDATE outright — scope
changes happen by ending the old subscription and inserting a new one.

UUIDv7 primary keys via Postgres 18's native `uuidv7()`. CHECK
`valid_to IS NULL OR valid_to > valid_from`. Index on
`(user_id, valid_from)` covers "this user's subscriptions in order" and
range-scans for "active at time T". `ON DELETE RESTRICT` from
`subscriptions → users`; `ON DELETE CASCADE` from
`subscription_scopes → subscriptions`.

Grants: `api_app` gets SELECT / INSERT / UPDATE on `users` +
`subscriptions` (the UPDATE on subscriptions is trigger-policed) and
SELECT / INSERT on `subscription_scopes`, plus USAGE on `user_role`.
`ingestion_worker` and `admin_bypass` receive nothing on these tables —
private state, not corpus. RLS lands in WU1.4.

### Q1–Q3 decisions

1. **`users.role` is a Postgres ENUM type** (`user_role` with values
   `client`, `admin`), not `text + CHECK`. The role set is small,
   intentionally rare-to-change, and type-level enforcement reads
   cleanly in psql and in autogen diffs.

2. **Append-only trigger is Tight, not Loose or monotonic.** Trigger
   permits exactly one UPDATE shape on `subscriptions`
   (`valid_to NULL → timestamp`, every other column equal). No
   monotonic guard on `valid_to`: historical-data imports and admin
   fix-ups are real operations and the trade-off isn't worth the
   protection.

3. **ORM models live in `db/models/` as a per-aggregate package**, not
   in a single `models.py`. Track 4 brings the table count to 8–10, so
   the flat-file layout would get unwieldy quickly. `__init__.py`
   re-exports `Base`, `User`, `UserRole`, `Subscription`,
   `SubscriptionScope`.

### Autogen wiring

`env.py` now imports `Base` from `horizons_core.db.models` and sets
`target_metadata = Base.metadata`. Verified end-to-end: spun a one-shot
PG 18 container, applied head, ran
`alembic revision --autogenerate -m drift_check2`, and confirmed the
generated upgrade/downgrade are empty `pass`. Two scratch revisions
along the way were deleted, not committed.

The first autogen pass caught two real model/migration drifts that
became part of the unit:

- `Mapped[str]` defaults to SQLAlchemy `String` (VARCHAR), but the
  migration creates `TEXT`. Fixed by declaring `Text` explicitly in the
  models.
- Table comments lived only in the migration. Fixed by mirroring them
  into `__table_args__["comment"]` on each model.

These would have been silent drift if we'd shipped without checking the
autogen output. Lesson: drift checks happen *after* the wiring change,
not just at the end.

### Code-review catch

Mid-review on the ORM relationships: `User.subscriptions` had
`cascade="all, delete-orphan"`. The DB FK is `ON DELETE RESTRICT` and
users are never deleted by design — the cascade is dead at best and
misleading at worst. Removed it; relationship is just `back_populates`
now, matching the DB intent.

### Ruff per-file ignore

SQLAlchemy 2.x's `Mapped[T]` resolves annotation strings at mapper-
configuration time via module globals. With
`from __future__ import annotations`, `uuid` and `datetime` look like
"type-only" imports to ruff (TC003) — but they must remain runtime
imports for the mapper to work. Added a per-file ignore for
`packages/horizons-core/src/horizons_core/db/models/*.py` with a comment
explaining the constraint.

## Schema-level documentation

Two doc updates:

1. New `packages/horizons-core/src/horizons_core/db/schema.md` —
   anchor-style table descriptions for the three aggregates, the
   append-only enforcement contract, and the current multi-tenant
   access posture (loose-as-workable grants, RLS deferred to WU1.4).

2. Updated `db/roles.md` with a per-table grants table that points
   forward to `schema.md` and back to the existing role-design content.

Design docs (`docs/0–4.*.md`) untouched — WU1.1 is implementation of
design doc 3, not a design-level change.

## Tests

11 new integration tests in `tests/test_tenancy_tables_migration.py`,
testcontainers PG 18, sync (Alembic is sync; the session-scoped async
engine fixture's event loop would conflict). Coverage:

| Test | What it verifies |
| --- | --- |
| `test_tenancy_tables_exist_with_expected_columns` | tables, columns, types, nullability |
| `test_schema_objects_owned_by_schema_owner` | DDL ownership on tables + ENUM |
| `test_user_role_enum_has_client_and_admin` | ENUM members exactly `{admin, client}` |
| `test_users_insert_returns_uuidv7_default` | `uuidv7()` default fires; version byte is 0x7 |
| `test_users_update_is_allowed` | password rotation works |
| `test_subscriptions_check_rejects_inverted_validity` | CHECK constraint enforced |
| `test_subscriptions_allow_ending_valid_to` | the one allowed UPDATE shape |
| `test_subscriptions_reject_other_updates` | rewriting `valid_from` rejected |
| `test_subscriptions_reject_resetting_valid_to_to_null` | revival via UPDATE rejected (must be new row) |
| `test_subscription_scopes_reject_any_update` | strict append-only on scopes |
| `test_index_on_subscriptions_user_id_valid_from_exists` | index present |

Full suite: 17 passed, 100% coverage on touched files.

## What's next

Per the manual run pointer (`manual-20260604T151127Z`), Track 1 still
has WU1.2 – WU1.9 ahead. WU1.2 is the corpus tables (documents,
versions, clauses) — distinctly different territory from the tenancy
spine, fresh-session-friendly. Branch protection on `main` requiring
both CI lanes still needs configuring in the GitHub UI (one-shot
manual; flagged from the previous session and still outstanding).
