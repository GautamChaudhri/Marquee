# Marquee

AI-powered automatic poster finder for Plex and Jellyfin media servers.

**Status: Pre-alpha — Phase 3 (revised AI pipeline)**

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
docker compose -f docker/docker-compose.yml up -d
```

Set `POSTGRES_PASSWORD` in `.env` before starting Compose. The stack starts a
private PostgreSQL database, a migration job, API service, durable worker, and
scheduler. Open http://localhost:3165/health once all services are healthy.

To run Uvicorn directly on the host, first start the database and migration
services, install the refreshed dependencies, and set the localhost `DB_URL`
shown in `.env.example`:

```bash
docker compose -f docker/docker-compose.yml up -d postgres migrate
pip install -e ".[all]"
# One process — the API auto-spawns the durable worker + scheduler as
# supervised child processes (no separate terminals needed).
python -m uvicorn marquee.main:app --host 127.0.0.1 --port 3165 --reload
```

The job worker and scheduler start and stop with the API; on shutdown the whole
process group is reaped, so background work is never left orphaned. Set
`JOB_EMBEDDED_WORKERS=false` to run them as dedicated processes instead — this is
what the Compose stack does, so its API container does **not** double-spawn them.
The schema must be migrated first (the Compose `migrate` service, or
`alembic upgrade head` against a fresh database).

## Design

See `design/` for the project specification and migration notes.
