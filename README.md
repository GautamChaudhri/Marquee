# Marquee

AI-powered media library management for Radarr and Sonarr, centered on a
movie-first poster pipeline and related library tooling.

## Current Status

Marquee is in active development. The documented product surface is movie-only
today; TV support is planned after the movie workflows have reached the desired
level of reliability and quality.

## What It Does

- Runs an AI-assisted poster selection pipeline with OCR, taste matching,
  ranking, review, and deployment workflows.
- Manages audio and subtitle inventories, cleanup policies, mutation jobs, and
  optional AI subtitle generation through an external Subgen service.
- Surfaces HDR and Dolby Vision information from Radarr, and provides
  letterbox detection, crop-tag management, and re-encode planning for movies.

## Tech Stack

- Backend: FastAPI, SQLAlchemy async, Alembic
- Frontend: SvelteKit
- Database: PostgreSQL by default, SQLite in tests and some local scenarios
- ML/runtime: ONNX Runtime, PaddleOCR, OpenCV, NumPy
- Deployment: Docker Compose plus host-run development workflows

## Quick Start

Backend:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install -e ".[all]"   # include OCR / ML extras
uvicorn marquee.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Container stack:

```bash
docker compose -f docker/docker-compose.yml up -d
```

Set any required secrets and client settings in `.env` before starting the
stack. When running the API directly, point `DB_URL` at a reachable database
and run migrations as needed with `alembic upgrade head`.
After pulling the artifact-management taste-page changes, run that migration
before expecting taste profile / learned head management to appear in the UI.

## Project Structure

- `marquee/` - FastAPI app, services, pipeline, ML wrappers, models, and jobs
- `frontend/` - SvelteKit web UI
- `design/` - core design docs, plans, and archived historical notes
- `tests/` - pytest suite
- `docker/` - compose files and hardware profiles
- `alembic/` - database migrations

## Documentation

Start with `design/overview.md`, then use the subsystem docs:

- `design/poster-pipeline.md`
- `design/hdr-overlay.md`
- `design/letterbox.md`
- `design/audio-subs.md`
- `design/library.md`
- `design/job-platform.md`
- `design/timeline.md`

## License

MIT
