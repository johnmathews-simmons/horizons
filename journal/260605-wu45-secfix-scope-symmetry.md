# 2026-06-05 — WU4.5 secfix: scope-symmetry with current_scope()

*Last revised: 2026-06-05.*
*Path: journal/260605-wu45-secfix-scope-symmetry.md.*

Post-push security review on the WU4.5 branch flagged a
**parser/validator differential** in
`SubscriptionsRepository.active_scope_documents`. Fixing it surfaced a
second, deeper Postgres clock-semantics issue that landed in the same
fix.

## The finding

`active_scope_documents` computes the set of document ids in the
target user's *current* scope. The PATCH soft-hide pass takes the
complement (active watchlists whose document is not in this set) and
flips `active=false`. If this function reports a tighter scope than
`app_private.current_scope()`, the soft-hide is **destructive**: it
inactivates watchlists the client still legitimately reads via RLS.

The original function:

```python
.where(
    Subscription.user_id == user_id,
    Subscription.valid_to.is_(None),
)
```

vs `app_private.current_scope()` (`migrations/.../0004_current_scope.py`
+ `0011_admin_subscription_endpoints_support.py`):

```sql
WHERE s.user_id = uid
  AND s.valid_from <= pg_catalog.now()
  AND (s.valid_to  IS NULL OR s.valid_to  > pg_catalog.now())
  AND (ss.valid_to IS NULL OR ss.valid_to > pg_catalog.now())
```

The repository was missing three predicates:

1. `valid_from <= now()` — would otherwise include not-yet-active
   subscriptions (wider scope than the client, would leave soft-hidden
   watchlists lingering).
2. `valid_to IS NULL OR > now()` on `subscriptions` — would otherwise
   reject subscriptions scheduled to end in the future but still
   active (tighter scope than the client → destructive soft-hide).
3. `valid_to IS NULL OR > now()` on `subscription_scopes` — same shape
   on the scope axis.

(2) is the destructive case. The asymmetry is exploitable any time a
client has a subscription with a future `valid_to` set: an admin
PATCH on a *different* subscription would still cause the soft-hide
pass to inactivate watchlists covered by the future-`valid_to`
subscription.

## What shipped (part 1 — predicate symmetry)

`active_scope_documents` now mirrors `current_scope()`'s predicates
exactly:

```python
now = func.now()
.where(
    Subscription.user_id == user_id,
    Subscription.valid_from <= now,
    or_(
        Subscription.valid_to.is_(None),
        Subscription.valid_to > now,
    ),
)
# and on the scope join:
or_(
    SubscriptionScope.valid_to.is_(None),
    SubscriptionScope.valid_to > now,
),
```

Docstring on the function explicitly calls out the lock-step contract
with `current_scope()` and points at the migration that defines the
canonical predicate.

A regression test
(`test_reduction_respects_future_valid_to_subscription`) seeds a
subscription with `valid_to = now() + 1 day` on one scope and patches
a *different* subscription. The watchlist for the future-`valid_to`
subscription's scope must stay active.

## What shipped (part 2 — txn-time vs python-time)

Predicate symmetry alone broke the original reduction test
(`test_admin_patch_reduction_soft_hides_out_of_scope_watchlist`):
the soft-hide reported 0 hidden rows instead of 1. Root cause is a
Postgres clock semantic that is easy to miss.

PostgreSQL's `now()` (= `transaction_timestamp()`) returns the time
the *transaction* started, not the current statement. Inside the
admin transaction:

- The PATCH does `UPDATE subscription_scopes SET valid_to = <python
  datetime.now(UTC)>` — this timestamp is *later* than the admin
  transaction's `now()` because it was taken after the transaction
  started.
- The soft-hide query then runs `active_scope_documents` in the same
  transaction. Server-side `now()` still returns the
  transaction-start time, so `valid_to > now()` evaluates **true**
  for the just-ended row → it still looks active → the soft-hide
  pass concludes the scope is in scope.
- Post-commit (client's later transaction) the row is correctly
  considered ended.

The two views disagree only **inside** the admin transaction. This is
the same family of bug as the original finding (asymmetry between
the write-time view and the post-commit view), now in the time
dimension instead of the predicate-set dimension.

Fix: `soft_delete_scopes` uses server-side `func.now()` for the new
`valid_to` value, dropping the `ended_at` parameter and the
`datetime.now(UTC)` call at the route. Now:

- The UPDATE writes `valid_to = transaction_timestamp()`.
- The subsequent `active_scope_documents` evaluates `valid_to >
  now()` as `txn_now > txn_now` → false → scope is correctly
  considered ended.
- Post-commit reads agree: `valid_to > later_now()` → false.

Both views converge on a single transaction clock.

## Status by suite (end of secfix)

- 521 passing (was 520 → +1 future-`valid_to` regression test).
- ruff check / pyright strict / pre-commit (incl.
  `regen-endpoints-md --check`) all clean.

## Design decisions worth keeping

1. **Lock-step contract with `current_scope()` is now documented in
   the repo function's docstring** with a pointer to migration 0011.
   Future changes to either side must touch both.
2. **Server-side `now()` for the soft-delete write.** This is the
   cheapest defence against the txn-time vs python-time asymmetry.
   The alternative — `SET LOCAL app.user_id = <target>` inside the
   admin transaction so we could call `current_scope()` directly —
   would mutate a GUC the admin's session bracket relies on; the
   blast radius is wider than the cure.
3. **No new endpoint, no schema change.** The fix is a predicate and
   a clock-anchor change in already-shipped code. Migration 0011's
   shape stays as-is; the contract it documents (`current_scope()`
   filters by both `subscriptions.valid_to` and
   `subscription_scopes.valid_to`) is the spec the secfix aligns
   `active_scope_documents` against.
