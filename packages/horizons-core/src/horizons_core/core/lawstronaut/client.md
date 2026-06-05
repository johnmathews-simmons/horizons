# Lawstronaut client

The shared async HTTP client for Lawstronaut v2. Wraps the
endpoints WU3.4's per-document poll body, the curated-set seed
(WU3.5), and any future admin tooling need. Lives in `horizons-core`
so it is reachable from every Python package in the workspace.

Surface (per the WU3.2 acceptance criteria in the improvement plan):

```python
class LawstronautClient:
    async def __aenter__(self) -> LawstronautClient: ...
    async def __aexit__(self, *exc: object) -> None: ...

    async def login(self) -> None: ...
    async def refresh(self) -> None: ...
    async def get_markdown(self, document_id: str) -> MarkdownDocument: ...
    async def list_jurisdictions(self) -> list[Jurisdiction]: ...
    async def list_portals(self, *, iso: str) -> list[Portal]: ...
```

`__aenter__` lazy-logs in on first use and opens an `httpx.AsyncClient`;
`__aexit__` closes it. Outside an `async with`, the client may also be
managed manually via `await client.aclose()`.

## Auth and refresh

Login lives on a different host (`https://filerskeepersapi.co/auth/login`)
from the data plane (`https://api.lawstronaut.com/v2`). The two hosts
are independent constructor parameters so tests point at a fixture
server. The login response nests the bearer two levels deep
(`payload.data.token.refresh_token`); `expires_in` is seconds (default
1800).

**Pre-emptive refresh at 25 min into a 30-min TTL.** A monotonic clock
(injectable, defaults to `time.monotonic`) records `_token_expires_at`
on login; calls check `_clock() >= _token_expires_at - REFRESH_BUFFER_S`
(60 s by default — i.e. refresh at the 29-minute mark, well before the
30-minute hard expiry) and trigger `refresh()` if so. `refresh()` is
just `login()` re-issued; the auth side does not expose a separate
refresh-token endpoint that works (the documented URL has a double
slash and the bearer returned by login already serves the role of a
refresh token — see memory `lawstronaut-api-key-facts`).

The 25-minute target the spec calls for is satisfied by setting
`REFRESH_BUFFER_S = 300` at construction time. The constant defaults
to 300 (a 25-minute refresh on the 30-min TTL) but is overridable for
tests that want a shorter window. The clock is `monotonic`, not wall —
NTP jumps and DST do not invalidate cached tokens.

**One refresh under contention.** A per-instance `asyncio.Lock` guards
the refresh path. Concurrent callers see exactly one refresh:

1. Each call enters `_ensure_fresh_token()`.
2. The first call into the lock observes the expired flag, calls
   `_do_refresh()`, updates `_token_expires_at`, releases.
3. Subsequent waiters acquire, re-check the flag (now fresh), release
   without a second refresh.

The lock is constructed in `__init__` per instance (not at module
import) so multiple event loops do not see a lock bound to a different
loop. (Trap caught by WU3.0's spike and by general pytest-asyncio
practice — `asyncio.Lock()` is event-loop-bound.)

## Quirk tolerance

Three documented Lawstronaut quirks (see `docs/api/operational-notes.md`)
are tolerated by a thin normalisation layer:

1. **`content_markdown` vs `markdown`** — the docs claim the markdown
   field is `markdown`; the live API returns `content_markdown`. The
   pydantic DTO `MarkdownDocument` reads `content_markdown` first and
   falls back to `markdown` so either shape parses. The first row's
   keys observed live were exactly `["document_id", "content_markdown"]`.
2. **`document_id` is sometimes string, sometimes number.** Coerce to
   `str` at the DTO boundary regardless of upstream type. Pydantic
   v2's `coerce_numbers_to_str = True` (or an equivalent validator)
   handles it.
3. **Malformed `publication_date` milliseconds** — values come back as
   `"2026-06-03T00:00:000Z"` (three zeros where ISO 8601 expects three
   digits of milliseconds — invalid). A custom parser
   (`normalise_publication_date`) normalises `T00:00:000Z` → `T00:00:00Z`
   before `datetime.fromisoformat`. Returns `None` if the input is
   missing or unparseable after normalisation rather than raising —
   downstream change-detection code already treats the field as a
   hint.

