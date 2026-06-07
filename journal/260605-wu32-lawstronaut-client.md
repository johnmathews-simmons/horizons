# WU3.2 — Lawstronaut client + token-refresh seam

*Last revised: 2026-06-05.*
*Path: journal/260605-wu32-lawstronaut-client.md.*

*Session 2026-06-05. Branch `worktree-eng-wu3.2-lawstronaut-client` → ff-merged to `main`.*

The async HTTP client WU3.4's poll body will call. Five public methods
per spec (`login`, `refresh`, `get_markdown`, `list_jurisdictions`,
`list_portals`), pre-emptive token refresh at 25 minutes of the 30-min
TTL guarded by a per-instance `asyncio.Lock`, `stamina`-driven retry on
transient HTTP errors, and a pydantic DTO layer that tolerates the four
documented Lawstronaut quirks. Lives in `horizons_core.core.lawstronaut`
so the ingestion worker, future admin tooling, and the seed script
share one client.

## What shipped

1. `packages/horizons-core/src/horizons_core/core/lawstronaut/client.py` — `LawstronautClient` with `login()`, `refresh()`, `get_markdown(document_id)`, `list_jurisdictions()`, `list_portals(iso=...)`. `__aenter__` / `__aexit__` for `async with`; `aclose()` for manual lifetimes. `_request_with_retry` wraps every call in `stamina.retry_context(on=LawstronautTransientError, …)`; `_consume` is the total status-code → taxonomy mapper. Bearer cached as `_bearer: str | None`, expiry as `_token_expires_at: float | None` against an injectable monotonic clock. `_needs_refresh()` is the double-check inside the lock so concurrent callers see exactly one refresh.
2. `packages/horizons-core/src/horizons_core/core/lawstronaut/models.py` — `MarkdownDocument`, `Jurisdiction`, `Portal`, `Credentials`. The four quirks are normalised at the pydantic boundary:
   - `MarkdownDocument` reads `content_markdown` first, falls back to `markdown`.
   - `document_id` is coerced to `str` via the `model_validator(mode="before")` hook (int and str both accepted upstream).
   - `publication_date` runs through `normalise_publication_date()` which strips the malformed `T00:00:000Z` milliseconds shape before `datetime.fromisoformat`. Returns `None` rather than raising on unparseable input.
   - `Portal.total_links` is `int | None` with a `field_validator` that accepts string or int upstream.
   - `Credentials.password` is `SecretStr`; `_wrap_password` validator promotes plain strings on construction.
   - `MarkdownDocument.raw` keeps the original record (deep-copied) so WU3.4 can compute a hash over the canonical bytes, not just the parsed view.
3. `packages/horizons-core/src/horizons_core/core/lawstronaut/errors.py` — shallow exception hierarchy: `LawstronautError` ← `LawstronautAuthError` (401/403, terminal), `LawstronautClientError` (other 4xx, terminal), `LawstronautTransientError` (5xx, 429, network timeouts; retried). WU3.4's poll body catches the base class and treats it as a poll failure that bumps `failure_count`.
4. `packages/horizons-core/src/horizons_core/core/lawstronaut/client.md` — design doc next to the code (same pattern as `loop.md`, `repos/repos.md`). Documents the surface, the auth host vs API host split, the bearer-field-name oddity, the refresh-buffer math, quirk-tolerance rationale, retry policy, error taxonomy, and the test substrate.
5. `packages/horizons-core/src/horizons_core/core/lawstronaut/__init__.py` — re-exports the public surface so callers import from `horizons_core.core.lawstronaut`, not the implementation module.
6. Tests:
   - `packages/horizons-core/tests/test_lawstronaut_client.py` (18 tests) — driven through `httpx.MockTransport`. A `CallRecorder` plus per-handler `static_handler(method, url_substr, status, body)` builds deterministic request-sequence models without `respx` or VCR. Covers: nested-bearer extraction, missing-token rejection, 401 → `AuthError` with no retry, no-refresh at 24:59 of the TTL, refresh at 25:01, exactly-one refresh under 8-coroutine contention, 503 → 200 succeeds, 4-attempt 503 exhaustion, no-retry on 400/401, 429 retries, each of the four quirks explicitly, list-endpoints parse, `list_portals` requires `iso`.
   - `packages/horizons-core/tests/fixtures/lawstronaut/*.json` — nine hand-rolled JSON fixtures. Plain-text diffs, easy to mutate to exercise specific quirks. `login_response_nested.json` / `login_response_nested_b.json` for the two-stage refresh tests; the rest for the data plane.
7. `packages/horizons-core/pyproject.toml` — `httpx>=0.27` and `stamina>=24.3` added to runtime deps. Resolved to `stamina==26.1.0` in the lockfile; `26.x` is the current major and ships `retry_context` unchanged.

