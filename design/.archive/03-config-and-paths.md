# Marquee — Configuration and Path Handling

**Status:** Reconciled with the codebase on 2026-06-16.

This document covers the implemented runtime configuration and path-safety
rules. Pipeline-specific knobs live in `marquee/core/pipeline_config.py`; the
full knob reference is `design/08-tuning-knobs.md`.

## Source of Truth

- Application settings: `marquee/config.py`
- Pipeline settings: `marquee/core/pipeline_config.py`
- Subtitle settings: `marquee/core/subtitles/config.py`
- Path validation: `marquee/core/path_utils.py`
- Runtime config API: `GET/PUT /api/config/pipeline`

## Database and Data Paths

`Settings.db_url_resolved` ignores the process working directory and anchors the
SQLite database at:

```text
<project>/data/marquee.db
```

Other derived paths are also project-root anchored:

| Setting/property | Implemented location |
|---|---|
| `poster_cache_path` | `<project>/data/cache/posters` |
| `poster_staging_path` | `<project>/data/staging` |
| `runs_work_path` | `<project>/data/runs/work` |
| `runs_archive_path` | `<project>/data/runs/archive` |
| `letterbox_preview_path` | `<project>/data/cache/letterbox` |

`DB_URL` still exists as a setting, but the engine uses `db_url_resolved`.

## Arr Path Translation

Radarr and Sonarr paths are translated independently.

| Source | Arr prefix | Local prefix | Translation method |
|---|---|---|---|
| Radarr | `RADARR_PATH_PREFIX` | `RADARR_MEDIA_PATH` | `settings.translate_radarr_path()` |
| Sonarr | `SONARR_PATH_PREFIX` | `SONARR_MEDIA_PATH` | `settings.translate_sonarr_path()` |

Example:

```bash
RADARR_PATH_PREFIX=/data/media/Movies
RADARR_MEDIA_PATH=/media/Movies
```

`/data/media/Movies/Dune (2021)` becomes `/media/Movies/Dune (2021)`.

## Path Validation

All filesystem paths from Radarr/Sonarr should pass through
`safe_translate_and_validate(arr_path, source=...)` before Marquee reads or
writes them.

Implemented checks:

- Reject empty paths and null bytes.
- Apply the source-specific Radarr/Sonarr path mapping.
- Resolve the translated path to a canonical absolute path.
- Validate containment under `settings.effective_media_roots` when roots are
  configured.
- Allow all resolved paths only when no media roots or path mappings are
  configured.

`effective_media_roots` is derived from `MEDIA_ROOTS`,
`RADARR_MEDIA_PATH`, and `SONARR_MEDIA_PATH`.

## Application Settings

Implemented in `marquee/config.py`:

| Field | Default | Purpose |
|---|---|---|
| `APP_NAME` | `Marquee` | FastAPI app title |
| `HOST` | `0.0.0.0` | Server bind host |
| `PORT` | `3165` | Server port |
| `DEBUG` | `False` | SQL echo/debug behavior |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FORMAT` | `text` | `text` or `json` logging |
| `DB_URL` | `sqlite+aiosqlite:///./data/marquee.db` | Config field retained; engine uses `db_url_resolved` |
| `DATA_DIR` | `data` | Project-relative data root |
| `CORS_ORIGINS` | localhost Vite/React ports | Allowed browser origins |
| `SHUTDOWN_TIMEOUT_SECONDS` | `10` | Startup/shutdown cleanup timeout |
| `TMDB_READ_ACCESS_TOKEN` | `None` | TMDB API bearer token |
| `FANART_API_KEY` | `None` | Stored but no Fanart client is implemented yet |
| `TVDB_API_KEY` | `None` | Stored but no TVDB client is implemented yet |
| `RADARR_URL` / `RADARR_API_KEY` | `None` | Radarr API integration |
| `SONARR_URL` / `SONARR_API_KEY` | `None` | Sonarr API integration |
| `MEDIA_ROOTS` | `[]` | Additional allowed filesystem roots |
| `SYNC_INTERVAL_MINUTES` | `15` | Stored setting; periodic sync loop is not currently started |
| `SYNC_COOLDOWN_SECONDS` | `300` | Manual `/api/sync/all` rate limit |
| `HEAL_INTERVAL_MINUTES` | `30` | Poster self-heal interval |
| `HEAL_ENABLED` | `True` | Start poster self-heal loop |
| `WEBHOOK_TOKEN` | `None` | Optional webhook query token |
| `WEBHOOK_DRY_RUN` | `False` | Log webhook action without file writes |
| `POSTER_CACHE_DIR` | `data/cache/posters` | Stored field; service uses derived `poster_cache_path` |
| `POSTER_STAGING_DIR` | `data/staging` | Stored field; pipeline uses experiment run dirs |
| `MOVIE_POSTER_FORMAT` | `poster.jpg` | Movie poster filename; supports `{movie_basename}` |
| `SERIES_POSTER_FORMAT` | `poster.jpg` | Existing-series poster filename check |
| `SEASON_POSTER_FORMAT` | `season{season:02d}-poster.jpg` | Existing-season poster filename check |