A fourth not-quite-a-quirk that this client also handles defensively:
`total_links` arriving as a string on `/v2/portals`. The `Portal`
DTO declares it as `int | str | None` and coerces at parse time.

## Retry policy

`stamina.retry` decorates the network call boundary. Transient HTTP
errors (5xx, network timeouts, 429) retry with exponential backoff:

```python
@stamina.retry(on=_TransientError, attempts=4, wait_initial=0.5, wait_max=8.0, wait_jitter=0.2)
async def _request_json(self, method, path, **kwargs) -> dict[str, Any]: ...
```

`_TransientError` is an internal exception class the client raises on
status codes that should retry. 4xx other than 429 raise
`LawstronautError` (a permanent failure) and do **not** retry — auth
failures are terminal. The 401 case in particular is **not** a refresh
trigger; if the bearer was rejected, retrying it cannot help, and
silently re-logging in on every 401 hides credential rotation.

`stamina` ≥ 24 ships the modern `on=` keyword; we pin
`stamina>=24.3` to keep the API stable.

## Errors

```text
LawstronautError                 # base
├── LawstronautAuthError         # 401/403 (terminal)
├── LawstronautClientError       # other 4xx (terminal)
└── LawstronautTransientError    # 5xx, 429, timeouts (retried by stamina)
```

Callers (WU3.4's poll body) catch `LawstronautError` and treat as a
poll failure that bumps `failure_count` in the schedule row. Anything
that escapes was unexpected.

## Tests

Hand-rolled JSON fixtures under
`packages/horizons-core/tests/fixtures/lawstronaut/` keep the diffs
plain-text in git. A lightweight in-memory transport (httpx's
`MockTransport`) routes requests by `path + method` to canned
responses. Each documented quirk has at least one fixture:

- `login_response_nested.json` — `data.token.refresh_token` shape.
- `contents_markdown_content_markdown_field.json` — preferred field.
- `contents_markdown_markdown_field.json` — fallback shape.
- `contents_markdown_int_document_id.json` — `document_id` as a
  number.
- `contents_markdown_malformed_pub_date.json` — `T00:00:000Z`.
- `jurisdictions_response.json` / `portals_response.json` — the
  enumeration endpoints.

Tests cover:

1. `login()` extracts the bearer from `data.token.refresh_token`
   and sets `_token_expires_at`.
2. **Pre-emptive refresh**: with an injected clock, at `expires - 301 s`
   no refresh fires; at `expires - 299 s` exactly one does.
3. **Concurrent contention**: N concurrent `get_markdown()` calls
   when the token is past its refresh threshold trigger exactly one
   `_do_refresh()` invocation.
4. **Stamina retry on 503**: one 503 followed by a 200 succeeds;
   four 503s in a row raise `LawstronautTransientError` after the
   configured backoff.
5. **No retry on 401**: a 401 raises `LawstronautAuthError`
   immediately; the `MockTransport` records exactly one request.
6. **Quirk normalisation**: each fixture is asserted to parse and
   the normalised field has the expected type / value.
7. **Sweeps**: `list_jurisdictions()` and `list_portals(iso=...)`
   return the documented shape, and `list_portals` raises if `iso`
   is omitted (because the live API returns 400 without it).

## Out of scope

- **Caching** — the client returns whatever the API returns; WU3.4
  layers any caching upstream.
- **`/v2/content/{id}/{version}` deep dive** — the endpoint returns
  empty `data` for the IDs we tried; documented as an open API
  question. Not the client's job to debug.
- **OpenAPI-generated DTOs** — Lawstronaut's spec is gated behind
  auth; we hand-roll until that changes.
- **Pagination helpers for `/contents`** — `get_markdown` fetches
  one document at a time; pagination iteration belongs to whoever
  is sweeping a corpus and is not on WU3.2's surface.
