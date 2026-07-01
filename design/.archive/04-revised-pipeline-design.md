# Marquee — Revised Poster Pipeline Design

**Status:** Reconciled with the codebase on 2026-06-16.

This document describes the implemented movie poster-selection pipeline in
`marquee/pipeline/` and its API wrappers. It supersedes older centroid/color
histogram designs.

## Current Scope

Implemented:

- Movie-only poster pipeline using TMDB movie posters.
- Live run management, SSE events, archived results, poster serving, and
  archived rescoring.
- Feedback labels, profile updates, optional learned-head retraining, and
  deploy-on-approve/override.

Not implemented in this pipeline:

- TV/series/season poster selection.
- Fanart.tv, TheTVDB, TVmaze, OMDb, AniDB, or Kitsu candidate fetching.
- LightGBM/pairwise learning-to-rank.
- VLM judge reranking.

## Architecture

The pipeline is a GATE-then-RANK system.

- **GATE:** absolute per-candidate rejection for invalid/junk candidates:
  low resolution, low aesthetic/off-style, text-heavy OCR results, optional
  fan-junk combinations.
- **RANK:** relative scoring among survivors for a single movie. The highest
  ranked candidate wins, but lower-ranked survivors remain visible in results.

If all candidates are rejected before ranking, the run status becomes
`flagged_manual`.

## Implemented Stage Order

Implemented in `marquee/pipeline/runner.py`:

```text
fetch
sha256
gate-resolution
style-features
gate-style
ocr
phash
detail-features
rank
output
```

Details:

1. `fetch`: TMDB movie images are fetched, candidate metadata is recorded, and
   w500 images are downloaded to `data/runs/work/<title>/0-originals`.
2. `sha256`: exact duplicate removal.
3. `gate-resolution`: rejects candidates below `GATE_MIN_WIDTH` using TMDB
   metadata before inference.
4. `style-features`: batched CLIP embeddings produce `knn_sim`, aesthetic,
   metadata scalars, zero-shot axes, and optional `official_family`.
5. `gate-style`: aesthetic floor with rescue and KNN/off-style floor.
6. `ocr`: PaddleOCR title/residual detection runs only on style survivors.
7. `phash`: near-duplicate removal after OCR so clean text variants win.
8. `detail-features`: face, title colorfulness, sharpness, text residual,
   palette/composition, quality artifacts, DINO/person/typicality when active.
9. `rank`: `select_scorer()` chooses weighted or learned scorer.
10. `output`: ranked images are placed in `ranked/`; top candidates are
    best-effort re-downloaded at original resolution.

Deployment is not an automatic stage of `run_pipeline()`. Poster writes happen
through `PosterService`, currently from feedback approve/override and restore
flows.

## Feature Vector

Implemented scorer-level fields in `FeatureVector`:

| Feature | Source | Notes |
|---|---|---|
| `knn_sim` | CLIP B/32 embedding against taste profile | Positive KNN minus optional negative-exemplar penalty |
| `aesthetic` | LAION B/32 linear head | Required |
| `title_colorfulness` | OpenCV over OCR title box | Neutral normalization when no title box is found |
| `text_residual` | OCR residual boxes | Normalized as cleanliness |
| `resolution` | TMDB dimensions | Megapixels |
| `sharpness` | OpenCV Laplacian variance | Detail phase |
| `face_area` | SCRFD face detector | Normalized as face absence |
| `provenance` | TMDB votes with shrinkage | TMDB-only today |
| `lang_match` | TMDB language metadata | `preferred`, `neutral`, or other |
| `dino_knn` | DINOv2 ONNX | Optional, hardware/artifact gated |
| `taste_typicality` | KDE calibration over profile feature bands | Optional |
| `quality_artifacts` | Blockiness/noise metrics | Optional |
| `official_family` | CLIP cosine to TMDB primary poster | Optional |

Extended raw features are serialized in `extended_features` and used for
calibration, diagnostics, and learned-head training.

## Taste Profile

The active taste profile is a NumPy `.npz` store loaded by
`NumpyTasteStore`. It contains individual exemplar embeddings, not just a
centroid. Scoring uses KNN over exemplars with configurable weighting:

- `KNN_WEIGHTING=softmax` by default.
- `KNN_SOFTMAX_TEMP` controls nearest-neighbor dominance.
- Negative exemplars are supported via `NEGATIVE_DATA_DIR` and
  `TASTE_NEG_WEIGHT`.

The profile path is model-specific:

```text
marquee/ml/taste_profile.<AI_MODEL>.npz
```

## Scorers

Implemented in `marquee/pipeline/scorer.py`:

- `WeightedScorer`: positive-weight normalized weighted sum.
- `LearnedScorer`: wraps `LogisticHead` when a trained artifact exists.
- `select_scorer()`: `SCORER=auto` uses learned when available, otherwise
  weighted.

[PLANNED] LightGBM and pairwise learning-to-rank are not implemented.

## Hardware and Optional Features

ONNX Runtime provider selection and hardware-derived defaults live in
`marquee/ml/hardware.py`.

Optional features are enabled only when configuration, hardware tier, and model
artifacts agree:

- DINOv2: `DINO_ENABLED`.
- Person detector and artifact metrics: `EXTRA_QUALITY_ENABLED` plus model
  availability.
- Zero-shot axes: loaded from `ZEROSHOT_AXES_PATH` when present.
- Calibration: requires profile calibration arrays.
- Learned head: requires `LEARNED_HEAD_PATH`.

Missing optional features do not fail the run; their weights are redistributed
around available features. Missing required CLIP/aesthetic/taste artifacts fail
preflight.

## API Surface

Implemented routes:

- `POST /api/pipeline/movie/{movie_id}/run`
- `GET /api/pipeline/runs/{run_id}/events`
- `GET /api/pipeline/runs/{run_id}`
- `GET /api/pipeline/runs/{run_id}/posters/{orig_filename}`
- `POST /api/pipeline/runs/{run_id}/rescore`
- `GET /api/movies/{movie_id}/runs`
- `GET /api/movies/{movie_id}/artwork-events`
- Legacy/test route: `POST /api/test/pipeline/movie/{movie_id}`

## Run Robustness

Implemented behavior:

- Per-candidate data failures are recorded and routed to `errored/` where
  possible.
- Systemic preflight failures abort the run.
- Re-runs keep cached `0-originals` downloads but clear generated outputs.
- A durable `PipelineRun` row and archived run JSON survive re-runs of the same
  movie.
- Original-resolution downloads are best-effort; w500 images remain usable.

## Cross-References

- Knobs: `design/08-tuning-knobs.md`
- Extended features/calibration: `design/07-extended-features-and-calibration.md`
- Feedback loop: `design/09-feedback-loop-design.md`
- Model schema: `design/02-model-schema.md`

## Verified Output Behavior

`place_ranked()` defaults to `top_n=5`: it copies all ranked candidates into
the ranked output folder, then best-effort re-downloads original-resolution
bytes for the first five ranked candidates and overwrites those ranked files
when each download succeeds.
