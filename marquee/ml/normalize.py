"""Fixed Phase-0 feature normalization."""

from __future__ import annotations

import math

from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.pipeline.types import FeatureVector


def _clip(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def normalize_features(
    features: FeatureVector,
    config: PipelineSettings = pipeline_settings,
) -> dict[str, float]:
    """Orient every feature so 1.0 is better and 0.0 is worse."""
    knn_span = config.NORM_KNN_MAX - config.NORM_KNN_MIN
    normalized = {
        "knn_sim": _clip(
            (features.knn_sim - config.NORM_KNN_MIN) / knn_span
            if knn_span > 0
            else 0.0
        ),
        "aesthetic": _clip(features.aesthetic / config.NORM_AESTHETIC_MAX),
        "title_colorfulness": _clip(
            features.title_colorfulness / config.NORM_TITLE_COLORFULNESS_MAX
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
    features.normalized = normalized
    return normalized
