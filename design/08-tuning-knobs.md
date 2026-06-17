# Marquee — Pipeline Tuning Knobs

**Status:** Reconciled with the codebase on 2026-06-16.

All poster-pipeline knobs live in `marquee/core/pipeline_config.py`. Most can
be set in `.env`; many non-restart knobs can also be updated at runtime through
`GET/PUT /api/config/pipeline`, which persists overrides to
`data/pipeline_overrides.json`.

## Taste Retrieval

| Knob | Default | Purpose |
|---|---:|---|
| `K_NEIGHBORS` | `10` | Number of positive/negative nearest exemplars |
| `KNN_WEIGHTING` | `softmax` | `softmax` or `mean` aggregation |
| `KNN_SOFTMAX_TEMP` | `0.1` | Lower values make nearest exemplars dominate |
| `TASTE_NEG_WEIGHT` | `1.0` | Penalty when closer to negative than positive exemplars |
| `PREFERRED_LANG` | `en` | Preferred TMDB poster language |

## Gates

| Knob | Default | Purpose |
|---|---:|---|
| `GATE_MIN_WIDTH` | `500` | Metadata width floor |
| `GATE_MIN_AESTHETIC` | `4.5` | Aesthetic floor |
| `GATE_MIN_KNN_SIM` | `0.45` | Style floor |
| `GATE_AESTHETIC_RESCUE_KNN` | `0.55` | Relax aesthetic floor for on-style candidates |
| `GATE_MIN_AESTHETIC_RESCUED` | `2.0` | Rescued aesthetic floor |
| `GATE_FAN_JUNK_ENABLED` | `False` | Optional combo gate |
| `GATE_FAN_JUNK_MAX_AESTHETIC` | `5.0` | Fan-junk aesthetic bound |
| `GATE_FAN_JUNK_MAX_PROVENANCE` | `0.55` | Fan-junk provenance bound |
| `GATE_FAN_JUNK_MAX_RESOLUTION_MP` | `1.0` | Fan-junk resolution bound |

## Weights

Weights are positive. Features are normalized so higher is better.

| Feature knob | Default |
|---|---:|
| `WEIGHT_KNN_SIM` | `0.30` |
| `WEIGHT_AESTHETIC` | `0.12` |
| `WEIGHT_TITLE_COLORFULNESS` | `0.15` |
| `WEIGHT_FACE_AREA` | `0.15` |
| `WEIGHT_TEXT_RESIDUAL` | `0.10` |
| `WEIGHT_PROVENANCE` | `0.02` |
| `WEIGHT_SHARPNESS` | `0.03` |
| `WEIGHT_RESOLUTION` | `0.0` |
| `WEIGHT_LANG_MATCH` | `0.0` |
| `WEIGHT_DINO_KNN` | `0.12` |
| `WEIGHT_TASTE_TYPICALITY` | `0.12` |
| `WEIGHT_QUALITY_ARTIFACTS` | `0.03` |
| `WEIGHT_OFFICIAL_FAMILY` | `0.12` |

The scorer renormalizes by available active weights, so optional missing
features do not drag scores down.

## Normalization

| Knob | Default | Feature |
|---|---:|---|
| `NORM_KNN_MIN` / `NORM_KNN_MAX` | `0.4` / `0.9` | `knn_sim` |
| `NORM_AESTHETIC_MAX` | `10.0` | `aesthetic` |
| `NORM_TITLE_COLORFULNESS_MAX` | `60.0` | `title_colorfulness` |
| `NORM_TITLE_COLORFULNESS_NEUTRAL` | `0.5` | no-title fallback |
| `NORM_RESOLUTION_MAX_MP` | `6.0` | `resolution` |
| `NORM_SHARPNESS_MAX` | `2000.0` | `sharpness` |
| `NORM_OFFICIAL_MIN` / `NORM_OFFICIAL_MAX` | `0.60` / `0.95` | `official_family` |

## Optional Features

| Knob | Default | Purpose |
|---|---:|---|
| `DINO_ENABLED` | `auto` | `auto`, `on`, or `off` |
| `EXTRA_QUALITY_ENABLED` | `True` | Artifact metrics and person detector |
| `PERSON_CONFIDENCE_THRESHOLD` | `0.40` | YOLO person threshold |
| `CALIBRATION_ENABLED` | `True` | KDE taste typicality |
| `CALIBRATION_BANDWIDTH_SCALE` | `1.0` | KDE bandwidth scale |
| `CALIBRATION_MIN_SAMPLES` | `20` | Minimum samples for a calibrated band |
| `SCORER` | `auto` | `auto`, `weighted`, or `learned` |

## OCR

| Knob | Default | Purpose |
|---|---:|---|
| `OCR_DEVICE` | `auto` | `auto`, `cpu`, or `gpu` |
| `OCR_WORKERS` | `0` | Auto-sized when zero |
| `OCR_DETAIL_PASSES` | `True` | Extra strip/upscale passes |
| `OCR_MAX_RESIDUAL_BOXES` | `0` | Strict title-only by default |
| `OCR_MAX_RESIDUAL_AREA_FRACTION` | `0.04` | Residual text area guard |
| `OCR_CONFIDENCE_THRESHOLD` | `0.75` | Full-image threshold |
| `OCR_STRIP_CONFIDENCE_THRESHOLD` | `0.65` | Strip-pass threshold |
| `OCR_BOTTOM_CONFIDENCE_THRESHOLD` | `0.50` | Bottom-strip threshold |
| `OCR_FUZZY_CUTOFF` | `0.60` | Title fuzzy-match cutoff |
| `OCR_REQUIRE_TITLE` | `True` | Reject if no title match |
| `OCR_ACCEPT_NO_TEXT` | `True` | Batch-level fallback only |
| `OCR_ENHANCE_RETRY` | `True` | Contrast-enhanced retry |

## Dedup

| Knob | Default | Purpose |
|---|---:|---|
| `DEDUP_PHASH_THRESHOLD` | `6` | pHash Hamming threshold |
| `DEDUP_MIN_POSTER_WIDTH` | `500` | Dedup width floor |

## Feedback and Learned Head

| Knob | Default | Purpose |
|---|---:|---|
| `FEEDBACK_LABELS_PATH` | `data/feedback/labels.jsonl` | Label store |
| `NEGATIVE_DATA_DIR` | `data/training/negative` | Negative exemplars |
| `TRAINING_DATA_DIR` | `data/training/positive` | Positive exemplars |
| `FEEDBACK_GATE_ALERT_THRESHOLD` | `5` | Gate alert threshold |
| `FEEDBACK_NEGATIVES_FROM_OVERRIDES` | `False` | Copy overridden auto-picks to negatives |
| `FEEDBACK_DEPLOY_DEFAULT` | `True` | Deploy approve/override by default |
| `HEAD_MIN_LABELS` | `150` | Learned-head minimum labels |
| `HEAD_MIN_MOVIES` | `5` | Learned-head minimum movies |
| `HEAD_AUTO_RETRAIN` | `True` | Retrain after feedback when eligible |

## Evaluation Commands

Implemented helper:

```bash
.venv/bin/python -m marquee.ml.knn_eval
.venv/bin/python -m marquee.ml.knn_eval --dino
```

Use archived run JSON plus `POST /api/pipeline/runs/{run_id}/rescore` for
interactive weight/gate experiments without re-running inference.

## Live Count Rule

Do not publish hardcoded current label counts in this doc. Query
`GET /api/taste/status` for live counts when tuning, reporting, or deciding
whether a calibrated/taste-weighted profile is ready.
