"""Settings-page metadata: knob groups (ordered by pipeline stage) and
per-knob control hints (kind, range, options, step).

All groups and knobs reference fields defined in ``PipelineSettings``
(``marquee/core/pipeline_config.py``).  The settings page builds its UI
from the groups/meta returned by ``GET /api/config/pipeline``.
"""

from __future__ import annotations

from typing import Any

# ── Ordered groups (pipeline stage order) ───────────────────────────────
# Each group has an ``id`` (slug), ``label`` (human), and ``knobs`` (list of
# PipelineSettings field names in display order).
KNOB_GROUPS: list[dict[str, Any]] = [
    {
        "id": "weights",
        "label": "Weights",
        "description": "Phase-0 scorer feature weights (renormalized at runtime).",
        "knobs": [
            "WEIGHT_KNN_SIM",
            "WEIGHT_AESTHETIC",
            "WEIGHT_TITLE_COLORFULNESS",
            "WEIGHT_FACE_AREA",
            "WEIGHT_TEXT_RESIDUAL",
            "WEIGHT_PROVENANCE",
            "WEIGHT_SHARPNESS",
            "WEIGHT_RESOLUTION",
            "WEIGHT_LANG_MATCH",
            "WEIGHT_DINO_KNN",
            "WEIGHT_TASTE_TYPICALITY",
            "WEIGHT_QUALITY_ARTIFACTS",
            "WEIGHT_OFFICIAL_FAMILY",
        ],
    },
    {
        "id": "gates",
        "label": "Gates",
        "description": "Hard thresholds that reject candidates before ranking.",
        "knobs": [
            "GATE_MIN_WIDTH",
            "GATE_MIN_AESTHETIC",
            "GATE_MIN_KNN_SIM",
            "GATE_AESTHETIC_RESCUE_KNN",
            "GATE_MIN_AESTHETIC_RESCUED",
            "GATE_FAN_JUNK_ENABLED",
            "GATE_FAN_JUNK_MAX_AESTHETIC",
            "GATE_FAN_JUNK_MAX_PROVENANCE",
            "GATE_FAN_JUNK_MAX_RESOLUTION_MP",
        ],
    },
    # The "text_gate" group was removed from the settings UI: text-gate
    # behaviour is managed through the Text Profiles system on the pipeline
    # landing page (design/plans/03). The OCR_* knobs below stay in KNOB_META
    # so the config API still serves and validates them.
    {
        "id": "style",
        "label": "Style / taste",
        "description": "KNN taste profile, weighting, and language preferences.",
        "knobs": [
            "K_NEIGHBORS",
            "KNN_WEIGHTING",
            "KNN_SOFTMAX_TEMP",
            "TASTE_NEG_WEIGHT",
            "TV_TASTE_MIN_POSTERS",
            "PREFERRED_LANG",
        ],
    },
    {
        "id": "detail",
        "label": "Detail",
        "description": "DINOv2, face/person detection, and quality-artifact thresholds.",
        "knobs": [
            "DINO_ENABLED",
            "EXTRA_QUALITY_ENABLED",
            "PERSON_CONFIDENCE_THRESHOLD",
            "FACE_CONFIDENCE_THRESHOLD",
            "FACE_NMS_THRESHOLD",
            "QUALITY_BLOCKINESS_SAT",
            "QUALITY_NOISE_SAT",
        ],
    },
    {
        "id": "calibration",
        "label": "Calibration",
        "description": "Exemplar-calibrated KDE normalization.",
        "knobs": [
            "CALIBRATION_ENABLED",
            "CALIBRATION_BANDWIDTH_SCALE",
            "CALIBRATION_MIN_SAMPLES",
        ],
    },
    {
        "id": "residual",
        "label": "Residual feedback",
        "description": "Bounded residual eligibility and feedback-loop controls.",
        "knobs": [
            "SCORER",
            "RESIDUAL_MIN_SUBJECTS",
            "RESIDUAL_MIN_PAIRS",
            "FEEDBACK_GATE_ALERT_THRESHOLD",
            "FEEDBACK_DEPLOY_DEFAULT",
        ],
    },
    {
        "id": "dedup",
        "label": "Dedup",
        "description": "Perceptual-hash deduplication thresholds.",
        "knobs": [
            "DEDUP_PHASH_THRESHOLD",
            "DEDUP_MIN_POSTER_WIDTH",
        ],
    },
    {
        "id": "stacks",
        "label": "Stacks",
        "description": (
            "Group same-design poster variants into stacks instead of deleting "
            "near-duplicates; rank designs, then variants within each design."
        ),
        "knobs": [
            "STACK_ENABLED",
            "STACK_SIGNAL",
            "STACK_SIM_THRESHOLD",
            "STACK_PHASH_MAX_DISTANCE",
            "STACK_AGG_TOPK",
        ],
    },
    {
        "id": "downloads",
        "label": "Downloads",
        "description": "TMDB poster fetch and batch sizing.",
        "knobs": [
            "TMDB_POSTER_SIZE",
            "PIPELINE_BATCH_MAX_MOVIES",
            "POSTER_GROUP_ENABLED",
            "POSTER_GROUP_CHUNK_SIZE",
        ],
    },
    {
        "id": "advanced",
        "label": "Advanced",
        "description": "Normalization ranges, residual blend, and provenance tuning.",
        "knobs": [
            "NORM_KNN_MIN",
            "NORM_KNN_MAX",
            "NORM_AESTHETIC_MAX",
            "NORM_TITLE_COLORFULNESS_MAX",
            "NORM_TITLE_COLORFULNESS_NEUTRAL",
            "NORM_RESOLUTION_MAX_MP",
            "NORM_SHARPNESS_MAX",
            "NORM_OFFICIAL_MIN",
            "NORM_OFFICIAL_MAX",
            "RESIDUAL_COUNT_SAT",
            "RESIDUAL_WEIGHT_COUNT",
            "RESIDUAL_WEIGHT_AREA",
            "PROV_PRIOR_MEAN",
            "PROV_CONFIDENCE",
            "CLIP_BATCH_SIZE",
        ],
    },
]


