# Repository layer

The repository layer is the third defence-in-depth layer on top of
Postgres grants ([roles.md](../db/roles.md)) and RLS policies
([rls.md](../db/rls.md)). Application code talks to repos; repos talk to
SQLAlchemy; SQLAlchemy talks to Postgres through the session yielded by
[`db/session.py`](../db/session.py).

Read this alongside the WU1.5 [session contract](../db/rls.md#session-contract-wu15)
— every repo method assumes its session was constructed via
`get_session()` or `session_for_user()` and therefore has
`app.user_id` bound for the lifetime of the transaction.

## Shape

Each aggregate has one file under `repos/` exporting:

- A `<Aggregate>DTO` Pydantic v2 model. ORM rows never leave the repo
  boundary; DTOs are constructed via `model_validate(orm_row)` (powered
  by `model_config = ConfigDict(from_attributes=True)`).
- A `<Aggregate>Repository` class whose constructor takes the
  `AsyncSession` it will operate against. The session's lifetime is the
  caller's problem; the repo holds a reference but does not open or
  close it.

A marker [`Repository[T]` Protocol](base.py) sits in `base.py` and
documents the shape: a repository returns instances of its DTO type and
never leaks ORM objects. The protocol carries no required methods —
each aggregate's read/write surface is different — but it gives static
type checkers a single name to reason about.

## Session injection

```python
async with get_session(user_id) as session:
    await session.execute(text("SET LOCAL ROLE api_app"))  # done by the consumer
    repo = WatchlistsRepository(session)
    items = await repo.list_for()
```

The role switch (`SET LOCAL ROLE api_app`) is the caller's
responsibility — see the RLS architecture's "Admin code paths" note: the
session module does not branch on role because the switch is the
carve-out, not the bracket. In Track 4 the FastAPI dependency that
yields sessions will perform the switch; in tests the
`two_clients` fixture does it explicitly.

## `user_id` discipline

| Operation | Takes `user_id`? | Why |
| --- | --- | --- |
| Read methods (`list_for`, `get_by_id`, …) | **no** | RLS filters via session-bound `app.user_id`; passing it again would be redundant noise. |
| Write methods (`create`, `delete`, …) | **yes**, keyword-only | The `WITH CHECK` predicate enforces the same equality; an explicit `*, user_id: UUID` keeps the ownership claim visible at every call site and prevents accidental cross-user writes through a buggy session. |

Reads relying on RLS look the same regardless of caller; writes name
their owner. The asymmetry mirrors the policy shape (`USING` vs
`WITH CHECK`) and makes it impossible to call a write method without
saying who you are.

## ORM-only — no `text()`

Repos are part of `packages/horizons-core/src/`, so the architectural
test `tests/test_raw_sql_isolation.py` AST-walks them and will fail if
any `text(...)` call appears. Use SQLAlchemy 2.x `select()`, `insert()`,
`update()`, `delete()`. If a repo genuinely needs raw SQL the
conversation is "lift it into `db/session.py`" — not "add it to the
repo."

## Aggregates implemented in WU1.6

### Private state

| Repo | Methods | RLS layer |
| --- | --- | --- |
| `WatchlistsRepository` | `list_for()`, `get_by_id(id)`, `create(*, user_id, name)`, `delete(*, user_id, watchlist_id) -> bool` | Owner-keyed `watchlists_owner_*` policies |
| `RefreshTokensRepository` *(WU4.0)* | `record(*, jti, user_id, issued_at, expires_at)`, `get_by_jti(jti)`, `revoke(*, jti, user_id, revoked_at) -> bool` | Owner-keyed `refresh_tokens_owner_*` policies |

`get_by_id` returns `None` when the row exists but belongs to another
user — the RLS predicate filters the row out, so the repo sees no row,
so the API layer maps it to 404 (not 403). This is intentional: a 403
would leak the row's existence to a third party.

`delete` returns `True` when a row was removed, `False` when no row
matched (either it doesn't exist or it belongs to another user). The
caller decides what to do with the boolean; both outcomes look the same
from the wire.

### Corpus (subscription-scoped)

| Repo | Methods | RLS layer |
| --- | --- | --- |
| `DocumentsRepository` | `list_all()`, `get_by_id(id)` | `documents_in_scope` via `current_scope()` |
| `DocumentVersionsRepository` | `list_for_document(doc_id)`, `get_by_id(id)` | `document_versions_in_scope` walks FK up to `documents` |
| `ClausesRepository` | `list_for_version(version_id)`, `get_by_id(id)` | `clauses_in_scope` walks FK up to `document_versions` → `documents` |

No write methods on the corpus repos — those are the ingestion
worker's surface, which arrives with Track 3. The shape (separate repo
class operating against a session whose role is `ingestion_worker`) is
the same; the methods just differ.

## DTO conversion

```python
class WatchlistDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    name: str
    created_at: datetime
```

`from_attributes=True` is what lets `model_validate(orm_row)` read
attribute access (`row.id`) instead of requiring dict access. The DTO is
serialisable, immutable from the caller's perspective, and decoupled
from SQLAlchemy session lifetime — once a DTO leaves the repo the
session can close without breaking attribute access.

## Related

- [base.py](base.py) — the `Repository[T]` marker protocol.
- [db/rls.md](../db/rls.md) — the policy shapes the repo layer assumes.
- [db/session.py](../db/session.py) — the session bracket that binds
  `app.user_id`.
- [db/schema.md](../db/schema.md) — the table definitions the DTOs
  mirror.
