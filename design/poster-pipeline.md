# Poster Pipeline

## Overview

The poster pipeline selects the best poster for a movie by applying a strict
gate-then-rank workflow. It fetches candidate posters, removes obviously bad or
invalid options, computes style and detail features, ranks the survivors with
either a weighted heuristic scorer or a learned logistic head, and archives the
full run for review. The live orchestration is centered on
`marquee/pipeline/runner.py`.

## Implemented Scope

Implemented:

- Movie poster candidate fetch from TMDB
- Resolution, style, OCR, and optional fan-junk gates
- Exact duplicate removal and same-design stacking / near-duplicate handling
- CLIP, DINOv2, face/person, visual-composition, title-geometry, and quality
  features
- Weighted scoring and learned-head scoring with `SCORER=auto`
- Run archival, review payloads, pure rescoring, feedback capture, and batch
  pipeline jobs

Not implemented:

- TV poster selection
- Additional live poster sources in the active pipeline
- A VLM or judge stage
- A fully productized ranking-v2 system beyond the experiments preserved in
  `design/plans/30-ranking-v2-concept-notes.md`

## Stage Flow

The implemented flow is split across `marquee/pipeline/runner.py`,
`marquee/pipeline/deduper.py`, `marquee/pipeline/gate.py`,
`marquee/pipeline/features.py`, `marquee/pipeline/ocr_filter.py`, and
`marquee/pipeline/scorer.py`.

1. Fetch and download candidates from TMDB into a per-run work directory.
2. Remove exact duplicates with SHA-256 before any inference work.
3. Apply the resolution gate from metadata (`GATE_MIN_WIDTH`) before CLIP.
4. Run the style feature batch: CLIP embeddings, k-NN taste similarity,
   aesthetic score, zero-shot axes, metadata scalars, and official-family
   similarity to the movie’s TMDB primary poster when available.
5. Apply style gating so low-aesthetic or off-style posters can be removed
   before OCR.
6. Run the OCR text gate with `PosterTextFilter` in
   `marquee/pipeline/ocr_filter.py`.
7. Group same-design variants with the stack layer or fall back to pHash-based
   near-duplicate filtering.
8. Run detail features for survivors only: DINO k-NN, face and person geometry,
   CV palette and composition signals, quality artifacts, title geometry,
   sharpness, and text residual metrics.
9. Apply any final detail gate such as the optional fan-junk gate.
10. Rank survivors with the resolved scorer and place inspectable output files.

## Gate Semantics

Gates are absolute accept-or-reject rules shared across movies. Ranking is
relative and only compares the surviving candidates for one movie.

Important gate behavior:

- Resolution is enforced early with `GATE_MIN_WIDTH`.
- Aesthetic can be rescued by strong taste similarity through
  `GATE_AESTHETIC_RESCUE_KNN` and `GATE_MIN_AESTHETIC_RESCUED`.
- OCR is strict by default: `OCR_REQUIRE_TITLE=true`,
  `OCR_TEXT_MODE=title_only`, and `OCR_MAX_RESIDUAL_BOXES=0`.
- `OCR_ACCEPT_NO_TEXT=false` means textless posters are not normally rescued
  unless an explicit batch fallback path is enabled.
- `GATE_FAN_JUNK_ENABLED=false` keeps the final fan-junk combo gate off by
  default.

## Feature Extraction

`marquee/pipeline/features.py` splits extraction into a style phase and a
detail phase.

Style phase:

- CLIP embedding via `marquee/ml/embedding.py`
- Taste similarity from `marquee/ml/taste_store.py`
- Aesthetic score from `marquee/ml/aesthetic.py`
- Metadata-derived signals such as resolution, provenance, and language match
- Optional CLIP zero-shot axes from `marquee/ml/zeroshot.py`

Detail phase:

- DINOv2 style second opinion from `marquee/ml/dino.py`
- Face geometry from `marquee/ml/face.py`
- Person detection from `marquee/ml/person.py`
- Palette, composition, and artifact metrics from
  `marquee/ml/visual_features.py`
- Title geometry and residual-text metrics using OCR output
- Exemplar-calibrated taste typicality through `marquee/ml/calibration.py`

The pipeline records both raw and normalized values in the archived run data so
review screens and rescoring can explain ranking decisions after the fact.

## Taste Profile And Learned Head

Taste matching is exemplar-based, not centroid-based. The taste store in
`marquee/ml/taste_store.py` uses k-NN over positive exemplars, supports
negative exemplars, and can aggregate neighbors with
`KNN_WEIGHTING=softmax` and `KNN_SOFTMAX_TEMP=0.1`.

