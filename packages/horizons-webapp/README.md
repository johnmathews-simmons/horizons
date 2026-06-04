# horizons-webapp

The Horizons SPA. Vue 3 + TypeScript + Vite + Pinia + Vue Router + Vitest, styled
with Tailwind v4.

This is a customer of the public REST API exposed by `horizons-api`. No
internal back-channel; the webapp talks to the same endpoints external
integrators do. See `docs/4. services.md` in the repo root.

The webapp is not a `uv` workspace member — it lives alongside the Python
packages under `packages/` but uses npm.

## Setup

```bash
cd packages/horizons-webapp
npm install
cp .env.example .env  # set VITE_API_BASE_URL
```

## Scripts

```bash
npm run dev          # vite dev server
npm run build        # type-check + production build
npm run type-check   # vue-tsc
npm run test:unit    # vitest
npm run lint         # oxlint + eslint --fix
npm run format       # prettier --write src/
```

## API client

`src/api/client.ts` is the single typed wrapper around `fetch`. Endpoint
methods land here as the public API takes shape.
