# Library

## Overview

The library layer stores the movie and TV metadata Marquee works against, syncs
it from Radarr and Sonarr, serves browse and detail APIs, and is the anchor
point for poster deployment, restore, and audit history.

## Core Modules

- `marquee/models/` defines the ORM models — `Movie`, `Series`, `Season`,
  `Episode`, `MediaFile`, `ArtworkEvent`, `PipelineRun`, `TastePreference`,
  `MlPublication`, `SystemMetrics`, the configuration tables, and the job
  tables.
- `marquee/database.py` owns the async SQLAlchemy engine and session factory.
- `marquee/core/sync_service.py` pulls metadata from Radarr and Sonarr into the
  local database.
- `marquee/core/jobs/handlers_poster_mutations.py` is the canonical fenced
  write path for poster deploy, reset, backup, and restore.
- `marquee/core/poster_files.py` holds the pure filename and hash helpers those
  jobs and the read routes share.
- `marquee/api/routes/library.py` serves browse and detail APIs;
  `marquee/api/library_serializers.py` enriches ORM rows with the derived fields
  the frontend needs.

## Persistence Model

PostgreSQL only, addressed through `DB_URL` with the `asyncpg` driver — there
is no SQLite path, including in tests. The schema is owned by the Alembic
migrations under `alembic/`; production never calls `create_all`.

Tables that matter most for the product:

- `movies`, `series`, `seasons`, `episodes` — library metadata and poster state
- `media_files` — the physical file and its technical metadata
- `artwork_events` — deploy/restore audit history
- `pipeline_runs` — poster pipeline executions and their archives
- `taste_preferences` — exemplar and feedback evidence for ranking

## Sync Behaviour

`SyncService.sync_all()` runs the configured steps and returns a `SyncReport`:

- movies from Radarr, with their movie files
- series, seasons, and episodes from Sonarr
- media-file upserts and poster-path checks against what is on disk

Each phase reports progress through an optional callback (so the job progress
bar narrates what is being synced) and checks a cancellation event between
steps. The path-validation cache is cleared afterwards, so a folder moved in
Radarr between syncs is never served from stale state.

Path translation is centralised in `marquee/config.py`: `RADARR_PATH_PREFIX`
maps \*arr-side paths onto `RADARR_MEDIA_PATH`, and Sonarr has the matching
`SONARR_PATH_PREFIX` / `SONARR_MEDIA_PATH` pair.

`POST /api/sync/all` is deliberately inline — it is network and database work,
not GPU work or filesystem mutation. The same work also exists as the
`library_sync` scheduled job.

## Library APIs

```
GET /api/library/movies
GET /api/library/movies/{movie_id}
GET /api/library/movies/{movie_id}/poster
GET /api/library/series
GET /api/library/series/{series_id}
GET /api/library/series/{series_id}/poster
GET /api/library/series/{series_id}/seasons
GET /api/library/seasons/{season_id}/poster
GET /api/library/episodes/{episode_id}
GET /api/movies/{movie_id}/artwork-events
GET /api/series/{series_id}/artwork-events
```

The list routes support pagination plus server-side filters for title query,
poster status, availability, and sort mode. Detail payloads are enriched with
media-file linkage and run history.

## Poster Deployment And Restore

Canonical poster mutation jobs are the authoritative write path for movie and
TV artwork. Their handlers cover:

- filename rendering via `MOVIE_POSTER_FORMAT`, `SERIES_POSTER_FORMAT`, and
  `SEASON_POSTER_FORMAT`
- atomic copies, with cached poster bytes under `data/cache/posters`
- SHA-256 bookkeeping
- `ArtworkEvent` audit rows
- restore from cache or source URL after an upgrade or a missing-file heal scan

Every mutation is planned, fenced, backed up, validated, projected, and
presented through the durable job lifecycle. Analysis and feedback *submit*
work; they never write into an operator library directly.

## Background Maintenance

- `poster_heal` — recurring scan that repairs posters missing from disk
- `poster_rescan` — reconciles library poster state after external changes
- `backup_create` — snapshot of the database and managed data directory
- `job_retention_purge` and `system_metrics_purge` — evidence hygiene

All of these run on the platform described in `job-platform.md`.

## Important Configuration

Representative knobs in `marquee/config.py`:

- Core runtime: `DB_URL`, `DATA_DIR`, `JOB_EMBEDDED_WORKERS`
- Clients: `RADARR_URL`, `RADARR_API_KEY`, `SONARR_URL`, `SONARR_API_KEY`,
  `TMDB_READ_ACCESS_TOKEN`
- Path translation: `RADARR_PATH_PREFIX`, `RADARR_MEDIA_PATH`,
  `SONARR_PATH_PREFIX`, `SONARR_MEDIA_PATH`, `MEDIA_ROOTS`
- Poster behaviour: `POSTER_CACHE_DIR`, `POSTER_STAGING_DIR`,
  `MOVIE_POSTER_FORMAT`, `SERIES_POSTER_FORMAT`, `SEASON_POSTER_FORMAT`
- Auth and request limits: `API_KEY`, `AUTH_ALLOW_LOCAL`,
  `MAX_REQUEST_BODY_BYTES`

## Cross References

- `poster-pipeline.md` — how pipeline runs are produced and reviewed
- `job-platform.md` — recurring maintenance and queued mutations
- `overview.md` — application-level architecture
