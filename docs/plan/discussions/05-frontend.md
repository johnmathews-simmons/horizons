# Frontend / SPA recommendations

*Discussion 05 — manual run 2026-06-04T15:11:27Z.*

SPA is architecturally a client of the public REST API (doc 4), static bundle from Azure Blob + CDN. Primary user is a senior data engineer stronger on backend — framework ergonomics outweigh raw bundle-size wins. Tags: `[VERIFIED]` (doc constraint or current external fact) / `[SUSPECTED]` (judgement).

---

## A. Framework, components, styling

**1. Framework: React 19 + Vite. [SUSPECTED]**

The static-bundle constraint rules nothing out — SvelteKit `adapter-static`, Astro, React+Vite, SolidStart all build to plain assets. Deciding factors are ecosystem and ergonomics for clause-diff, tables, dashboards, admin.

- **React+Vite** has the deepest ecosystem for every category here: TanStack Query/Router, shadcn/ui, react-diff-view, recharts/visx, headless tables. shadcn/ui is the default for new React projects in 2026 ([shadcn changelog](https://ui.shadcn.com/docs/changelog/2026-02-radix-ui)).
- **SvelteKit** wins bundle size and TTI (Svelte 5 ~2–5 KB vs React 19 ~42 KB gzipped, ~33% faster first paint per [tech-insider benchmarks](https://tech-insider.org/svelte-vs-react-2026-2/)). Matters for marketing sites; not for an authed B2B dashboard on corporate desktops.
- **Astro** is content-first; wrong for an authed SPA.
- **Solid** has tiny bundles, thin ecosystem — risky when engineer is backend-strong and demo is ~7 weeks out.
- **Vue** has weaker headless-table / diff coverage than React.

Asymmetric risk is "I can't find a good component for X" — React minimises that.

**2. Components: shadcn/ui (Radix primitives, copy-in not npm-installed). [SUSPECTED]**

Feb 2026 release consolidated Radix into a single `radix-ui` package and added a Visual Builder. Two reasons:

- **Copy-in pattern.** Components live in our repo as ordinary files — no opaque dependency, easy to modify when the admin "support view" needs a non-standard treatment.
- **Radix accessibility** is solid out of the box (focus, keyboard, ARIA) — otherwise hand-rolled.

Radix maintenance has slowed since the WorkOS acquisition; Base UI (MUI-maintained) is more active and shadcn supports it ([PkgPulse](https://www.pkgpulse.com/guides/shadcn-ui-vs-base-ui-vs-radix-components-2026)). Default Radix; revisit at end of demo prep.

**3. Styling: Tailwind v4. [VERIFIED]**

Tailwind v4 stable, v4.2/4.3 shipping in 2026 ([v4.0 release](https://tailwindcss.com/blog/tailwindcss-v4)). Zero-config (`@import "tailwindcss"`), CSS-native `@theme`, ~5× faster builds. shadcn targets Tailwind. CSS Modules / vanilla-extract are fine but more boilerplate.

---

## B. Routing and state

**4. Router: TanStack Router. [SUSPECTED]**

TanStack Router is stable as of 2026 with active beta releases ([TanStack Router releases](https://github.com/TanStack/router/releases), [TanStack 2026 guide](https://www.codewithseb.com/blog/tanstack-ecosystem-complete-guide-2026)). Type-safe params + search params and first-class integration with TanStack Query (loader → query prefetching) is exactly the shape this app wants: every route corresponds to a scoped query (e.g. `/documents/$documentId/changes?from=X&to=Y` ↔ differential primitive). React Router works fine but you'd hand-roll the type-safety we get for free here.

**5. Server state: TanStack Query. [VERIFIED]**

12M weekly downloads, paired natively with the router ([TanStack Query](https://tanstack.com/query/latest), [TanStack Router/Query integration](https://tanstack.com/router/latest/docs/integrations/query)). Built-in cache, stale-while-revalidate, pagination, mutation invalidation — all of which the discovery primitive (polling-friendly cheap query) and watchlist mutations need. SWR is fine but less feature-dense. Framework-native `load` is unavailable here (no SvelteKit).

**6. Client state: Zustand for shared UI state; URL + TanStack Query for everything else. [SUSPECTED]**

90% of app state is **server state** (handled by TanStack Query) or **URL state** (router search params: filters, scope, time-window). For the remaining slice — auth token, current user, theme, diff-view preference toggles — use Zustand. Redux Toolkit is overkill; Jotai is fine but Zustand is simpler and the user is backend-strong. Avoid Context-based global state.

---

## C. Auth on the client

**7. Token storage: short-lived access token in memory, refresh token in `HttpOnly; Secure; SameSite=Lax` cookie. [VERIFIED]**

This is the post-XSS-decade IETF-leaning consensus ([Wisp blog: token storage](https://www.wisp.blog/blog/understanding-token-storage-local-storage-vs-httponly-cookies), [Pivot Point Security](https://www.pivotpointsecurity.com/local-storage-versus-cookies-which-to-use-to-securely-store-session-tokens/)). localStorage is XSS-exfiltratable in full; one careless `dangerouslySetInnerHTML` or compromised dependency exposes every token.

Concretely:
- Access token (15 min TTL): module-level variable in an auth service; reissued via refresh endpoint on 401 or proactively before expiry. Lost on reload — fine, the refresh cookie mints a new one.
- Refresh token (7–30 day TTL): set by the API as `HttpOnly; Secure; SameSite=Lax; Path=/auth/refresh`. JS cannot read it.
- CSRF: `SameSite=Lax` covers most cases; add a CSRF token header on state-changing endpoints as defence-in-depth.

This is **a constraint on the public API** (doc 4 currently says "stores the token client-side for the session" — should be tightened): the API must support cookie-set refresh in addition to the JSON token response programmatic customers use. Two auth modes against one login endpoint, switched by a `mode=cookie|json` request flag or by client type.

**8. API base URL: runtime `/config.json`. [SUSPECTED]**

Build-time env vars are tempting (one Vite build, one Blob upload). But: we want the *same* static bundle to deploy to dev/staging/prod against different API hosts, and we want admins to be able to point the SPA at a different API for support. Ship `index.html` + bundle + a small `/config.json` fetched on boot; bundle stays cacheable forever, `/config.json` has a short TTL. Cost is one extra request at boot; benefit is dev/staging/prod sharing artifacts.

---

## D. The clause-diff UX (headline moment)

**9. Diff renderer: client-side using `diff-match-patch`. [SUSPECTED]**

The API already returns `before_text` and `after_text` per `change_events` row (doc 4 §Public API): "Differential responses are assembled from precomputed `change_events` rows and the inline clause text on each side." Server-side HTML diff rendering would add work to the API hot path and bake formatting choices into the contract.

`diff-match-patch` is Google's library — 3.3M weekly downloads vs jsdiff's 20K ([npmtrends](https://npmtrends.com/diff-match-patch-vs-jsdiff-vs-text-diff)). Both have low recent commit activity; diff-match-patch is "done" rather than abandoned. For prose-style legal clauses, word-level diff with `diff_main` + `diff_cleanupSemantic` produces the readable output we want.

Wrap in `react-diff-view` or a thin custom component to render the operation list. The result is plain spans with classes Tailwind can theme.

**10. Confidence affordances. [SUSPECTED]**

`alignment_confidence` is a raw float in [0,1] per doc 2 §"Output shape". UI surface:

- **Inline badge per changed clause.** Small pill next to the change-type chip showing e.g. `0.87`. Hover/click reveals a tooltip: "Matched by content similarity (Jaccard 0.87). Lower confidence than source-ID or title+content matches." Two decimal places, not a bucket label — doc 2 is explicit that we don't bucket.
- **Default filter.** Suppress `MOVED` and below-threshold matches by default (per doc 2 §"Change types and confidence" and CLAUDE.md). Filter chips in the toolbar make this visible: "Hiding 12 MOVED, 4 below threshold" with click-to-toggle. Threshold is the runtime-tunable config value.
- **Review pane.** A separate "Review flagged" tab/route lists below-threshold matches as a single review queue, with side-by-side text and accept/reject (accept/reject is post-demo; for demo just show the queue).
- **Confidence colour ramp on the badge.** Red <0.5, amber 0.5–0.8, green ≥0.8 — non-decorative use of colour, paired with the numeric value so colour-blind users get the same information.

The threshold itself must be surfaced as a slider somewhere in the admin/settings UI (CLAUDE.md: "experimental tuning parameters live as runtime-tunable config… surfaced in the UI").

**11. Side-by-side vs unified: default side-by-side, toggle to unified. [SUSPECTED]**

Side-by-side reads better for prose clauses (legal text is sentence-heavy, not line-heavy like code) and the demo audience will see it first. Unified diff is denser and better for scanning many small changes — provide as a toggle in the diff header. Persist the choice per user via the dashboard-preferences endpoint (doc 4 mentions saved-query / dashboard payloads — extend to diff prefs).

---

## E. Admin views

**12. System-health dashboard. [SUSPECTED]**

Doc 4 §Public API enumerates the admin-only data: ingestion health across all curated documents, scheduler status, recent `ingestion_incident` rows, aggregate stats. At-a-glance shape:

- **Top row, 4 KPI cards:** documents tracked, polls in last 24h (with success rate), changes detected in last 24h, open incidents.
- **Coverage table:** per-jurisdiction × per-sector grid; cell = `(docs, last_scanned median, % polls succeeded in 24h)`. Click into a cell drills to a per-document table.
- **Ingestion timeline:** stacked bar over last 7 days — polls per day, broken down `unchanged / changed / failed`. Recharts or visx; not d3 — too low-level.
- **Incidents list:** sortable table of `ingestion_incident` rows with retry status, last error, document, run id.
- **Scheduler view:** next-polled-at queue, top 20 most overdue documents.

No charts that need real-time websockets — the API is poll-based and this matches doc 4's "pull-based dashboard surfacing only".

**13. Admin-as-client support view: persistent banner + reframed chrome + explicit exit. [SUSPECTED]**

CLAUDE.md is explicit that this must be unmistakable. Concrete pattern:

- **Persistent top banner** in a high-contrast non-product colour (suggest amber `bg-amber-500` — neither error red nor success green, signalling "different mode"). Text: `Support view — viewing CLIENT_NAME's dashboard as admin@horizons.example.` Sticky, full width, ~36px tall, present on every route.
- **Banner contains an "Exit support view" button** that returns to the admin dashboard (calls a `/admin/support/exit` or just drops the impersonation token).
- **Sidebar/topbar adopts a tinted border** in the same amber, so even partial screenshots reveal context.
- **Browser tab title prefixed:** `[SUPPORT] CLIENT_NAME — Horizons`.
- **No write actions** in support view (read-only; CLAUDE.md treats admin-as-support as a distinct audited code path).
- The doc 4 open question "impersonation vs role bypass" affects the *token* the SPA uses but not the chrome — chrome treatment stays the same either way.

---

## F. Repo layout and build

**14. SPA lives at `/webapp/`. [SUSPECTED]**

Doc 4 calls the service "Webapp" by name — match the doc. `/frontend/` would be fine too; `/webapp/` keeps the term aligned with the architecture doc and makes the three-service shape obvious as sibling top-level dirs (`/ingestion/`, `/api/`, `/webapp/`).

**15. CI build. [SUSPECTED]**

- **Node 22 LTS** (active LTS as of 2026; required by current Vite/Tailwind toolchains).
- **Build:** `pnpm install --frozen-lockfile && pnpm build` (pnpm over npm/yarn — faster, content-addressed store; specify pnpm version via `packageManager` field).
- **Output dir:** `webapp/dist/`.
- **Artifact:** upload `webapp/dist/` to Azure Blob Storage container (`$web` static-site container or equivalent), then issue a CDN purge for `/index.html` and `/config.json` only — everything else is hashed-filename and cacheable forever.
- **GHCR rule (global CLAUDE.md):** the webapp itself is a static bundle, not a Docker image — the GHCR rule doesn't apply unless we wrap it in nginx for some reason. Don't.
- **Per-PR preview deploys** to a per-PR Blob path — cheap, makes the design reviewable.

---

## G. Risks

**16. Top 3 risks specific to the SPA.**

- **Token refresh during a long-running differential query. [SUSPECTED]** A corpus-scope differential query over 6 months of EU finance laws can take a few seconds; if the access token expires mid-flight, the API returns 401 and we lose the response. Mitigation: refresh proactively when access token has <2 min left (background timer), and a single-flight refresh queue so concurrent 401s trigger one refresh + retry, not N. Implement as a Query global `onError`/retry hook.
- **CDN cache invalidation after deploy. [SUSPECTED]** Stale `index.html` cached at the CDN edge serves last week's bundle that references deleted hashed JS chunks → blank screen for a portion of users. Mitigation: hash all assets, set `index.html` and `config.json` to `Cache-Control: no-cache, max-age=0`, issue explicit CDN purge for those two files on deploy. Add a build-time version-stamp endpoint and a client-side check that triggers a soft reload on mismatch.
- **Rendering large documents without freezing the UI. [SUSPECTED]** The Albanian fixture is 3.8 MB; some Acts will have thousands of clauses. Rendering them all into the DOM at once stalls the main thread. Mitigation: virtualise the clause list (`@tanstack/react-virtual`); render diffs lazily (only as a clause scrolls into view); render the diff itself off the main thread via a Web Worker for the largest cases (`diff-match-patch` runs fine in a Worker). This is also relevant for the "review flagged" pane if a single change set is large.

Sources:
- [SvelteKit adapter-static docs](https://svelte.dev/docs/kit/adapter-static)
- [shadcn/ui changelog (Feb 2026 Radix unification)](https://ui.shadcn.com/docs/changelog/2026-02-radix-ui)
- [shadcn vs Base UI vs Radix 2026 (PkgPulse)](https://www.pkgpulse.com/guides/shadcn-ui-vs-base-ui-vs-radix-components-2026)
- [Tailwind v4.0 announcement](https://tailwindcss.com/blog/tailwindcss-v4)
- [Tailwind v4.2 (Laravel News)](https://laravel-news.com/tailwindcss-4-2-0)
- [TanStack 2026 guide](https://www.codewithseb.com/blog/tanstack-ecosystem-complete-guide-2026)
- [TanStack Router/Query integration](https://tanstack.com/router/latest/docs/integrations/query)
- [TanStack Query](https://tanstack.com/query/latest)
- [diff-match-patch on npm](https://www.npmjs.com/package/diff-match-patch)
- [diff library trends](https://npmtrends.com/diff-match-patch-vs-jsdiff-vs-text-diff)
- [Token storage best practice (Wisp)](https://www.wisp.blog/blog/understanding-token-storage-local-storage-vs-httponly-cookies)
- [Svelte vs React 2026 benchmarks](https://tech-insider.org/svelte-vs-react-2026-2/)