Full sweep green: **351 passed / 4 skipped** (was 333+4 on the WU3.3 baseline; +18 from this unit). `ruff check`, `ruff format --check`, `pyright` (0 errors, 15 pre-existing `Stub file not found` warnings for `testcontainers.postgres`), `pre-commit run --all-files`, webapp `lint:check` + `build` + `vitest --run` (3/3) all clean.

## Decisions resolved up-front

Four `AskUserQuestion` (with previews) before the first edit:

1. **HTTP library: httpx.** Pairs natively with stamina, ships PEP-561 stubs, and was already imported (sync) by `scripts/fetch_fixtures.py`. The footprint cost of a second HTTP lib alongside `horizons-ingestion`'s `aiohttp.web` is real but small — `aiohttp` is used as a server there, not a client, so there's no real overlap.
2. **Hand-rolled JSON fixtures, not VCR.** Each documented quirk has its own fixture file under `tests/fixtures/lawstronaut/`. Plain-text diffs in git, deterministic, no network involved, easy to mutate to inject specific bad shapes. VCR's auto-capture was less useful here than direct control over the bytes.
3. **Injectable clock, defaults to `time.monotonic`.** Constructor takes `clock: Callable[[], float] | None`; tests pass a mutable closure. No `monkeypatch.setattr("…time.monotonic", fake)`, no real-time sleeps, no shrunken TTLs in fixtures.
4. **Module location: `horizons_core/core/lawstronaut/`.** Matches the existing convention (`core/auth/`, `core/alignment/`). The spec's `core/lawstronaut/client.py` translates cleanly. WU3.4 (ingestion) and future Track 5 callers all reach the same module.

## How the client interacts with WU3.4

The seam WU3.4 plugs into:

```python
async with LawstronautClient(credentials=Credentials(email=…, password=…)) as client:
    doc = await client.get_markdown(document_id)
    if doc is None:
        # API returned empty data — soft "not found"; bump next_poll_at and move on.
        return
    sha = hashlib.sha256(doc.markdown.encode("utf-8")).hexdigest()
    # … hash compare against current version, optionally write a new version.
```

`get_markdown` returns `MarkdownDocument | None`. `None` is the "empty `data` array" case — caller decides whether to log+skip or treat as a scheduling-assumption violation. The DTO carries `markdown` (canonical bytes for hashing), `version`, `publication_date`, `language`, `iso`, and `raw` (the full upstream record, for ETag-equivalent and forensics).

One trade-off worth surfacing for WU3.4: `get_markdown` issues GET `/contents/markdown?document_id=X&limit=1`, the **documented** query-param form. The operational notes record that the live API returned **400** for this shape on 2026-06-04; the path form `/contents/markdown/{document_id}` was untested. The client encodes the documented contract; WU3.4's first integration test against real credentials will reveal which form the live API accepts and we'll adapt the URL construction. Until then, the test seam (URL-substring matching) is agnostic.

## What I considered and didn't do

