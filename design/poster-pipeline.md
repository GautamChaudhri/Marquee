# Poster Pipeline

## Overview

The poster pipeline selects the best poster for a movie by applying a strict
gate-then-rank workflow. It fetches candidate posters, removes obviously bad or
invalid options, computes style and detail features, ranks the survivors with
the deployed weighted baseline plus an optional bounded residual correction,
and archives the full run for review. The live orchestration is centered on
`marquee/pipeline/runner.py`.

## Implemented Scope

Implemented:

- Movie poster candidate fetch from TMDB
- Resolution, style, OCR, and optional fan-junk gates
- Exact duplicate removal and same-design stacking / near-duplicate handling
- CLIP, DINOv2, face/person, visual-composition, title-geometry, and quality
  features
- Weighted scoring and a compatible bounded residual correction
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

## Taste Profile And Residual Ranking

Taste matching is exemplar-based, not centroid-based. The deployed movie and TV
profiles include versioned positive and negative evidence; their compatible
publication is resolved through the canonical publication authority before
scoring. The weighted scorer is the permanent baseline. A residual may alter
that baseline only when its frozen evidence, compatibility, held-out evaluation,
and bounded delta are valid. A consumer must load the publication before
readiness can claim personalization.

Historical learned-head modules and plans remain historical evidence. They are
not a current runtime, onboarding, publication, or readiness authority.

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
Any compatible residual is a bounded correction to that baseline, never a
replacement scorer.

`marquee/pipeline/output.py` copies ranked survivors into the attempt workspace,
names them with rank and score metadata, and re-downloads top-ranked designs at
TMDB original resolution. Canonical `JobArtifact` ownership and `PipelineRun`
projections keep immutable run archives available for:

- `GET /api/pipeline/runs/{run_id}`
- `POST /api/pipeline/runs/{run_id}/rescore`
- `GET /api/pipeline/review-queue`
- `GET /api/pipeline/metrics`

Batch runs use canonical parent/child definitions and the fixed internal runner
boundary, which lets OCR and DINO-heavy work share a bounded batch process while
preserving child identity and fencing.

## Feedback Loop

`marquee/api/routes/feedback.py` records immutable approve, override, reject,
and undo events linked to canonical runs and candidate artifacts. Successor ML
jobs consume frozen feedback features; request handlers do not mutate live
training folders.

The feedback system feeds both ranking and operations:

- Submits deploy or reset through canonical poster mutation jobs
- Marks a run as reviewed in the `PipelineRun` record
- Supplies immutable evidence for profile and residual work

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
- Feedback/training: versioned profile evidence and bounded residual
  evaluation/publication controls
- Operations: `TMDB_POSTER_SIZE`, `PIPELINE_BATCH_MAX_MOVIES`

## Cross References

- `overview.md` for the application-level architecture
- `library.md` for deployment, restore, and sync interactions
- `job-platform.md` for how poster pipeline runs and retraining are queued
- `timeline.md` for remaining tuning and deferred poster-pipeline work
- `job-system-update/jmc6i-runner-progress-and-certification-closure.md` for fixed-runner cancellation
  and progress closure
- `job-system-update/jmc6j-taste-onboarding-and-residual-learning.md` for the approved cold-start,
  continuous taste, and residual-ranking target
- `job-system-update/jmc7-readiness-recovery-timeline.md` for the current JMC7 certification state

## JMC7 certification and operator gates

JMC7C certifies the deterministic local pipeline boundary through canonical
jobs, PgQueuer delivery, durable evidence, consumers, SSE, and the built web
application. It does not claim live operator-library, GPU, model-download, or
destructive media-operation acceptance. Those remain separate operator gates:
run representative movie and TV libraries with explicitly available models and
credentials, validate restore paths on disposable copies, and activate no
schedules until the corresponding live check has passed.
