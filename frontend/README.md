# Marquee web UI

The frontend is a Svelte 5, SvelteKit 2, and TypeScript application for library browsing, poster-pipeline
review, taste tooling, configuration, and durable job activity. The root
[`README.md`](../README.md) covers the complete application and local backend setup.

## Development

Use Node.js 22 or newer:

```bash
npm ci
npm run dev
```

Browser requests go through the same-origin `/api/*` SvelteKit route. That proxy talks to the
FastAPI service and injects the server-side API key, so credentials never need to enter browser
state. Configure the proxy with `MARQUEE_API_URL` and `MARQUEE_API_KEY`; see
[`frontend/.env.example`](.env.example).

## API contract

`src/lib/api/generated/openapi.ts` is generated from the committed
[`design/api-schema.json`](../design/api-schema.json). After a backend route or schema change, run:

```bash
cd ..
python scripts/export_openapi.py
cd frontend
npm run api:generate
```

Both artifacts are checked for drift in CI.

## Quality gates

```bash
npm run check      # svelte-check
npm run lint       # Prettier + ESLint
npm run test:unit  # Vitest
npm run build      # production adapter-node build
npm run bundle:check # production chunk budget
npm run test:e2e   # Playwright + axe against the synthetic backend
```

The end-to-end harness starts its own synthetic API and production build. It does not contact an
operator database, media library, Radarr, Sonarr, or TMDB.