## Letterbox Settings

Letterbox configuration is implemented in `marquee/config.py`. The feature is
movie-only in the current API.

| Group | Settings |
|---|---|
| Feature flags | `LETTERBOX_ENABLED`, `LETTERBOX_AUTO_APPLY_HIGH`, `LETTERBOX_ASYMMETRIC`, `LETTERBOX_HEAL_ENABLED` |
| Binaries | `LETTERBOX_FFMPEG`, `LETTERBOX_FFPROBE`, `LETTERBOX_MKVPROPEDIT`, `LETTERBOX_MKVMERGE`, `LETTERBOX_CONVERT` |
| Detection | `LETTERBOX_DETECT_METHOD`, `LETTERBOX_TRIM_FUZZ`, sample window settings, cropdetect thresholds, spread/asymmetry thresholds |
| Batch/heal | `LETTERBOX_MAX_PARALLEL`, `LETTERBOX_HEAL_INTERVAL_MINUTES` |

`LETTERBOX_AUTO_APPLY_HIGH` is stored but the current HTTP batch-detect route
does not auto-apply high-confidence results; applying crop tags is done through
`POST /api/letterbox/.../apply`.

## Pipeline Settings

`PipelineSettings` lives in `marquee/core/pipeline_config.py`, is loaded from
`.env`, and is overlaid with `data/pipeline_overrides.json`.

Implemented groups:

- Model/provider: `AI_MODEL`, `EXECUTION_PROVIDER`, `CLIP_BATCH_SIZE`, model
  artifact paths.
- Taste: `K_NEIGHBORS`, `KNN_WEIGHTING`, `KNN_SOFTMAX_TEMP`,
  `TASTE_NEG_WEIGHT`, `PREFERRED_LANG`.
- Optional features: DINO, person/quality, zero-shot axes, KDE calibration,
  learned head.
- Scorer weights and normalization ranges.
- Hard gates, OCR behavior, face/person thresholds, dedup thresholds.
- Feedback-loop paths and learned-head activation thresholds.

Hot updates are exposed by `GET/PUT /api/config/pipeline`. Model identity,
provider, path, and feedback data-dir fields are reported as restart-required
and rejected by the hot update endpoint.

## Subtitle Settings

Subtitle-specific settings live in `marquee/core/subtitles/config.py` as
`subtitle_settings`.

Implemented groups:

- Feature/concurrency: `SUBTITLE_ENABLED`, scan/mutation/generation concurrency.
- Plan safety: plan TTL, file stability delay, hardlink policy, backup mode,
  external delete mode.
- Policy defaults: preferred languages, forced/default/full-dialogue
  protections, preview cue limit.
- Subgen integration: `SUBGEN_URL`, profile/model labels, path translation,
  callback token, timeout, polling.

Subgen is an external service integration; it is not bundled with Marquee.

## Current Gaps

- `[PLANNED]` `SYNC_INTERVAL_MINUTES` exists, but no periodic sync loop is
  started in `marquee/main.py`.
- `POSTER_CACHE_DIR` and `POSTER_STAGING_DIR` are stored settings. Current
  poster services use the derived project-root paths from `settings`
  (`data/cache/posters` and `data/staging`) rather than these env vars.
