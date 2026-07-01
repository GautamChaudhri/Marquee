# Marquee — Extended Features and Calibration

**Status:** Reconciled with the codebase on 2026-06-16.

This document covers optional rank-stage features implemented around
`marquee/pipeline/features.py` and `marquee/ml/`.

## Calibration

Implemented in `marquee/ml/calibration.py`.

The taste profile can store calibration arrays:

- `calib_feature_names`
- `calib_feature_values`

At runtime, `TasteCalibration` builds one-dimensional KDE bands for features
with at least `CALIBRATION_MIN_SAMPLES` usable exemplar values. Candidate
values are converted to 0..1 typicality scores and averaged into
`taste_typicality`.

Knobs:

- `CALIBRATION_ENABLED`
- `CALIBRATION_BANDWIDTH_SCALE`
- `CALIBRATION_MIN_SAMPLES`
- `TYPICALITY_FEATURES`

Missing calibration arrays disable `taste_typicality` with a warning; they do
not abort a run.

## Extended Feature Sources

| Feature/source | Implementation | Notes |
|---|---|---|
| Zero-shot CLIP axes | `marquee/ml/zeroshot.py` | Optional artifact at `ZEROSHOT_AXES_PATH` |
| Palette/composition | `marquee/ml/visual_features.py` | Darkness, saturation, entropy, contrast, negative space, edges, symmetry |
| Title geometry | `visual_features.title_geometry()` | Derived from OCR title box |
| Face geometry | `visual_features.face_geometry()` and `marquee/ml/face.py` | SCRFD face detector |
| DINOv2 KNN | `marquee/ml/dino.py` | Optional second style opinion |
| Quality artifacts | `visual_features.quality_artifact_features()` | Blockiness and noise |
| Person features | `marquee/ml/person.py` | Optional YOLO person detector |
| Official family | `FeatureExtractor.extract_style_batch()` | CLIP cosine to TMDB primary poster |

## Optional Feature Activation

Optional features are active only when the relevant config, artifacts, and
hardware support are present.

- `DINO_ENABLED=auto` enables DINO on GPU tiers (`cuda`, `openvino-gpu`,
  `coreml`) and disables it on CPU tiers.
- `EXTRA_QUALITY_ENABLED=false` disables artifact metrics and person features.
- Person features require `PERSON_MODEL_PATH` to exist.
- Zero-shot axes require `ZEROSHOT_AXES_PATH`.
- Official-family scoring requires the TMDB primary poster to be present and
  embedded in the same run.

Optional features produce `None` when unavailable. The scorer redistributes
weights around missing normalized values.

## Current Scorer Weights

The current defaults live in `PipelineSettings`:

| Feature | Default weight |
|---|---:|
| `dino_knn` | `0.12` |
| `taste_typicality` | `0.12` |
| `quality_artifacts` | `0.03` |
| `official_family` | `0.12` |

See `design/08-tuning-knobs.md` for the complete weight list.

## Profile Build

`marquee/ml/taste_trainer.py` builds the taste profile from the configured
training folders and can include CLIP embeddings, negative exemplars, DINO
embeddings, self-KNN diagnostics, and calibration arrays.

`marquee/ml/profile_updater.py` incrementally appends approved posters between
full rebuilds.

## Logging and Serialization

Per run, the pipeline logs hardware/provider choices, optional feature status,
calibration bands, raw extended values, typicality details, and rank
contributions.

`pipeline_run.json` stores:

- `raw_features`
- `normalized_features`
- `extended_features`
- `typicality_detail`

## Planned

- `[PLANNED]` Authority anchors beyond TMDB primary poster, such as Wikipedia
  infobox art or Fanart.tv likes.
- `[PLANNED]` Pairwise/LightGBM learned ranker.

## Follow-Up Benchmark Note

Do not publish fixed performance guarantees for DINO/person/CV features from
this design doc. Re-measure on the current target hardware before quoting
millisecond/poster or posters/minute numbers.
