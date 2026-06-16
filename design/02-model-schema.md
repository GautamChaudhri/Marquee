# Marquee — Model Schema Design

**Status:** Phase 1 design — approved, implemented (see marquee/models/)  
**Date:** 2026-06-02  
**Rationale:** Separate tables for movies, series, seasons, and episodes; shared `ArtworkMixin` for poster state; future-proofed for additional artwork types.

---

## Table of Contents

1. [Design Rationale](#1-design-rationale)
2. [Entity-Relationship Diagram](#2-entity-relationship-diagram)
3. [Table Definitions](#3-table-definitions)
4. [ArtworkMixin](#4-artworkmixin)
5. [Index Strategy](#5-index-strategy)
6. [HDR/DV Tracking (Future Feature)](#6-hdrdv-tracking-future-feature)
7. [Pipeline Integration](#7-pipeline-integration)
8. [Future Artwork Types: Two Expansion Paths](#8-future-artwork-types-two-expansion-paths)

---

## 1. Design Rationale

Movies and TV shows share an identical poster pipeline (fetch → dedup → OCR → AI score → deploy). However, they diverge fundamentally in media structure:

| Property | Movie | TV Show |
|---|---|---|
| Files per entity | 1 file | N episodes × 1 file each |
| HDR/DV tracking scope | Check 1 file | Check N episode files |
| Canonical external ID | `tmdb_id` (from Radarr) | `tvdb_id` (from Sonarr) |
| Poster scope | 1 poster | 1 series poster + N season posters |
| API data source | Radarr `/api/v3/movie` | Sonarr `/api/v3/series` + `/api/v3/episode` |
| Quality profile | 1 per movie | 1 per series (applies to all episodes) |

A single `media` table with a `media_type` discriminator forces nullable columns and type-guards everywhere — episode count on movies, movie file path on shows, UNION queries for poster lookups. The small upfront savings of avoiding two tables is lost in downstream complexity.

**Decision:** Four dedicated tables with a shared `ArtworkMixin`.

---

## 2. Entity-Relationship Diagram

```
┌───────────────────────────────────────────────┐
│                ArtworkMixin                    │
│                                                │
│  poster_path           TEXT     NULL = needs   │
│  poster_source         TEXT     tmdb/fanart/etc│
│  poster_source_url     TEXT     original URL   │
│  poster_ai_selected    BOOL     AI picked?     │
│  poster_embedding      BLOB     CLIP vector    │
│  poster_sha256         TEXT     exact hash     │
│  poster_phash          TEXT     perceptual hash│
└───────────────────────────────────────────────┘
         ▲                    ▲                    ▲
         │                    │                    │
┌────────┴────────┐  ┌───────┴────────┐  ┌───────┴──────────┐
│     Movie        │  │    Series       │  │    Season         │
│─────────────────│  │────────────────│  │──────────────────│
│ id          PK   │  │ id         PK   │  │ id            PK │
│ title            │  │ title           │  │ series_id    FK │
│ year             │  │ year            │  │ season_number   │
│ tmdb_id   UNIQUE │  │ tvdb_id  UNIQUE │  │ tmdb_id         │
│ imdb_id          │  │ tmdb_id         │  │                  │
│ folder_path      │  │ imdb_id         │  │ (+ ArtworkMixin) │
│ movie_file_path  │  │ series_path     │  └──────────────────┘
│ radarr_id UNIQUE │  │ sonarr_id UNIQUE│
│ q_profile_id     │  │ q_profile_id    │
│ has_hdr          │  │ season_count    │
│ has_dv           │  │                  │
│                  │  │ (+ ArtworkMixin) │
│ (+ ArtworkMixin) │  └─────────────────┘
└──────────────────┘           │
                               │ 1 : N
                               ▼
                      ┌───────────────────┐
                      │     Episode        │
                      │───────────────────│
                      │ id             PK │
                      │ series_id      FK │
                      │ season_number     │
                      │ episode_number    │
                      │ title             │
                      │ episode_file_path │
                      │ sonarr_ep_id UNIQ │
                      │ has_hdr           │
                      │ has_dv            │
                      └───────────────────┘
```

**Four tables.** Three carry `ArtworkMixin`. Two carry `has_hdr`/`has_dv`.

---

## 3. Table Definitions

### 3.1 Movie

One row per movie in the user's library. Identified by Radarr sync or standalone filesystem scan.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER PK | No | Auto-increment |
| `title` | TEXT | No | From Radarr or filename parsing |
| `year` | INTEGER | No | Release year |
| `tmdb_id` | INTEGER | Yes (UNIQUE) | Universal poster lookup key |
| `imdb_id` | TEXT | Yes | e.g. "tt1234567" |
| `genres` | TEXT (JSON) | Yes | JSON array synced from Radarr (e.g. `["Action","Thriller"]`) — powers label-diversity tracking and taste-map genre coloring |
| `folder_path` | TEXT | No | Full path to movie folder |
| `movie_file_path` | TEXT | Yes | Filename within folder |
| `radarr_id` | INTEGER | Yes (UNIQUE) | Radarr's internal ID — sync dedup + webhook matching |
| `quality_profile_id` | INTEGER | Yes | For HDR/DV feature (Phase N) |
| `has_hdr` | BOOL | Yes | NULL = not checked; True = confirmed present; False = confirmed missing |
| `has_dv` | BOOL | Yes | Same semantics |
| `created_at` | TIMESTAMP | No | Auto |
| `updated_at` | TIMESTAMP | Yes | Auto on update |

Plus `ArtworkMixin` columns.

**Additional fields (letterbox pre-filter, added Phase N):**

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `video_width` | INTEGER | Yes | Encoded video width from Radarr `movieFile.mediaInfo` |
| `video_height` | INTEGER | Yes | Encoded video height from Radarr `movieFile.mediaInfo` |
| `container` | VARCHAR(16) | Yes | Container/extension (`matroska`, `mp4`, …) |

These three fields are populated during Radarr sync and used by the letterbox resolution pre-filter to triage candidates without frame decode. See `design/more-features/04-letterbox-cropping.md`.

### 3.2 Series

One row per TV show.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER PK | No | Auto-increment |
| `title` | TEXT | No | From Sonarr |
| `year` | INTEGER | No | Premiere year |
| `tvdb_id` | INTEGER | Yes (UNIQUE) | Primary ID from Sonarr; required by Fanart.tv |
| `tmdb_id` | INTEGER | Yes | Resolved from Sonarr or TMDB `/find` endpoint |
| `imdb_id` | TEXT | Yes | e.g. "tt1234567" |
| `series_path` | TEXT | No | Root folder path from Sonarr |
| `sonarr_id` | INTEGER | Yes (UNIQUE) | Sonarr's internal ID |
| `quality_profile_id` | INTEGER | Yes | For HDR/DV feature |
| `season_count` | INTEGER | No | Cached from Sonarr |
| `created_at` | TIMESTAMP | No | Auto |
| `updated_at` | TIMESTAMP | Yes | Auto on update |

Plus `ArtworkMixin` columns.

### 3.3 Season

One row per season of a TV show. **New for v2.**

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER PK | No | Auto-increment |
| `series_id` | INTEGER FK | No | References `series.id` |
| `season_number` | INTEGER | No | 1-based season number |
| `tmdb_id` | INTEGER | Yes | TMDB season ID for artwork lookup |
| `created_at` | TIMESTAMP | No | Auto |
| `updated_at` | TIMESTAMP | Yes | Auto on update |

Plus `ArtworkMixin` columns.

**Poster naming convention:** Season posters are deployed to the show's root folder with a season prefix — e.g., `season01-poster.jpg`. The `poster_path` column stores the full absolute path.

### 3.4 Episode

One row per episode file. No poster state — episodes don't get posters.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER PK | No | Auto-increment |
| `series_id` | INTEGER FK | No | References `series.id` |
| `season_number` | INTEGER | No | 1-based |
| `episode_number` | INTEGER | No | 1-based |
| `title` | TEXT | Yes | Episode title |
| `episode_file_path` | TEXT | Yes | Full path to media file |
| `sonarr_episode_id` | INTEGER | Yes (UNIQUE) | Sync dedup |
| `has_hdr` | BOOL | Yes | NULL = not checked |
| `has_dv` | BOOL | Yes | NULL = not checked |
| `created_at` | TIMESTAMP | No | Auto |
| `updated_at` | TIMESTAMP | Yes | Auto on update |

---

## 3.5 PipelineRun

One row per pipeline execution. Written during execution (status=running) and updated on completion. Survives re-runs of the same movie via stable run_id.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `run_id` | VARCHAR(32) PK | No | UUID4 hex — stable identifier for results, events, feedback |
| `movie_id` | INTEGER FK | No | References `movies.id` |
| `status` | VARCHAR(20) | No | `running` | `completed` | `flagged_manual` | `failed` |
| `started_at` | TIMESTAMP | No | Auto |
| `completed_at` | TIMESTAMP | Yes | Set on completion |
| `scorer_name` | VARCHAR(20) | Yes | `weighted` (Phase 0) or `learned` (Phase 1) |
| `counts_json` | TEXT | Yes | Stage survivor counts |
| `archive_path` | TEXT | Yes | Path to archived `pipeline_run.json` (survives re-runs) |
| `output_dir` | TEXT | Yes | Working directory under `experiments/runs/<title>/` |
| `feedback_event_id` | VARCHAR(32) | Yes | Set when feedback is submitted |
| `error` | TEXT | Yes | Error message if failed |

## 3.6 ArtworkEvent

Append-only audit trail of poster lifecycle events — deployments, restorations, and webhook activity.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER PK | No | Auto-increment |
| `movie_id` | INTEGER FK | No | References `movies.id` |
| `action` | VARCHAR(20) | No | `deploy` | `restore` | `restore_failed` | `heal_restore` | `webhook_noop` | `webhook_error` |
| `source` | VARCHAR(20) | No | `pipeline` | `feedback` | `webhook` | `heal` | `manual` |
| `detail` | TEXT | Yes | JSON: old/new paths, cache hit/miss, error |
| `created_at` | TIMESTAMP | No | Auto |

---

## 4. ArtworkMixin

A SQLAlchemy mixin providing poster state for `Movie`, `Series`, and `Season`.

```python
class ArtworkMixin:
    """Poster artwork state for Movie, Series, and Season."""

    poster_path: Mapped[str | None]        # deployed poster file; NULL = needs poster
    poster_source: Mapped[str | None]      # "tmdb" | "fanart" | "tvdb" | "tvmaze"
    poster_source_url: Mapped[str | None]  # original URL for reference
    poster_ai_selected: Mapped[bool]       # did the AI engine choose this?
    poster_user_approved: Mapped[bool]     # True = human approved (vs unreviewed AI pick)
    poster_sha256: Mapped[str | None]      # SHA-256 of deployed poster
    poster_phash: Mapped[str | None]       # perceptual hash
    poster_deployed_filename: Mapped[str | None]  # filename in media folder (e.g. "poster.jpg")
    poster_deployed_at: Mapped[datetime | None]   # when last deployed

    @property
    def needs_poster(self) -> bool:
        return self.poster_path is None
```

**Changes from the initial design:** `poster_embedding` is defined in the base model (`marquee/models/base.py`) as `LargeBinary` but is currently unused — embeddings live in the taste profile `.npz` and per-run caches, not the DB. Added `poster_user_approved`, `poster_deployed_filename`, and `poster_deployed_at` for the poster restoration and feedback loop features (designs 09-10).

**Naming convention:** All columns are prefixed with `poster_`. This makes a future extraction to a separate `artwork` table a clean find-and-replace. See Section 8.

---

## 4b. Letterbox Tables (Added Phase N)

`marquee/models/letterbox.py` — per-movie crop-detection state + audit trail.

### 4b.1 `letterbox_state`

One row per movie. Tracks detection verdict, recommended/applied crop, confidence, and workflow status (Candidates / Tagged / Skipped).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `movie_id` | INTEGER FK UNIQUE | References `movies.id` |
| `status` | VARCHAR(24) | `prefilter_candidate`, `candidate`, `not_letterboxed`, `variable_unsafe`, `tagged`, `skipped`, `ineligible`, `errored` |
| `confidence` | VARCHAR(8) | `high`, `medium`, `low`, `none` |
| `eligible` | BOOL | MKV + writable + has video track |
| `ineligible_reason` | VARCHAR(120) | Why `eligible=false` |
| `source_width` | INTEGER | Encoded width (sync metadata / ffprobe) |
| `source_height` | INTEGER | Encoded height |
| `prefilter_bucket` | VARCHAR(24) | Resolution-only stage-1 triage |
| `prefilter_reason` | VARCHAR(64) | Triage reason |
| `prefilter_aspect_ratio` | FLOAT | Calculated aspect ratio |
| `last_prefiltered_at` | TIMESTAMP | |
| `recommended_crop_top` | INTEGER | Recommended crop px (top) |
| `recommended_crop_bottom` | INTEGER | Recommended crop px (bottom) |
| `aspect_label` | VARCHAR(12) | e.g. `1.78:1`, `2.00:1` |
| `applied_crop_top` | INTEGER | Currently applied (NULL = no tags) |
| `applied_crop_bottom` | INTEGER | Currently applied (NULL = no tags) |
| `detect_method` | VARCHAR(16) | `cropdetect` or `trim` |
| `samples_json` | TEXT (JSON) | Per-timestamp breakdown |
| `reviewed` | BOOL | User-reviewed (no re-flag on rescans) |
| `last_detected_at` | TIMESTAMP | |
| `last_applied_at` | TIMESTAMP | |
| `error` | TEXT | |

### 4b.2 `letterbox_events`

Append-only audit trail (detect / apply / remove / ignore / heal_reapply / error).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `movie_id` | INTEGER FK | References `movies.id` |
| `action` | VARCHAR(20) | Lifecycle step |
| `source` | VARCHAR(20) | `detect`, `api`, `webhook`, `heal`, `manual` |
| `detail` | TEXT | JSON detail |
| `created_at` | TIMESTAMP | Auto |

---

## 4c. Media File Tables (Added Phase N)

`marquee/models/media_file.py` — the physical-file unit of work for subtitle/letterbox mutations.

### 4c.1 `media_files`

One row per physical media file tracked from Radarr/Sonarr (or standalone).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `source` | VARCHAR(20) | `radarr`, `sonarr`, `standalone` |
| `source_key` | VARCHAR(200) UNIQUE | `radarr:movie-file:1234` |
| `source_file_id` | INTEGER | Native *arr file ID |
| `movie_id` | INTEGER FK | References `movies.id` |
| `path` | TEXT | Source-app namespace path (never trusted directly) |
| `relative_path` | TEXT | |
| `size_bytes` | BIGINT | |
| `container` | VARCHAR(20) | |
| `is_active` | BOOL | Current vs historical |
| `last_seen_at` | TIMESTAMP | |
| `last_resolved_path` | TEXT | Diagnostic only |

### 4c.2 `episode_media_files`

Association: which Episode rows live in which physical file. Double-episode files map two rows to one `media_file_id`.

| Column | Type | Notes |
|---|---|---|
| `episode_id` | INTEGER PK (FK) | References `episodes.id` |
| `media_file_id` | INTEGER PK (FK) | References `media_files.id` |
| `created_at` | TIMESTAMP | Auto |

---

## 4d. Media Job Tables (Added Phase N)

`marquee/models/media_job.py` — durable mutation queue that survives restarts.

### 4d.1 `media_batches`

A durable job batch grouping child media jobs (e.g., a library-wide policy apply).

| Column | Type | Notes |
|---|---|---|
| `batch_id` | VARCHAR(32) PK | UUID4 hex |
| `operation` | VARCHAR(30) | |
| `status` | VARCHAR(20) | `planned`, `running`, `completed`, … |
| `requested_count` | INTEGER | |
| `completed_count` | INTEGER | |
| `failed_count` | INTEGER | |
| `request_json` | JSON | |
| `summary_json` | JSON | |
| `paused` | BOOL | |
| `cancel_requested` | BOOL | |
| `created_at` | TIMESTAMP | Auto |
| `updated_at` | TIMESTAMP | Auto on update |

### 4d.2 `media_jobs`

One durable media-file operation (subtitle_scan / remove / embed / extract / …).

| Column | Type | Notes |
|---|---|---|
| `job_id` | VARCHAR(32) PK | UUID4 hex |
| `batch_id` | VARCHAR(32) FK | References `media_batches.batch_id` |
| `media_file_id` | INTEGER FK | References `media_files.id` |
| `operation` | VARCHAR(30) | Mutation type |
| `status` | VARCHAR(16) | `planned`, `queued`, `running`, `succeeded`, `failed`, `cancelled`, `interrupted` |
| `stage` | VARCHAR(30) | Current stage |
| `progress_done` | INTEGER | |
| `progress_total` | INTEGER | |
| `trigger` | VARCHAR(12) | `manual`, `batch`, `policy`, `webhook` |
| `request_json` | JSON | |
| `plan_json` | JSON | |
| `result_json` | JSON | |
| `error_json` | JSON | |
| `input_signature` | VARCHAR(128) | |
| `idempotency_key` | VARCHAR(200) UNIQUE | |
| `cancel_requested` | BOOL | |
| `attempts` | INTEGER | |
| `created_at` | TIMESTAMP | Auto |
| `updated_at` | TIMESTAMP | Auto on update |

### 4d.3 `media_job_events`

Append-only progress event for a media job (durable SSE backing for the UI).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `job_id` | VARCHAR(32) FK | References `media_jobs.job_id` |
| `stage` | VARCHAR(30) | |
| `state` | VARCHAR(20) | |
| `message` | TEXT | |
| `progress_json` | JSON | |
| `created_at` | TIMESTAMP | Auto |

### 4d.4 `media_backups`

Pre-mutation copies of media files, tracked under a hidden `.marquee/backups/` dir.

| Column | Type | Notes |
|---|---|---|
| `id` | VARCHAR(32) PK | |
| `job_id` | VARCHAR(32) FK | References `media_jobs.job_id` |
| `media_file_id` | INTEGER FK | References `media_files.id` |
| `original_path` | TEXT | |
| `backup_path` | TEXT | |
| `original_signature` | VARCHAR(128) | |
| `size_bytes` | BIGINT | |
| `status` | VARCHAR(12) | `available`, `restored`, `deleted`, `missing` |
| `created_at` | TIMESTAMP | Auto |
| `updated_at` | TIMESTAMP | Auto on update |

---

## 4e. Subtitle Tables (Added Phase N)

`marquee/models/subtitle_inventory.py`, `subtitle_managed.py`, `subtitle_policy.py`.

### 4e.1 `subtitle_inventories`

Current subtitle/container snapshot for one media file — a cached view of tracks plus container-level facts.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `media_file_id` | INTEGER FK UNIQUE | References `media_files.id` |
| `file_signature` | VARCHAR(128) | Cheap path+size+mtime+edge-block hash for stale-plan detection |
| `container` | VARCHAR(20) | |
| `duration_seconds` | FLOAT | |
| `audio_streams_json` | JSON | |
| `chapters_count` | INTEGER | |
| `attachments_count` | INTEGER | |
| `coverage_json` | JSON | Language coverage summary |
| `probe_tool_versions_json` | JSON | |
| `scanned_at` | TIMESTAMP | Auto |
| `error` | TEXT | |

### 4e.2 `subtitle_tracks`

One subtitle track (embedded stream or external sidecar). Versioned by parent inventory.

| Column | Type | Notes |
|---|---|---|
| `id` | VARCHAR(32) PK | |
| `inventory_id` | INTEGER FK | References `subtitle_inventories.id` |
| `source` | VARCHAR(10) | `embedded`, `external` |
| `stream_index` | INTEGER | |
| `tool_track_id` | INTEGER | |
| `external_path` | TEXT | |
| `paired_path` | TEXT | |
| `codec` | VARCHAR(40) | |
| `kind` | VARCHAR(20) | `text`, `bitmap`, `teletext`, `unknown` |
| `language_raw` | VARCHAR(40) | |
| `language_tag` | VARCHAR(40) | BCP 47 tag |
| `language_source` | VARCHAR(20) | `metadata`, `filename`, `user`, `unknown` |
| `title` | TEXT | |
| `is_default` | BOOL | |
| `is_forced` | BOOL | |
| `is_sdh` | BOOL | |
| `is_commentary` | BOOL | |
| `is_generated` | BOOL | |
| `size_bytes` | BIGINT | |
| `content_sha256` | VARCHAR(64) | |
| `metadata_json` | JSON | |

### 4e.3 `managed_subtitle_assets`

A cached subtitle Marquee can re-embed after a media-file replacement (subtitle equivalent of PosterService restore).

| Column | Type | Notes |
|---|---|---|
| `id` | VARCHAR(32) PK | |
| `cache_path` | TEXT | Cached bytes on disk |
| `content_sha256` | VARCHAR(64) | |
| `language_tag` | VARCHAR(40) | |
| `title` | TEXT | |
| `kind` | VARCHAR(20) | `text` |
| `is_default` | BOOL | |
| `is_forced` | BOOL | |
| `is_sdh` | BOOL | |
| `is_commentary` | BOOL | |
| `source` | VARCHAR(20) | `external`, `generated`, `extracted` |
| `provenance_json` | JSON | |
| `restore_on_replacement` | BOOL | |
| `active` | BOOL | |
| `created_at` | TIMESTAMP | Auto |
| `last_restored_at` | TIMESTAMP | |

### 4e.4 `managed_subtitle_bindings`

Binds a managed asset to a logical owner (movie or episode).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `asset_id` | VARCHAR(32) FK | References `managed_subtitle_assets.id` |
| `owner_type` | VARCHAR(10) | `movie`, `episode` |
| `owner_id` | INTEGER | PK of the owning row |

### 4e.5 `subtitle_policies`

A language-cleanup policy (allowlist/blocklist over normalized language tags).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `name` | VARCHAR(120) | |
| `enabled` | BOOL | |
| `revision` | INTEGER | Incremented on edit (invalidates stale plans) |
| `mode` | VARCHAR(12) | `allowlist`, `blocklist` |
| `languages_json` | JSON | Language tag list |
| `unknown_action` | VARCHAR(10) | `keep`, `review`, `remove` |
| `protect_forced` | BOOL | |
| `protect_default` | BOOL | |
| `protect_last_full_dialogue` | BOOL | |
| `include_external` | BOOL | |
| `auto_apply` | BOOL | |
| `audit_only` | BOOL | |
| `hardlink_action` | VARCHAR(12) | `block`, `allow_break` |
| `backup_mode` | VARCHAR(16) | `none`, `keep_original` |
| `created_at` | TIMESTAMP | Auto |
| `updated_at` | TIMESTAMP | Auto on update |

### 4e.6 `subtitle_policy_bindings`

Binds a policy to a scope (global / movies / tv / series / item). Resolves most-specific-first.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `policy_id` | INTEGER FK | References `subtitle_policies.id` |
| `scope_type` | VARCHAR(20) | `global`, `movies`, `tv`, `series`, `movie`, `episode`, `media_file` |
| `scope_id` | INTEGER | FK to the scoped entity |

---

## 5. Index Strategy

| Table | Index | Type | Purpose |
|---|---|---|---|
| `movies` | `tmdb_id` | UNIQUE | Primary poster lookup key |
| `movies` | `radarr_id` | UNIQUE | Sync dedup + webhook matching |
| `movies` | `id WHERE poster_path IS NULL` | Partial | Find movies needing posters — O(k) not O(n) |
| `series` | `tvdb_id` | UNIQUE | Primary lookup; required by Fanart.tv |
| `series` | `sonarr_id` | UNIQUE | Sync dedup + webhook matching |
| `series` | `id WHERE poster_path IS NULL` | Partial | Find shows needing posters |
| `seasons` | `(series_id, season_number)` | UNIQUE | One row per season per show |
| `seasons` | `id WHERE poster_path IS NULL` | Partial | Find seasons needing posters |
| `episodes` | `series_id` | Non-unique | Get all episodes for a show |
| `episodes` | `sonarr_episode_id` | UNIQUE | Sync dedup |

**Partial index benefit:** When your library is 80% complete with posters, the `WHERE poster_path IS NULL` index contains only the remaining 20%. Scanning it is O(k) where k = incomplete items, not the full library size.

---

## 6. HDR/DV Tracking (Future Feature)

### How it works

1. **Sync quality profiles from *arr APIs.** A quality profile "wants HDR" if any custom format with "HDR" in its name has a positive score assigned in that profile's format items.

2. **Sync media info from *arr APIs.** Radarr's `/api/v3/movie` returns `movieFile.mediaInfo`. Sonarr's `/api/v3/episode` + `/api/v3/episodefile` returns per-episode media info.

3. **Compare at sync time.** Store the result in `has_hdr`/`has_dv`:
   - `NULL` — "haven't checked yet"
   - `True` — "checked and has it"
   - `False` — "checked and definitely missing"

4. **Frontend queries:**
   ```sql
   -- Movies missing HDR that should have it
   SELECT * FROM movies
   WHERE has_hdr = FALSE AND quality_profile_wants_hdr = TRUE;

   -- Shows with any episode missing HDR
   SELECT DISTINCT s.* FROM series s
   JOIN episodes e ON e.series_id = s.id
   WHERE e.has_hdr = FALSE AND s.quality_profile_wants_hdr = TRUE;
   ```

### Why columns, not a separate table

At this scale (hundreds to low thousands of items), a `has_hdr` boolean column is simpler, faster to query, and perfectly adequate. A separate `media_attributes` join table would be over-engineering for a personal library.

---

## 7. Pipeline Integration

The poster pipeline operates on any entity carrying `ArtworkMixin`:

```
Pipeline.process(movie: Movie):
    candidates = PosterFetcher.fetch(tmdb_id=movie.tmdb_id)
    candidates = Deduper.dedup(candidates)
    candidates = OCRFilter.filter(candidates, title=movie.title)
    winner = AIScorer.select(candidates)
    PosterDeployer.deploy(winner, entity=movie)

Pipeline.process(series: Series):
    # Series poster
    candidates = PosterFetcher.fetch(tmdb_id=series.tmdb_id, media_type="tv")
    ...same pipeline...

    # Season posters
    for season in series.seasons:
        candidates = PosterFetcher.fetch_season(
            tmdb_id=series.tmdb_id,
            season_number=season.season_number,
        )
        winner = ...same pipeline...
        PosterDeployer.deploy_season(winner, season)
```

Season posters use TMDB's `/tv/{id}/season/{season_number}/images` endpoint. The `Season.tmdb_id` column supports this directly.

---

## 8. Future Artwork Types: Two Expansion Paths

In Phase 5 or later, you may want to add backdrops, logos, banners, or other artwork types. The schema supports two clean expansion strategies.

### Current State

Each entity table carries `ArtworkMixin` with `poster_*` columns:

```python
class Movie(Base, ArtworkMixin):
    poster_path: str | None
    poster_source: str | None
    poster_source_url: str | None
    poster_ai_selected: bool
    poster_embedding: bytes | None
    poster_sha256: str | None
    poster_phash: str | None
```

### Path A: Add Columns (Simple)

Add `backdrop_*`, `logo_*` columns to each table:

```python
class Movie(Base, ArtworkMixin):
    # Existing
    poster_path, poster_source, poster_ai_selected, ...
    
    # New
    backdrop_path: str | None
    backdrop_source: str | None
    logo_path: str | None
    logo_source: str | None
```

**Pros:**
- Zero migration complexity — just `ALTER TABLE ADD COLUMN`
- Queries are dead simple: `SELECT backdrop_path FROM movies WHERE id = ?`
- No JOINs or polymorphic dispatch

**Cons:**
- Each new art type adds N columns to 3 tables (N × 3 = column creep)
- Adding "banner" means touching `Movie`, `Series`, and `Season` schemas
- After 5 art types, each table has 35+ artwork columns

**Verdict:** Good for 1-2 additional art types. Becomes messy beyond that.

### Path B: Extract to Separate Artwork Table (Scalable)

Create a dedicated `artwork` table and migrate `poster_*` columns into it:

```sql
CREATE TABLE artwork (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Which entity owns this artwork
    entity_type     TEXT NOT NULL,     -- "movie" | "series" | "season"
    entity_id       INTEGER NOT NULL,  -- PK of the owning row
    
    -- What kind of artwork
    artwork_type    TEXT NOT NULL,     -- "poster" | "backdrop" | "logo" | "banner"
    
    -- Artwork state
    file_path       TEXT,
    source          TEXT,
    source_url      TEXT,
    ai_selected     BOOLEAN DEFAULT 0,
    embedding       BLOB,
    sha256          TEXT,
    phash           TEXT,
    
    -- Season-specific (NULL for movies/series)
    season_number   INTEGER,
    
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(entity_type, entity_id, artwork_type)
);
```

**Migration path:**

1. Run a one-time script that reads `poster_*` from each table and inserts into `artwork`
2. Drop `poster_*` columns from entity tables
3. All code now queries `artwork` instead

**Example queries:**

```sql
-- Get poster for a movie
SELECT * FROM artwork 
WHERE entity_type = 'movie' AND entity_id = ? AND artwork_type = 'poster';

-- Get all artwork for a series
SELECT * FROM artwork 
WHERE entity_type = 'series' AND entity_id = ?;

-- Add a backdrop (no schema change!)
INSERT INTO artwork (entity_type, entity_id, artwork_type, file_path, ...)
VALUES ('movie', 42, 'backdrop', '/Movies/Inception/backdrop.jpg', ...);

-- Find entities needing a specific artwork type
SELECT entity_id FROM artwork 
WHERE entity_type = 'movie' AND artwork_type = 'backdrop' AND file_path IS NULL;
```

**Pros:**
- Adding a new artwork type requires zero schema changes — just INSERT with a new `artwork_type`
- One table to query, one partial index, one dedup pipeline
- Clean separation: domain columns (title, year) stay on entity tables; artwork columns live together
- Scales to unlimited artwork types without column creep

**Cons:**
- One extra JOIN or second query per entity
- Migration script needed when first adding
- `entity_type` string isn't enforced by foreign keys (acceptable at this scale)

**Verdict:** The right choice if you plan to support 3+ artwork types.

### The `poster_` Prefix Makes Either Path Easy

Because all current artwork columns are prefixed with `poster_`, moving to Path B is a find-and-replace:

- `movie.poster_path` → query where `entity_type='movie' AND artwork_type='poster'`
- `series.poster_source` → query where `entity_type='series' AND artwork_type='poster'`

No ambiguity about which columns are artwork vs. domain.

---

## Summary

| Decision | Choice |
|---|---|
| Movies vs Shows | Separate tables — different file structure warrants different models |
| Season posters | Dedicated `seasons` table with `ArtworkMixin` |
| Poster state | `ArtworkMixin` on `Movie`, `Series`, `Season` |
| Column naming | `poster_` prefix — enables clean extraction to `artwork` table later |
| HDR/DV | `has_hdr`/`has_dv` on `Movie` and `Episode`; quality profile tracking deferred to Phase N |
| Future artwork types | Supported via Path A (columns) or Path B (artwork table) — `poster_` prefix makes either trivial |
