# Marquee — Project Specification

**Status:** Reconciled with the codebase on 2026-06-16.

Marquee is a FastAPI application for local media-library operations:

- movie poster candidate selection from TMDB using local ML features;
- Radarr/Sonarr library sync into a SQLite database;
- poster deployment, cache-backed restoration, webhook handling, and self-heal;
- feedback-driven taste-profile updates and optional learned-head ranking;
- subtitle inspection, planning, mutation jobs, policies, and external Subgen
  integration;
- movie-only letterbox detection and MKV crop-tag application.

## Implemented Product Surface

### Backend

Implemented in `marquee/main.py` and `marquee/api/routes/`:

- `GET /health`
- `POST /api/sync/all`
- library browse routes under `/api/library`
- poster pipeline routes under `/api/pipeline` and `/api/movies`
- feedback routes under `/api/feedback`
- taste status/map/retrain routes under `/api/taste`
- runtime pipeline config under `/api/config/pipeline`
- system status/heal routes under `/api/system`
- letterbox routes under `/api/letterbox`
- subtitle inventory/plan/job/policy/generation routes
- Radarr/Sonarr/Subgen webhook routes under `/api/webhooks`

The app creates the async SQLAlchemy engine lazily, initializes tables at
startup, connects configured Radarr/Sonarr/TMDB clients, and starts background
poster heal, letterbox heal, and subtitle media-job workers when enabled.

### Poster Selection

The implemented poster pipeline is movie-only and TMDB-only. It fetches TMDB
movie posters, runs exact and perceptual deduplication, applies metadata/style
OCR/detail gates, ranks survivors with weighted or learned scoring, and
archives results.

See `design/04-revised-pipeline-design.md` for the authoritative pipeline
details and `design/08-tuning-knobs.md` for runtime knobs.

### Poster Deployment and Restoration

`PosterService` is the single write path for posters. It validates paths,
writes atomically, updates `Movie` poster columns, populates
`data/cache/posters/movies/{tmdb_id}.jpg`, and records `ArtworkEvent` rows.

Radarr webhooks restore movie posters after upgrade downloads. Sonarr webhooks
currently handle test/rename-style behavior and subtitle scan scheduling; they
do not restore series/season artwork after file upgrades because Sonarr file
upgrades do not normally delete series-level poster files.

### Library Sync

Implemented sync sources:

- Radarr `/api/v3/movie` for movies.
- Sonarr `/api/v3/series`, `/api/v3/episode`, and `/api/v3/episodefile` for
  series, seasons, episodes, and physical episode files.

Sync also checks for existing local poster files and upserts `MediaFile` rows.
Standalone filesystem scanning, filename parsing, and NFO parsing are
`[PLANNED]`.

### Subtitle Management

Implemented subtitle features include:

- subtitle inventory scan from ffprobe plus external sidecar discovery;
- language normalization and coverage summaries;
- read-only preview/download endpoints;
- mutation planning for remove/embed/metadata operations;
- durable media jobs with persisted events and SSE;
- optional backups, hardlink protection, stale-plan checks, and atomic replace;
- policies and policy apply/audit endpoints;
- external Subgen generation integration.

See `design/more-features/03-subtitle-management.md`.

### Letterbox Cropping

Implemented letterbox features are movie-only:

- resolution prefilter;
- ffmpeg cropdetect or ImageMagick trim measurement;
- consensus confidence classification;
- preview frame generation;
- MKV crop-tag apply/remove/ignore via `mkvpropedit`;
- tag-drift heal.

See `design/more-features/04-letterbox-cropping.md`.

## Implemented Data Model

The schema uses dedicated tables for library entities, poster provenance,
letterbox state, media files, subtitle inventory, subtitle policies, durable
jobs, and backups. See `design/02-model-schema.md`.

## Configuration

Primary settings are split across:

- `marquee/config.py` for app, database, clients, path mapping, poster,
  webhook, heal, and letterbox settings;
- `marquee/core/pipeline_config.py` for poster pipeline knobs;
- `marquee/core/subtitles/config.py` for subtitle and Subgen knobs.

See `design/03-config-and-paths.md`.

## Implemented Integrations

| Integration | Current status |
|---|---|
| Radarr | Sync, webhook restore, media-file rows |
| Sonarr | Sync, webhook handling, episode media-file rows |
| TMDB | Movie poster candidates and primary-poster lookup |
| Subgen | External subtitle-generation provider integration |
| ffmpeg/ffprobe | Subtitle probe/mutation and letterbox preview/detection |
| mkvtoolnix | Letterbox crop tags and subtitle mutation support |
| PaddleOCR/ONNX Runtime/OpenCV | Poster feature extraction |

## Planned or Not Implemented

These are not current-code claims:

- `[PLANNED]` Web UI. Backend routes are present, but no frontend app is in
  `marquee/`.
- `[PLANNED]` Standalone filesystem scanner, guessit parsing, and NFO parsing.
- `[PLANNED]` Automated TV/series/season poster selection and deployment.
- `[PLANNED]` Fanart.tv, TheTVDB, TVmaze, OMDb, AniDB, Kitsu, TPDb, or MediUX
  source clients.
- `[PLANNED]` Docker images for every hardware profile should be verified
  against `docker/` before being described as production-ready.
- `[PLANNED]` HDR/DV tracking logic from quality profiles.
- `[PLANNED]` Filesystem watcher/inotify integration.
- `[PLANNED]` LightGBM/pairwise ranking and VLM-as-judge reranking.
- `[PLANNED]` Periodic sync loop. `SYNC_INTERVAL_MINUTES` is configured, but
  `main.py` does not currently start a background periodic sync task.

## Deployment Note

Docker hardware profiles are documented generically. Treat profile-specific
GPU/driver/runtime readiness as a host validation step rather than a
production guarantee in this spec.
