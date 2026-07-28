# Marquee Overview

## Summary

Marquee is a FastAPI application that selects poster artwork for a Radarr and
Sonarr library. It fetches candidates from TMDB, gates out invalid art, ranks
what survives against a taste profile learned from the operator's own picks,
and deploys the chosen image into the media folder. Everything heavy runs on a
durable job platform. The backend lives in `marquee/`, the web UI in
`frontend/`, and the reference docs for each subsystem sit beside this file.

The product covers **movies, series, and seasons**. Individual episode artwork
is not part of the scope.

## Scope

Marquee is poster selection only. HDR and Dolby Vision management, letterbox
detection and crop tagging, and audio/subtitle management were all removed to
get this one capability right. If a change appears to need `ffmpeg`,
`mkvmerge`, or track manipulation, it is out of scope.

## Current Product Surface

### Poster pipeline

Fetch candidates for a subject, gate out invalid or off-style options, rank the
survivors, and archive the run for review. The stages live in
`marquee/pipeline/` with the inference wrappers under `marquee/ml/`. See
`poster-pipeline.md`.

### Library and poster deployment

Movie and TV records synced from Radarr and Sonarr, browse and detail APIs, and
the fenced write path that deploys artwork and can restore what it replaced.
The main modules are `marquee/core/sync_service.py`,
`marquee/core/jobs/handlers_poster_mutations.py`, and
`marquee/api/routes/library.py`. See `library.md`.

### Taste and learning

Approvals, overrides, and rejections are recorded as immutable feedback events
and become the evidence behind the exemplar taste profile and the bounded
ranking residual. `marquee/api/routes/feedback.py` and `taste.py` expose the
surface; `marquee/ml/taste_store.py`, `taste_trainer.py`, `residual.py`, and
`taste_map.py` do the work.

### Durable jobs

Heavy or long-running work is decoupled from the request loop by the platform
in `marquee/core/jobs/`, built on PgQueuer. Jobs are submitted by API routes,
claimed by workers, and tracked with persisted progress events streamed to the
UI. See `job-platform.md`.

## Architecture

Marquee is organised in a few stable layers:

- `marquee/config.py` provides the singleton `settings` object for runtime
  configuration, path derivation, and Radarr/Sonarr path translation.
- `marquee/core/pipeline_config.py` provides the separate `pipeline_settings`
  singleton for the poster pipeline's thresholds, weights, and model paths.
- `marquee/core/configuration.py` is the versioned database configuration
  authority: `CONFIGURATION_CATALOG` is the closed set of writable keys, and a
  stored revision containing an unknown key is rejected at startup.
- `marquee/core/` holds service logic, integrations, jobs, and backups.
- `marquee/pipeline/` holds the selection stages and run management.
- `marquee/api/routes/` exposes the HTTP surface.
- `frontend/` is a SvelteKit UI consuming a TypeScript client generated from
  the committed OpenAPI schema.

`marquee/main.py` wires startup and shutdown. On startup it configures logging,
initialises the already-migrated database, connects the Radarr, Sonarr, and
TMDB clients when configured, bootstraps job resources, optionally spawns the
embedded worker and scheduler, and starts the system-metrics sampler.

## Key Design Decisions

- **Gate then rank.** The pipeline first removes objectively invalid or junk
  candidates against absolute thresholds, then ranks the survivors relative to
  one another for that one title.
- **Cheapest signal first.** Resolution and embedding-driven gates run before
  the far more expensive OCR and detail-feature stages.
- **Enqueue versus inline.** GPU-bound, filesystem-mutating, or long-running
  work is queued; fast reads, listing, configuration changes, and pure
  rescoring stay inline.
- **Runner containment.** Pipeline inference never runs on the API or worker
  event loop — it is fenced into its own process group by
  `marquee/core/jobs/internal_runner.py`, which may only write inside its
  attempt workspace.
- **Centralised path translation.** `settings.translate_radarr_path()` and
  `settings.translate_sonarr_path()` are the only supported way to map \*arr
  container paths onto host-visible media paths.
- **One canonical write path.** Poster writes go through
  `marquee/core/jobs/handlers_poster_mutations.py`, including backup,
  validation, publication, projection updates, and audit history.

## Configuration Families

- `Settings` (`marquee/config.py`) — application runtime, auth, database URL,
  media paths, clients, rate limits, backups, and job platform behaviour.
  Representative knobs: `DB_URL`, `API_KEY`, `JOB_EMBEDDED_WORKERS`,
  `RADARR_PATH_PREFIX`, `RADARR_MEDIA_PATH`, `RATE_PIPELINE_RUN_SECONDS`,
  `MOVIE_POSTER_FORMAT`.
- `PipelineSettings` (`marquee/core/pipeline_config.py`) — pipeline models and
  thresholds. Representative knobs: `SCORER=auto`, `OCR_MAX_RESIDUAL_BOXES=0`,
  `PIPELINE_BATCH_MAX_MOVIES=500`, `TMDB_POSTER_SIZE=w500`, `K_NEIGHBORS=10`,
  `GATE_MIN_WIDTH=500`.

## Implemented Versus Deferred

Implemented today:

- Movie, series, and season poster runs, single and batched, with live progress
  and cooperative cancellation
- Review queue, deployment with backup, restore, and immutable feedback capture
- Taste profile building, the taste map, and bounded residual ranking over the
  weighted baseline
- Library sync from Radarr and Sonarr, the durable job platform, recurring
  schedules, backups, and system metrics

Deferred or intentionally off:

- Cold-start onboarding ("Rank Test") is built but gated off
  (`ONBOARDING_ENABLED=false`) until a seed bundle ships
- Poster sources beyond TMDB
- A VLM or judge stage in the pipeline

## Development Setup

See the project `README.md` for the full bare-metal setup. In short:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,cpu]"      # or [dev,nvidia] / [dev,intel]
pip install -e ".[ml,viz]"       # OCR gate + model export + taste map
python -m marquee.db_migration
uvicorn marquee.main:app --reload --port 3165
pytest
ruff check marquee tests scripts
```

```bash
cd frontend
npm install
npm run dev
```

For containerised development, use `docker/docker-compose.yml` with the
matching hardware profile.
