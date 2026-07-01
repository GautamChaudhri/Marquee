# Marquee Overview

## Summary

Marquee is a FastAPI application for movie-library management around a curated
set of media workflows: AI-assisted poster selection, subtitle and audio track
management, HDR visibility for Radarr-managed files, letterbox detection and
crop-tag application, and durable background job execution. The backend lives
in `marquee/`, the web UI lives in `frontend/`, and long-form reference docs
for the major subsystems live alongside this file in `design/`.

The project is documented as movie-focused today. TV metadata is already stored
and synced, but the poster pipeline, letterbox workflow, and the broader
feature set described here are treated as movie-only until the same behaviors
are implemented and proven for shows.

## Current Product Surface

### Poster pipeline

The poster pipeline fetches poster candidates for a movie, gates out invalid or
off-style options, ranks the survivors, and archives the run for later review.
It uses `marquee/pipeline/runner.py`, `marquee/pipeline/gate.py`,
`marquee/pipeline/features.py`, `marquee/pipeline/scorer.py`, and
`marquee/pipeline/output.py`, with ONNX and OCR helpers under `marquee/ml/`.
See `poster-pipeline.md`.

### HDR overlay

The HDR overlay exposes Radarr metadata that is awkward to inspect in Radarr
itself: dynamic-range tags, custom-format scores, profile-derived HDR targets,
and Dolby Vision fallback analysis. The API surface is in
`marquee/api/routes/hdr.py` and the classification logic is in
`marquee/core/radarr_overlay.py`. The UI lives under `frontend/src/routes/hdr/`.
See `hdr-overlay.md`.

### Letterbox

The letterbox feature detects black bars, stores review state, generates
preview frames, applies or removes MKV crop tags, and can prepare or run
permanent re-encodes. The main modules are `marquee/media/letterbox_detect.py`,
`marquee/media/letterbox_manager.py`, `marquee/core/letterbox_service.py`, and
`marquee/api/routes/letterbox.py`. See `letterbox.md`.

### Audio and subtitles

Subtitle management models physical media files, inventories embedded and
external subtitle tracks, evaluates policy rules, queues durable mutations, and
can call an external Subgen service for AI subtitle generation. The core code
is under `marquee/core/subtitles/`, with related APIs in
`marquee/api/routes/subtitles.py`, `subtitle_policies.py`,
`subtitle_generators.py`, and `media_jobs.py`. See `audio-subs.md`.

### Library and poster deployment

The library layer stores movie records, syncs from Radarr, serves browse and
detail APIs, and manages poster deployment and restoration. The main modules
are `marquee/core/sync_service.py`, `marquee/core/poster_service.py`,
`marquee/api/routes/library.py`, and `marquee/api/routes/webhooks.py`. See
`library.md`.

### Durable jobs

Heavy or long-running work is decoupled from the request loop by the durable
job platform in `marquee/core/jobs/`. Jobs are created by API routes, claimed by
workers, and tracked with persisted progress events and resource reservations.
See `job-platform.md`.

## Architecture

Marquee is organized in a few stable layers:

- `marquee/config.py` provides the singleton `settings` object for runtime
  configuration, path derivation, and Radarr/Sonarr path translation.
- `marquee/core/pipeline_config.py` provides the separate singleton
  `pipeline_settings` for poster-pipeline knobs, while
  `marquee/core/subtitles/config.py` provides `subtitle_settings`.
- `marquee/core/` contains service logic, integrations, jobs, backups, and
  feature-specific orchestration.
- `marquee/pipeline/` contains the poster pipeline stages and run management.
- `marquee/api/routes/` exposes the HTTP surface.
- `frontend/` is a SvelteKit UI that consumes the API for films, pipeline
  review, HDR, letterbox, subtitles, settings, onboarding, and job views.

`marquee/main.py` wires startup and shutdown. On startup it configures logging,
migrates legacy runtime artifacts, initializes the database, connects Radarr,
Sonarr, and TMDB clients when configured, bootstraps job resources, optionally
spawns embedded worker processes, and starts the system-metrics sampler.

## Key Design Decisions

- Gate then rank. The poster pipeline first removes objectively invalid or
  junk candidates and only then ranks the survivors relative to one another.
- Cheapest signal first. Resolution and embedding-driven gates run before the
  more expensive OCR and detail-feature stages.
- Enqueue versus inline. GPU-bound, filesystem-mutating, or long-running work
  is queued through `job_manager`; fast reads, listing, and pure rescoring stay
  inline.
- Path translation is centralized. `settings.translate_radarr_path()` and
  `settings.translate_sonarr_path()` are the only supported way to map *arr
  container paths to host-visible media paths.
- Poster writes have a single write path. `marquee/core/poster_service.py`
  handles deploy and restore operations, including caching and audit history.

## Configuration Families

- `Settings` in `marquee/config.py` controls application runtime, auth,
  database URLs, media paths, clients, rate limits, backup behavior, and
  letterbox settings. Representative knobs include `DB_URL`,
  `JOB_EMBEDDED_WORKERS`, `RADARR_PATH_PREFIX`, `RADARR_MEDIA_PATH`,
  `RATE_PIPELINE_RUN_SECONDS`, `LETTERBOX_ENABLED`, and `MOVIE_POSTER_FORMAT`.
- `PipelineSettings` in `marquee/core/pipeline_config.py` controls pipeline
  models and thresholds. Representative knobs include `SCORER=auto`,
  `HEAD_AUTO_RETRAIN=false`, `OCR_MAX_RESIDUAL_BOXES=0`,
  `PIPELINE_BATCH_MAX_MOVIES=500`, `TMDB_POSTER_SIZE=w500`,
  `K_NEIGHBORS=10`, and `HDR_OVERLAY_DOVI_REQUIRE_FALLBACK=true`.
- `SubtitleSettings` in `marquee/core/subtitles/config.py` controls subtitle
  concurrency, safety policy, and Subgen integration. Representative knobs
  include `SUBTITLE_HARDLINK_POLICY=block`, `SUBTITLE_BACKUP_MODE=none`,
  `SUBTITLE_PREFERRED_LANGUAGES=["en"]`, and `SUBGEN_URL`.

## Implemented Versus Deferred

Implemented today:

- Movie sync, browse APIs, poster deployment and restore history
- Movie poster pipeline runs, review queue, feedback capture, and taste tools
- HDR overlay APIs and frontend page
- Letterbox detect/apply/remove/reencode APIs and frontend page
- Subtitle inventory, policy, mutation, generation, and jobs UI
- Durable background jobs, recurring schedules, system metrics, backups

Deferred or intentionally incomplete:

- TV versions of the poster pipeline, letterbox workflow, and the rest of the
  movie-centric features
- Additional poster sources beyond TMDB in the active pipeline
- Dedicated subtitle batch route family described in older design notes
- Some planned ranking and UX experiments preserved under `design/plans/`

## Development Setup

Backend:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install -e ".[all]"   # when OCR / ML extras are needed
uvicorn marquee.main:app --reload
pytest
ruff check marquee tests
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

For containerized development, use `docker/docker-compose.yml` and the desired
hardware profile. See the project root `README.md` for a concise getting-started
version of these commands.
