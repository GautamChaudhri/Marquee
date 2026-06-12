"""Feature normalization: fixed Phase-0 ramps + profile-derived ranges.

Core features keep their documented fixed ranges (min-max with clipping).
The new scorer features:

  - ``dino_knn`` — normalized against the taste profile's own statistics
    when available (p5..p95 of the exemplars' self k-NN similarities, the
    empirical "belongs in this profile" range), falling back to a fixed
    range otherwise.
  - ``taste_typicality`` — already a 0..1 KDE typicality; identity.
  - ``quality_artifacts`` — monotonic badness blend inverted to cleanliness.

Per-fine-feature calibration (the KDE typicality behind taste_typicality)
lives in ``calibration.py`` and is applied in the feature extractor; this
module only maps the top-level scorer features.
"""

from __future__ import annotations

import math

from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.pipeline.types import FeatureVector

# Fallback dino_knn range when the profile carries no self-knn statistics.
_DINO_KNN_FALLBACK_MIN = 0.3
_DINO_KNN_FALLBACK_MAX = 0.8


def _clip(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def quality_artifact_raw(
    blockiness: float,
    noise_sigma: float,
    config: PipelineSettings = pipeline_settings,
) -> float:
    """Blend the artifact metrics into one 0..1 badness scalar."""
    block_component = min(max(blockiness, 0.0) / config.QUALITY_BLOCKINESS_SAT, 1.0)
    noise_component = min(max(noise_sigma, 0.0) / config.QUALITY_NOISE_SAT, 1.0)
    return 0.5 * block_component + 0.5 * noise_component


def normalize_features(
    features: FeatureVector,
    config: PipelineSettings = pipeline_settings,
    *,
    dino_knn_range: tuple[float, float] | None = None,
) -> dict[str, float]:
    """Orient every feature so 1.0 is better and 0.0 is worse.

    Features whose value is None (disabled / model absent) are omitted from
    the normalized dict entirely — the scorer treats absent keys as
    inactive and renormalizes the remaining weights.
    """
    knn_span = config.NORM_KNN_MAX - config.NORM_KNN_MIN
    normalized = {
        "knn_sim": _clip(
            (features.knn_sim - config.NORM_KNN_MIN) / knn_span
            if knn_span > 0
            else 0.0
        ),
        "aesthetic": _clip(features.aesthetic / config.NORM_AESTHETIC_MAX),
        "title_colorfulness": (
            _clip(features.title_colorfulness / config.NORM_TITLE_COLORFULNESS_MAX)
            if features.title_found
            else _clip(config.NORM_TITLE_COLORFULNESS_NEUTRAL)
        ),
        "text_residual": _clip(1.0 - features.text_residual),
        "resolution": _clip(features.resolution / config.NORM_RESOLUTION_MAX_MP),
        "sharpness": _clip(
            math.log1p(max(features.sharpness, 0.0))
            / math.log1p(config.NORM_SHARPNESS_MAX)
        ),
        "face_area": _clip(1.0 - features.face_area),
        "provenance": _clip(features.provenance),
        "lang_match": _clip(features.lang_match),
    }

    if features.dino_knn is not None:
        low, high = dino_knn_range or (
            _DINO_KNN_FALLBACK_MIN,
            _DINO_KNN_FALLBACK_MAX,
        )
        span = high - low
        normalized["dino_knn"] = _clip(
            (features.dino_knn - low) / span if span > 0 else 0.0
        )

    if features.taste_typicality is not None:
        normalized["taste_typicality"] = _clip(features.taste_typicality)

    if features.quality_artifacts is not None:
        # Raw value is a 0..1 badness blend; invert to cleanliness.
        normalized["quality_artifacts"] = _clip(1.0 - features.quality_artifacts)

    if features.official_family is not None:
        official_span = config.NORM_OFFICIAL_MAX - config.NORM_OFFICIAL_MIN
        normalized["official_family"] = _clip(
            (features.official_family - config.NORM_OFFICIAL_MIN) / official_span
            if official_span > 0
            else 0.0
        )

    features.normalized = normalized
    return normalized