# ── Per-knob control metadata ───────────────────────────────────────────
# ``kind`` maps to a UI control:
#   weight  → 0..1 slider, step 0.01
#   float   → number input with min/max/step
#   int     → number input with min/max/step
#   bool    → toggle switch
#   enum    → dropdown select from ``options``
#   str     → text input
KNOB_META: dict[str, dict[str, Any]] = {
    # ── Weights ──────────────────────────────────────────────────────
    "WEIGHT_KNN_SIM": {"kind": "weight", "min": 0, "max": 1, "step": 0.01},
    "WEIGHT_AESTHETIC": {"kind": "weight", "min": 0, "max": 1, "step": 0.01},
    "WEIGHT_TITLE_COLORFULNESS": {"kind": "weight", "min": 0, "max": 1, "step": 0.01},
    "WEIGHT_FACE_AREA": {"kind": "weight", "min": 0, "max": 1, "step": 0.01},
    "WEIGHT_TEXT_RESIDUAL": {"kind": "weight", "min": 0, "max": 1, "step": 0.01},
    "WEIGHT_PROVENANCE": {"kind": "weight", "min": 0, "max": 1, "step": 0.01},
    "WEIGHT_SHARPNESS": {"kind": "weight", "min": 0, "max": 1, "step": 0.01},
    "WEIGHT_RESOLUTION": {"kind": "weight", "min": 0, "max": 1, "step": 0.01},
    "WEIGHT_LANG_MATCH": {"kind": "weight", "min": 0, "max": 1, "step": 0.01},
    "WEIGHT_DINO_KNN": {"kind": "weight", "min": 0, "max": 1, "step": 0.01},
    "WEIGHT_TASTE_TYPICALITY": {"kind": "weight", "min": 0, "max": 1, "step": 0.01},
    "WEIGHT_QUALITY_ARTIFACTS": {"kind": "weight", "min": 0, "max": 1, "step": 0.01},
    "WEIGHT_OFFICIAL_FAMILY": {"kind": "weight", "min": 0, "max": 1, "step": 0.01},
    # ── Gates ────────────────────────────────────────────────────────
    "GATE_MIN_WIDTH": {"kind": "int", "min": 0, "max": 4000, "step": 10},
    "GATE_MIN_AESTHETIC": {"kind": "float", "min": 0, "max": 10, "step": 0.1},
    "GATE_MIN_KNN_SIM": {"kind": "float", "min": 0, "max": 1, "step": 0.01},
    "GATE_AESTHETIC_RESCUE_KNN": {"kind": "float", "min": 0, "max": 1, "step": 0.01},
    "GATE_MIN_AESTHETIC_RESCUED": {"kind": "float", "min": 0, "max": 10, "step": 0.1},
    "GATE_FAN_JUNK_ENABLED": {"kind": "bool"},
    "GATE_FAN_JUNK_MAX_AESTHETIC": {"kind": "float", "min": 0, "max": 10, "step": 0.1},
    "GATE_FAN_JUNK_MAX_PROVENANCE": {"kind": "float", "min": 0, "max": 10, "step": 0.01},
    "GATE_FAN_JUNK_MAX_RESOLUTION_MP": {"kind": "float", "min": 0, "max": 20, "step": 0.1},
    # ── Text gate / OCR ──────────────────────────────────────────────
    "OCR_TEXT_MODE": {"kind": "enum", "options": ["title_only", "textless", "custom"]},
    "OCR_ALLOW_TITLE": {"kind": "bool"},
    "OCR_ALLOW_DIRECTOR": {"kind": "bool"},
    "OCR_ALLOW_STUDIO": {"kind": "bool"},
    "OCR_ALLOW_RATING": {"kind": "bool"},
    "OCR_ALLOW_TAGLINE": {"kind": "bool"},
    "OCR_ALLOW_BILLING": {"kind": "bool"},
    "OCR_DEVICE": {"kind": "enum", "options": ["auto", "cpu", "gpu"]},
    "OCR_WORKERS": {"kind": "int", "min": 0, "max": 16, "step": 1},
    "OCR_DETAIL_PASSES": {"kind": "bool"},
    "OCR_MAX_RESIDUAL_BOXES": {"kind": "int", "min": 0, "max": 20, "step": 1},
    "OCR_MAX_RESIDUAL_AREA_FRACTION": {"kind": "float", "min": 0, "max": 1, "step": 0.01},
    "OCR_CONFIDENCE_THRESHOLD": {"kind": "float", "min": 0, "max": 1, "step": 0.05},
    "OCR_STRIP_CONFIDENCE_THRESHOLD": {"kind": "float", "min": 0, "max": 1, "step": 0.05},
    "OCR_BOTTOM_CONFIDENCE_THRESHOLD": {"kind": "float", "min": 0, "max": 1, "step": 0.05},
    "OCR_FUZZY_CUTOFF": {"kind": "float", "min": 0, "max": 1, "step": 0.05},
    "OCR_TITLE_PROXIMITY_PIXELS": {"kind": "float", "min": 0, "max": 200, "step": 5},
    "OCR_RESIDUAL_SIGNIFICANT_AREA_FRACTION": {
        "kind": "float",
        "min": 0,
        "max": 0.1,
        "step": 0.001,
    },
    "OCR_RESIDUAL_SIGNIFICANT_WIDTH_FRACTION": {"kind": "float", "min": 0, "max": 1, "step": 0.05},
    "OCR_REQUIRE_TITLE": {"kind": "bool"},
    "OCR_ACCEPT_NO_TEXT": {"kind": "bool"},
    "OCR_ENHANCE_RETRY": {"kind": "bool"},
    "OCR_TITLE_RECOVERY_ENABLED": {"kind": "bool"},
    "OCR_TITLE_RECOVERY_CONFIDENCE_THRESHOLD": {
        "kind": "float",
        "min": 0,
        "max": 1,
        "step": 0.05,
    },
    # ── Style / taste ────────────────────────────────────────────────
    "K_NEIGHBORS": {"kind": "int", "min": 1, "max": 100, "step": 1},
    "KNN_WEIGHTING": {"kind": "enum", "options": ["mean", "softmax"]},
    "KNN_SOFTMAX_TEMP": {"kind": "float", "min": 0.01, "max": 5, "step": 0.01},
    "TASTE_NEG_WEIGHT": {"kind": "float", "min": 0, "max": 10, "step": 0.1},
    "TV_TASTE_MIN_POSTERS": {"kind": "int", "min": 1, "max": 10000, "step": 1},
    "PREFERRED_LANG": {
        "kind": "enum",
        "options": ["en", "fr", "de", "es", "it", "ja", "ko", "zh", "pt", "ru"],
    },
    # ── Detail ───────────────────────────────────────────────────────
    "DINO_ENABLED": {"kind": "enum", "options": ["auto", "on", "off"]},
    "EXTRA_QUALITY_ENABLED": {"kind": "bool"},
    "PERSON_CONFIDENCE_THRESHOLD": {"kind": "float", "min": 0, "max": 1, "step": 0.05},
    "FACE_CONFIDENCE_THRESHOLD": {"kind": "float", "min": 0, "max": 1, "step": 0.05},
    "FACE_NMS_THRESHOLD": {"kind": "float", "min": 0, "max": 1, "step": 0.05},
    "QUALITY_BLOCKINESS_SAT": {"kind": "float", "min": 0, "max": 50, "step": 0.5},
    "QUALITY_NOISE_SAT": {"kind": "float", "min": 0, "max": 50, "step": 0.5},
    # ── Calibration ──────────────────────────────────────────────────
    "CALIBRATION_ENABLED": {"kind": "bool"},
    "CALIBRATION_BANDWIDTH_SCALE": {"kind": "float", "min": 0.1, "max": 5, "step": 0.1},
    "CALIBRATION_MIN_SAMPLES": {"kind": "int", "min": 2, "max": 500, "step": 1},
    # ── Residual / feedback ──────────────────────────────────────────
    "SCORER": {"kind": "enum", "options": ["auto", "weighted", "residual"]},
    "RESIDUAL_MIN_SUBJECTS": {"kind": "int", "min": 1, "max": 1000, "step": 1},
    "RESIDUAL_MIN_PAIRS": {"kind": "int", "min": 1, "max": 10000, "step": 10},
    "FEEDBACK_GATE_ALERT_THRESHOLD": {"kind": "int", "min": 1, "max": 100, "step": 1},
    "FEEDBACK_DEPLOY_DEFAULT": {"kind": "bool"},
    # ── Dedup ────────────────────────────────────────────────────────
    "DEDUP_PHASH_THRESHOLD": {"kind": "int", "min": 0, "max": 64, "step": 1},
    "DEDUP_MIN_POSTER_WIDTH": {"kind": "int", "min": 0, "max": 4000, "step": 10},
    # ── Stacks ───────────────────────────────────────────────────────
    "STACK_ENABLED": {"kind": "bool"},
    "STACK_SIGNAL": {"kind": "enum", "options": ["dino", "clip", "phash"]},
    "STACK_SIM_THRESHOLD": {"kind": "float", "min": 0, "max": 1, "step": 0.01},
    "STACK_PHASH_MAX_DISTANCE": {"kind": "int", "min": 0, "max": 64, "step": 1},
    "STACK_AGG_TOPK": {"kind": "int", "min": 1, "max": 20, "step": 1},
    # ── Downloads ────────────────────────────────────────────────────
    "TMDB_POSTER_SIZE": {
        "kind": "enum",
        "options": ["w92", "w154", "w185", "w342", "w500", "w780", "original"],
    },
    "PIPELINE_BATCH_MAX_MOVIES": {"kind": "int", "min": 1, "max": 5000, "step": 10},
    "POSTER_GROUP_ENABLED": {"kind": "bool"},
    "POSTER_GROUP_CHUNK_SIZE": {"kind": "int", "min": 1, "max": 16, "step": 1},
    # ── Advanced ─────────────────────────────────────────────────────
    "NORM_KNN_MIN": {"kind": "float", "min": 0, "max": 1, "step": 0.01},
    "NORM_KNN_MAX": {"kind": "float", "min": 0, "max": 1, "step": 0.01},
    "NORM_AESTHETIC_MAX": {"kind": "float", "min": 1, "max": 20, "step": 0.1},
    "NORM_TITLE_COLORFULNESS_MAX": {"kind": "float", "min": 1, "max": 200, "step": 1},
    "NORM_TITLE_COLORFULNESS_NEUTRAL": {"kind": "float", "min": 0, "max": 1, "step": 0.05},
    "NORM_RESOLUTION_MAX_MP": {"kind": "float", "min": 0.1, "max": 50, "step": 0.1},
    "NORM_SHARPNESS_MAX": {"kind": "float", "min": 100, "max": 10000, "step": 100},
    "NORM_OFFICIAL_MIN": {"kind": "float", "min": 0, "max": 1, "step": 0.01},
    "NORM_OFFICIAL_MAX": {"kind": "float", "min": 0, "max": 1, "step": 0.01},
    "RESIDUAL_COUNT_SAT": {"kind": "int", "min": 1, "max": 20, "step": 1},
    "RESIDUAL_WEIGHT_COUNT": {"kind": "float", "min": 0, "max": 1, "step": 0.05},
    "RESIDUAL_WEIGHT_AREA": {"kind": "float", "min": 0, "max": 1, "step": 0.05},
    "PROV_PRIOR_MEAN": {"kind": "float", "min": 0, "max": 10, "step": 0.1},
    "PROV_CONFIDENCE": {"kind": "float", "min": 0, "max": 100, "step": 0.5},
    "CLIP_BATCH_SIZE": {"kind": "int", "min": 0, "max": 128, "step": 1},
}