1. **No separate `refresh()` path against `/auth/refresh-token`.** The documented refresh endpoint has known URL bugs (the docs URL has a double slash — see `lawstronaut-api-key-facts` memory) and the bearer the login response returns at `data.token.refresh_token` already plays the bearer role on subsequent requests. The pragmatic implementation is to re-login — `refresh()` delegates to `login()`. If a real refresh endpoint ships, swap the body.
2. **No `Protocol` for the client interface.** Same call as WU3.3 made for `PollFn`: only one real implementation is planned. Tests use the concrete class with `MockTransport`, not a duck-typed stand-in. Lift to `Protocol` when a second implementation emerges (a `RecordedClient` for replay? a `FailureInjectingClient` for chaos tests?).
3. **No pagination helpers on `/contents`.** `get_markdown` fetches one document at a time; whoever sweeps a corpus owns its own iteration. Adding paginators here would couple the client to specific call patterns.
4. **No retry-after-header parsing on 429.** stamina's exponential backoff is the policy. If the demo period reveals Lawstronaut returns useful `Retry-After` headers, swap `wait_initial` / `wait_max` for a `Retry-After`-aware waiter.
5. **No `/v2/content/{id}/{version}` method.** The endpoint returns empty `data` for the IDs we tried (documented as an open API question in `operational-notes.md`); not the client's job to debug an upstream. Add the method once we know what it returns and what we want from it.
6. **No `LawstronautClient(credentials, …)` builder helpers** (e.g., `from_env()`). The ingestion worker config (WU3.3's `ClaimLoopConfig`) will own credential loading; the client is a pure surface.
7. **No instrumentation hooks.** structlog observability lives at the call-site — adding it to the client itself would mean each caller imports a wider surface. WU3.4's poll body and Track 7's metrics will instrument from outside.

## Gotchas captured

1. **pyright strict on `Any` JSON payloads.** `payload.get("data")` returns `Any`; chaining `.get("token")` triggers `reportUnknownMemberType` even after `isinstance(payload, dict)` narrows. The fix is explicit `cast(dict[str, Any], …)` at each narrowing step. Same trap WU3.4 will hit on the `MarkdownDocument.raw` shape — if it does, the pattern is already in `client._extract_data` to copy.
2. **`from __future__ import annotations` + ruff TC003 + runtime imports.** Same trap as WU3.0 / WU3.3: ruff treats `from collections.abc import Callable` as type-only when it appears only in annotations, even with `__future__.annotations` deferring evaluation. Move under `if TYPE_CHECKING:`. Inside the client we import `time` inside the test helper `_real_monotonic` to dodge the same flag in the test file.
3. **`asyncio.Lock` constructed lazily / per-instance, not at module import.** Repeated WU3.0 lesson: pytest-asyncio creates a fresh event loop per test under `asyncio_mode = "auto"`; a module-level lock binds to whichever loop imports the module first and breaks every subsequent loop. Construct in `__init__`.
4. **`stamina>=24.3` resolved to `stamina==26.1.0`.** The `retry_context` API is stable across 24.x → 26.x; the `on=`, `attempts=`, `wait_initial=`, `wait_max=`, `wait_jitter=` kwargs all carry through. Pin floor at 24.3 to keep the contract honest; the lockfile reflects the actual install.
5. **`httpx.MockTransport` works with `AsyncClient(transport=…)`.** No need for `respx` or a second-level mocking library. A `CallRecorder` keeps a transcript of requests for assertions like "exactly one POST to /auth/login under contention". The `static_handler(method, url_substr, status, body)` pattern emits each canned response once, so a sequence of handlers becomes a deterministic request-sequence model.
6. **The auth host is NOT the API host.** Login is `POST https://filerskeepersapi.co/auth/login`; data plane is `https://api.lawstronaut.com/v2`. Constructor takes them as two independent params (`auth_url`, `base_url`) so test fixtures point at one mock without dragging the other.
7. **`pydantic.SecretStr` displays as `SecretStr('**********')` in `repr`.** Good for accidental logging. The bearer is a plain `str` field on the client (`_bearer`) — keep that out of any future `__repr__`.
8. **stash-merge-pop dance for ff merging with uncommitted WIP.** The main checkout had pre-existing argon2-cffi / pyjwt edits to `horizons-core/pyproject.toml` (looks like WU4.0 prep). The `--ff-only` merge refused with "local changes would be overwritten." Resolution: `git stash push -- <paths>`, ff-merge, `git stash pop`. The pop auto-merged both sets of additive deps cleanly. Worth codifying for any future worktree merge that lands on a non-clean main.

## Test runner notes

- All 18 client tests are `async def` with no decorator (pyproject's `asyncio_mode = "auto"`). They live under `packages/horizons-core/tests/` because the package owns them; no testcontainers, no cross-package fixtures, no integration marker — pure unit tests against `MockTransport`.
- The contention test (`test_concurrent_refresh_under_contention_runs_exactly_once`) does an initial login + GET, pushes the clock past the refresh boundary, then `asyncio.gather`s 8 concurrent `list_jurisdictions()`. Expected POST count: 2 (initial + one contention refresh). The lock's double-check pattern is what makes this property robust.
- The stamina timing knobs (`stamina_wait_initial`, `stamina_wait_max`, `stamina_wait_jitter`) are constructor params so tests can keep retries ~milliseconds. Production callers will use the defaults (0.5 s → 8 s).

## Next session

WU3.4 — per-document poll transaction. Slots into WU3.3's `PollFn` seam and calls this unit's `LawstronautClient.get_markdown`. Its responsibilities (per the improvement plan): (a) fetch the markdown, (b) compute the content hash, (c) extend `valid_to` if unchanged, (d) upload to `originals/<sha256>.md` and open a Postgres transaction wrapping the version row + parsed clauses + alignment output + change events if changed, (e) leave at most one orphan blob on failed runs, reclaimed by a periodic sweep.

Three things WU3.4 should plan around:

1. **Empirical validation of `get_markdown`'s URL form.** First integration call with real credentials will reveal whether the query-param form (`/contents/markdown?document_id=X`) works, or whether we need the path form (`/contents/markdown/{document_id}`), or whether we have to filter by `iso`+`portal` and post-filter on `document_id` (the pattern `scripts/fetch_fixtures.py` uses). If we need the iso+portal form, the schedule row will need iso/portal in scope — touches WU3.5's seed.
2. **Lock-hold during HTTP.** WU3.3's tick holds the SKIP-LOCKED row lock for the entire batch including the poll body. Median latency on the live API is a few hundred ms; one slow fetch with `batch_size=10` blocks nine other rows. If WU3.4 measures this matters, the refactor is local — claim+commit-bump first, polls in separate short transactions.
3. **Handling `get_markdown → None`.** Empty `data` from the API is `MarkdownDocument | None`'s None case. WU3.4 picks the semantics: log + bump `next_poll_at` (a deprecation-style soft delete on the upstream), or write an `ingestion_incident`. The schedule already has the row.
