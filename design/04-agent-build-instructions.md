# Marquee — Agent Build Notes

**Status:** Reconciled with the codebase on 2026-06-16.

This file is no longer a "build this from scratch" instruction sheet. The
poster pipeline and the adjacent feedback/config/taste APIs have been built.
Use this as an implementation map and maintenance checklist.

## Current Poster Pipeline Modules

| Module | Current role |
|---|---|
| `marquee/pipeline/runner.py` | Pipeline execution core, stage timing, run JSON/log artifacts |
| `marquee/pipeline/run_manager.py` | Singleton extractor, run concurrency guard, SSE fan-out, archive loading |
| `marquee/pipeline/deduper.py` | SHA-256 and pHash deduplication |
| `marquee/pipeline/ocr_filter.py` | PaddleOCR title/residual gate and OCR worker management |
| `marquee/pipeline/features.py` | Style/detail feature extraction and embedding cache |
| `marquee/pipeline/gate.py` | Metadata/style/detail hard gates |
| `marquee/pipeline/scorer.py` | Weighted and learned scorer selection |
| `marquee/pipeline/output.py` | Gated/ranked file placement and original-resolution download |
| `marquee/pipeline/retro_features.py` | Feedback-time feature backfill for rejected selections |
| `marquee/pipeline/types.py` | Candidate and feature dataclasses |

The production route is `POST /api/pipeline/movie/{movie_id}/run`. The legacy
test route `POST /api/test/pipeline/movie/{movie_id}` still exists.

## Current Supporting Modules

| Area | Modules |
|---|---|
| ML/runtime | `marquee/ml/embedding.py`, `hardware.py`, `aesthetic.py`, `face.py`, `person.py`, `dino.py`, `zeroshot.py`, `visual_features.py`, `normalize.py`, `calibration.py` |
| Taste and feedback | `taste_store.py`, `taste_trainer.py`, `profile_updater.py`, `feedback_store.py`, `head_trainer.py`, `learned_head.py`, `taste_map.py` |
| Poster writes/restores | `marquee/core/poster_service.py`, `marquee/core/heal.py`, `marquee/api/routes/webhooks.py` |
| Config/API | `marquee/core/pipeline_config.py`, `marquee/api/routes/config.py`, `pipeline.py`, `feedback.py`, `taste.py`, `system.py` |
| Letterbox | `marquee/media/letterbox_detect.py`, `letterbox_manager.py`, `letterbox_preview.py`, `marquee/core/letterbox_service.py` |
| Subtitles | `marquee/core/subtitles/`, `marquee/core/media_jobs/`, subtitle API routes |

## Maintenance Rules

- Keep the poster pipeline movie/TMDB-only unless deliberately adding TV or
  new poster-source support.
- Preserve the GATE-then-RANK split. Gates reject invalid/junk candidates;
  rankers order survivors.
- Keep all feature orientations higher-is-better before they reach the scorer.
- Keep optional features nullable and let scorers redistribute weight around
  missing features.
- Do not make poster writes outside `PosterService`.
- Do not make letterbox writes outside `LetterboxService`.
- Do not make subtitle media mutations outside the media-job/mutation path.
- Keep path inputs from Radarr/Sonarr behind `safe_translate_and_validate()`.
- When changing pipeline output JSON, check feedback, results, rescore, and
  taste-map overlay consumers.

## Current Stage Contract

The implemented stage order is:

```text
fetch -> sha256 -> gate-resolution -> style-features -> gate-style
-> ocr -> phash -> detail-features -> rank -> output
```

Run artifacts are written under:

```text
marquee/experiments/runs/<Movie>/
```

Important folders/files:

- `0-originals/`
- `1-sha256-rejected/`
- `2-ocr-rejected/`
- `3-phash-rejected/`
- `gated/`
- `ranked/`
- `errored/`
- `pipeline.log`
- `pipeline_run.json`

Archived copies live under `data/runs/archive/{run_id}.json`.

## Current API Contract

Pipeline/results:

- `POST /api/pipeline/movie/{movie_id}/run`
- `GET /api/pipeline/runs/{run_id}/events`
- `GET /api/pipeline/runs/{run_id}`
- `GET /api/pipeline/runs/{run_id}/posters/{orig_filename}`
- `POST /api/pipeline/runs/{run_id}/rescore`
- `GET /api/movies/{movie_id}/runs`
- `GET /api/movies/{movie_id}/artwork-events`

Feedback/config/taste:

- `POST /api/feedback`
- `POST /api/feedback/undo`
- `GET /api/config/pipeline`
- `PUT /api/config/pipeline`
- `GET /api/taste/status`
- `POST /api/taste/retrain`
- `GET /api/taste/map`
- `POST /api/taste/map/rebuild`
- `POST /api/taste/map/candidates`
- exemplar image/neighbor routes under `/api/taste/exemplars/...`

## Planned Work

- `[PLANNED]` TV/series/season poster pipeline support.
- `[PLANNED]` Additional poster-source clients beyond TMDB.
- `[PLANNED]` Pairwise/LightGBM ranking.
- `[PLANNED]` A frontend UI consuming the existing backend routes.

## Verified Output Behavior

`marquee/pipeline/output.py` currently downloads original-resolution bytes for
the first five ranked candidates by default. UI copy can describe this as
best-effort: if an original download fails, the ranked candidate remains usable
from the earlier downloaded image.