The learned scorer is a logistic head implemented in
`marquee/ml/learned_head.py` and trained by
`marquee/ml/head_trainer.py`. `select_scorer()` in
`marquee/pipeline/scorer.py` resolves:

- `SCORER=weighted` to always use hand weights
- `SCORER=learned` to require a valid learned artifact
- `SCORER=auto` to prefer the learned head when a compatible artifact exists

The current default keeps automatic retraining off:
`HEAD_AUTO_RETRAIN=false`. Feedback accumulates labels and exemplars, and the
actual head retraining is queued through `/api/taste/head/retrain`.

## OCR

`marquee/pipeline/ocr_filter.py` uses a multi-process PaddleOCR pool and a
three-pass strategy with optional detail passes and contrast-enhanced retries.
The OCR stage identifies title evidence, classifies residual text, and records
diagnostic geometry for later review.

Key knobs:

- `OCR_DEVICE=auto`
- `OCR_WORKERS=8`
- `OCR_DETAIL_PASSES=true`
- `OCR_MAX_RESIDUAL_BOXES=0`
- `OCR_MAX_RESIDUAL_AREA_FRACTION=0.04`
- `OCR_REQUIRE_TITLE=true`
- `OCR_ENHANCE_RETRY=true`
- `OCR_TITLE_RECOVERY_ENABLED=true`

## Scoring, Output, And Run Management

`WeightedScorer` renormalizes over the features actually present on each
candidate, so optional features can drop out without breaking the score range.
`LearnedScorer` returns the head’s estimated pick probability.

`marquee/pipeline/output.py` copies ranked survivors into the run directory,
names them with rank and score metadata, and re-downloads top-ranked designs at
TMDB original resolution. `marquee/pipeline/run_manager.py` and the durable job
system keep run archives available for:

- `GET /api/pipeline/runs/{run_id}`
- `POST /api/pipeline/runs/{run_id}/rescore`
- `GET /api/pipeline/review-queue`
- `GET /api/pipeline/metrics`

Batch runs use `poster_pipeline_batch` in
`marquee/core/jobs/builtin_handlers.py`, which lets OCR and DINO-heavy work run
once across many movies instead of once per movie.

## Feedback Loop

`marquee/api/routes/feedback.py` records approve, override, reject, and undo
actions. Feedback is stored in `FEEDBACK_LABELS_PATH`, can add positive
exemplars to `TRAINING_DATA_DIR`, and can optionally promote strong mistakes
into the negative-exemplar set under `NEGATIVE_DATA_DIR`.

The feedback system feeds both ranking and operations:

- Deploys or restores posters through `marquee/core/poster_service.py`
- Marks a run as reviewed in the `PipelineRun` record
- Supplies labels for future learned-head training

See `library.md` for deployment behavior and `job-platform.md` for the queued
training path.

## Important Configuration

Representative pipeline knobs in `marquee/core/pipeline_config.py`:

- Model/runtime: `AI_MODEL`, `EXECUTION_PROVIDER`, `CLIP_BATCH_SIZE`,
  `DINO_ENABLED`
- Taste: `K_NEIGHBORS`, `KNN_WEIGHTING`, `TASTE_NEG_WEIGHT`,
  `CALIBRATION_ENABLED`
- OCR: `OCR_TEXT_MODE`, `OCR_MAX_RESIDUAL_BOXES`,
  `OCR_ACCEPT_NO_TEXT`, `OCR_TITLE_RECOVERY_ENABLED`
- Gating: `GATE_MIN_WIDTH`, `GATE_MIN_AESTHETIC`, `GATE_MIN_KNN_SIM`,
  `GATE_FAN_JUNK_ENABLED`
- Scoring: `SCORER`, `WEIGHT_*`, `STACK_ENABLED`, `STACK_SIGNAL`
- Feedback/training: `HEAD_MIN_LABELS`, `HEAD_MIN_MOVIES`,
  `HEAD_MIN_PAIRS`, `HEAD_AUTO_RETRAIN`
- Operations: `TMDB_POSTER_SIZE`, `PIPELINE_BATCH_MAX_MOVIES`

## Cross References

- `overview.md` for the application-level architecture
- `library.md` for deployment, restore, and sync interactions
- `job-platform.md` for how poster pipeline runs and retraining are queued
- `timeline.md` for remaining tuning and deferred poster-pipeline work
