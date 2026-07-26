# Marquee

AI-assisted poster selection for Radarr and Sonarr libraries.

## Current Status

In active development. The product surface is deliberately scoped to the poster
pipeline: fetch candidates, score them against a learned taste profile, review,
and deploy. HDR/Dolby Vision management, letterbox detection, and audio/subtitle
management were removed to get that one capability right; they may return as
separate features later.

## What It Does

- Fetches poster candidates from TMDB for movies, shows, and seasons.
- Filters them through a staged pipeline — resolution and style gates, OCR text
  analysis, duplicate and near-duplicate removal, then a ranking model trained
  on your own picks.
- Presents ranked candidates for review, and deploys the chosen artwork into the
  media folder with the original backed up.
- Learns from every decision: approvals, overrides, and rejections feed the
  taste profile and the ranking head.

## Tech Stack

- Backend: FastAPI, SQLAlchemy async, Alembic, PgQueuer
- Frontend: SvelteKit 5
- Database: PostgreSQL
- ML/runtime: ONNX Runtime, PaddleOCR, OpenCV, NumPy

## Quick Start

Backend:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Pick exactly one accelerator extra on Linux — `.[cpu]`, `.[nvidia]`, or
`.[intel]`. They install conflicting `onnxruntime` builds. Apple Silicon needs
none. Add `.[all]` for the OCR/ML extras.

```bash
python -m marquee.db_migration
uvicorn marquee.main:app --reload --host 127.0.0.1 --port 3165
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

Copy `.env.example` to `.env` and fill in `API_KEY`, `DB_URL`,
`TMDB_READ_ACCESS_TOKEN`, and your Radarr/Sonarr connection details before
starting. Model files are not committed — they must be exported or downloaded
into `marquee/ml/models/`.

## Project Structure

- `marquee/pipeline/` — the poster selection stages
- `marquee/ml/` — model wrappers, taste profile, ranking head
- `marquee/core/jobs/` — the durable job platform that runs pipeline work
- `marquee/api/` — FastAPI routes
- `marquee/models/` — SQLAlchemy models
- `frontend/` — SvelteKit web UI
- `alembic/` — database migrations
- `design/` — design docs
- `tests/` — pytest suite

## Documentation

Start with `design/overview.md`, then:

- `design/poster-pipeline.md`
- `design/library.md`
- `design/job-platform.md`

## License

MIT
