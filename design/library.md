# Library

## Overview

The library layer stores movie metadata, syncs with Radarr, serves browse and
detail APIs, and acts as the anchor point for poster deployment, subtitle
inventory, HDR visibility, and letterbox state. This document focuses on the
movie workflow that is authoritative today.

## Core Modules

- `marquee/models/` defines the ORM models, including `Movie`, `MediaFile`,
  `ArtworkEvent`, `PipelineRun`, `LetterboxState`, and subtitle/job tables.
- `marquee/database.py` owns the async SQLAlchemy engine and session factory.
- `marquee/core/sync_service.py` pulls movie metadata from Radarr and writes it
  into the local database.
- `marquee/core/jobs/handlers_poster_mutations.py` is the canonical fenced
  write path for poster deploy, reset, backup, and restore operations.
- `marquee/core/poster_files.py` contains reusable pure filename and hash
  helpers used by those jobs and read routes.
- `marquee/api/routes/library.py` serves browse and detail APIs.
- `marquee/api/library_serializers.py` enriches raw ORM rows with derived
  fields for the frontend.
- `marquee/api/routes/webhooks.py` responds to Radarr and Sonarr webhook
  events.

## Persistence Model

Production is designed around PostgreSQL via `DB_URL`, while tests and some
development scenarios can use SQLite. The project also carries Alembic
migrations under `alembic/`.

Movie-focused tables that matter most for the current product:

- `Movie` for library metadata and poster state
- `MediaFile` for the physical movie file and technical metadata
- `ArtworkEvent` for deploy/restore audit history
- `PipelineRun` for poster pipeline executions
- `LetterboxState` and `LetterboxEvent` for crop workflow state
- `SubtitleInventory` and `SubtitleTrack` for subtitle visibility

Series, season, and episode tables exist in the schema, but movie workflows are
the actively documented path today.

## Sync Behavior

`SyncService.sync_all()` in `marquee/core/sync_service.py` coordinates:

- movie sync from Radarr
- series, season, and episode sync from Sonarr
- media-file upserts
- HDR and custom-format overlay reference sync
- poster-path checks against files already present on disk

Path translation is centralized in `marquee/config.py`. `RADARR_PATH_PREFIX`
maps *arr-side paths to `RADARR_MEDIA_PATH`, and the same pattern exists for
Sonarr with `SONARR_PATH_PREFIX` and `SONARR_MEDIA_PATH`.

`POST /api/sync/all` is intentionally kept inline because it is network and
database work, not GPU work or filesystem mutation.

## Library APIs

Current browse and detail routes:

- `GET /api/library/movies`
- `GET /api/library/movies/{movie_id}`
- `GET /api/library/movies/{movie_id}/poster`

The movie list supports pagination plus a real set of server-side filters for
title query, poster status, HDR status, letterbox status, availability, and
sort mode. Detail payloads are enriched with media-file linkage and subtitle
coverage where available.

Series and episode browse routes exist, but this doc treats them as supporting
schema and library sync rather than as first-class feature parity.

## Poster Deployment And Restore

Canonical poster mutation jobs are the authoritative write path for movie and
TV artwork.

Their handlers handle:

- filename rendering via `MOVIE_POSTER_FORMAT`
- atomic copies and cached poster bytes under `data/cache/posters`
- SHA-256 and pHash bookkeeping
- `ArtworkEvent` audit rows
- restore from cache or source URL after upgrades or missing-file heal scans

Every mutation is planned, fenced, backed up, validated, projected, and
presented through the durable job lifecycle. Analysis and feedback submit work;
they never write an operator library directly.

## Background Maintenance

Library-facing maintenance includes:

- poster heal scans via the recurring `poster_heal` job
- letterbox heal scans when enabled
- system backup jobs and restore APIs
- worker-driven remediation after webhook events or queued jobs

These workflows rely on the durable job platform described in
`job-platform.md`.

## Important Configuration

Representative library and runtime knobs in `marquee/config.py`:

- Core runtime: `DB_URL`, `DATA_DIR`, `JOB_EMBEDDED_WORKERS`
- Client setup: `RADARR_URL`, `RADARR_API_KEY`, `SONARR_URL`,
  `SONARR_API_KEY`, `TMDB_READ_ACCESS_TOKEN`
- Path translation: `RADARR_PATH_PREFIX`, `RADARR_MEDIA_PATH`,
  `SONARR_PATH_PREFIX`, `SONARR_MEDIA_PATH`, `MEDIA_ROOTS`
- Poster behavior: `POSTER_CACHE_DIR`, `POSTER_STAGING_DIR`,
  `MOVIE_POSTER_FORMAT`
- Auth and request limits: `API_KEY`, `AUTH_ALLOW_LOCAL`,
  `MAX_REQUEST_BODY_BYTES`

## Cross References

- `poster-pipeline.md` for how pipeline runs are produced and reviewed
- `hdr-overlay.md` for synced HDR and custom-format reference data
- `letterbox.md` for crop detection and tag management
- `audio-subs.md` for media-file and subtitle inventory behavior
- `job-platform.md` for recurring maintenance and queued remediation
