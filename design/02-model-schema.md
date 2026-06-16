# Marquee — Model Schema

**Status:** Reconciled with the codebase on 2026-06-16.

The schema is implemented with SQLAlchemy models in `marquee/models/`. Startup
currently calls `Base.metadata.create_all()`; Alembic migrations also exist in
the repository.

## Current Tables

### Library and Artwork

| Model | Table | Purpose |
|---|---|---|
| `Movie` | `movies` | Radarr-synced movie metadata, poster state, encoded video hints |
| `Series` | `series` | Sonarr-synced series metadata and poster state |
| `Season` | `seasons` | Per-season metadata and poster state |
| `Episode` | `episodes` | Per-episode metadata; no poster state |
| `ArtworkEvent` | `artwork_events` | Append-only movie poster deploy/restore/webhook audit trail |
| `PipelineRun` | `pipeline_runs` | One poster-pipeline execution and archived result metadata |

`Movie`, `Series`, and `Season` include `TimestampMixin` and `ArtworkMixin`.
`Episode` only includes `TimestampMixin`.

### Physical Media, Subtitles, and Jobs

| Model | Table | Purpose |
|---|---|---|
| `MediaFile` | `media_files` | Physical movie or episode file tracked from Radarr/Sonarr |
| `EpisodeMediaFile` | `episode_media_files` | Many-to-many link for multi-episode files |
| `SubtitleInventory` | `subtitle_inventories` | Cached subtitle/container snapshot for one media file |
| `SubtitleTrack` | `subtitle_tracks` | Embedded or external subtitle track rows for an inventory |
| `ManagedSubtitleAsset` | `managed_subtitle_assets` | Cached subtitle bytes Marquee can re-embed after replacement |
| `ManagedSubtitleBinding` | `managed_subtitle_bindings` | Binds managed subtitle assets to movies or episodes |
| `SubtitlePolicy` | `subtitle_policies` | Language cleanup policy |
| `SubtitlePolicyBinding` | `subtitle_policy_bindings` | Policy scope bindings |
| `MediaBatch` | `media_batches` | Durable grouping for media jobs |
| `MediaJob` | `media_jobs` | Durable subtitle scan/mutation/generation/restore job |
| `MediaJobEvent` | `media_job_events` | Persisted progress events for media-job SSE |
| `MediaBackup` | `media_backups` | Pre-mutation backup records |

### Letterbox

| Model | Table | Purpose |
|---|---|---|
| `LetterboxState` | `letterbox_state` | Latest movie crop-detection/application state |
| `LetterboxEvent` | `letterbox_events` | Append-only letterbox detect/apply/remove/ignore/error audit trail |

Letterbox state is movie-only in the current schema and routes.

## Entity Relationships

```text
Series 1 ── N Season
Series 1 ── N Episode
Episode N ── N MediaFile through EpisodeMediaFile
Movie 0/1 ── N MediaFile
MediaFile 1 ── 0/1 SubtitleInventory
SubtitleInventory 1 ── N SubtitleTrack
MediaFile 1 ── N MediaJob
MediaJob 1 ── N MediaJobEvent
Movie 1 ── N PipelineRun
Movie 1 ── N ArtworkEvent
Movie 1 ── 0/1 LetterboxState
Movie 1 ── N LetterboxEvent
```

## Important Column Groups

### `ArtworkMixin`

Implemented on `Movie`, `Series`, and `Season`:

- `poster_path`
- `poster_source`
- `poster_source_url`
- `poster_ai_selected`
- `poster_embedding`
- `poster_sha256`
- `poster_phash`
- `poster_user_approved`
- `poster_deployed_filename`
- `poster_deployed_at`

`poster_embedding` exists as a `LargeBinary` column but the active pipeline
uses taste-profile `.npz` files and an embedding cache instead of this column.

### `Movie`

Notable implemented fields:

- Identity: `title`, `year`, `tmdb_id`, `imdb_id`, `radarr_id`, `genres`.
- Filesystem: `folder_path`, `movie_file_path`.
- Video hints used by letterbox prefilter: `video_width`, `video_height`,
  `container`.
- Quality placeholders: `quality_profile_id`, `has_hdr`, `has_dv`.

### `Series`, `Season`, and `Episode`

Sonarr sync creates `Series`, `Season`, and `Episode` rows. Series and seasons
carry poster state, but the implemented poster-selection pipeline and
`PosterService` are movie-oriented today.

`Series.tmdb_id` and `Season.tmdb_id` exist, but current Sonarr sync does not
resolve TMDB IDs for TV entries. It stores Sonarr/TVDB/IMDB metadata and checks
for existing local posters.

### `PipelineRun`

`PipelineRun` stores:

- `run_id`
- `movie_id`
- status: `running`, `completed`, `flagged_manual`, or `failed`
- `started_at`, `completed_at`
- `scorer_name`
- `counts_json`
- `archive_path`
- `output_dir`
- `feedback_event_id`
- `error`

The run archive is loaded by the results/rescore/feedback APIs.

### `LetterboxState`

Implemented status values include:

- `prefilter_candidate`
- `prefilter_unknown`
- `prefilter_skipped`
- `candidate`
- `not_letterboxed`
- `variable_unsafe`
- `tagged`
- `skipped`
- `ineligible`
- `errored`

The row stores confidence, eligibility, source dimensions, prefilter details,
recommended crop, applied crop, detection method, samples JSON, review state,
timestamps, and error text.

### Subtitle and Media Job Tables

Subtitle inventory and mutation are implemented around `MediaFile`, not around
movie/episode tables directly. Plans and jobs store JSON request/plan/result
payloads, file signatures, idempotency keys, cancellation state, and durable
events so job status can survive process restarts.

## Current Indexes and Constraints

Implemented in model declarations:

- Unique external IDs: `movies.tmdb_id`, `movies.radarr_id`,
  `series.tvdb_id`, `series.sonarr_id`, `episodes.sonarr_episode_id`.
- `seasons` has a unique `(series_id, season_number)` constraint.
- `letterbox_state.movie_id` is unique.
- `subtitle_inventories.media_file_id` is unique.
- `media_files.source_key` is unique.
- Partial missing-poster indexes exist on `movies`, `series`, and `seasons`.

## Implemented vs Planned

Implemented:

- Radarr movie sync and Sonarr series/season/episode sync.
- Movie poster pipeline run records.
- Movie poster deploy/restore audit trail.
- Physical media-file tracking for movies and episodes.
- Subtitle inventory, policies, jobs, backups, and managed assets.
- Movie letterbox detection/application state.

[PLANNED] or not currently exposed as a working flow:

- Standalone filesystem scanner and filename/NFO identification.
- Automated TV/season poster selection and deployment.
- Fanart.tv, TheTVDB, TVmaze, OMDb, AniDB, or Kitsu poster-source clients.
- HDR/DV detection from quality profiles and media info.
- Generic artwork table for backdrops/logos/banners.

## Cross-References

- Config and path rules: `design/03-config-and-paths.md`
- Poster pipeline behavior: `design/04-revised-pipeline-design.md`
- Feedback loop: `design/09-feedback-loop-design.md`
- Poster restoration: `design/more-features/01-poster-restoration.md`
- Subtitle management: `design/more-features/03-subtitle-management.md`
- Letterbox feature: `design/more-features/04-letterbox-cropping.md`

## Migration Coverage

Verified on 2026-06-16 by applying Alembic `head` into a temporary SQLite
`DATA_DIR` and comparing the resulting schema to SQLAlchemy metadata. Current
migrations create all model tables with no table or column drift.
