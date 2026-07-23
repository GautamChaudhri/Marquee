"""Retroactive feature backfill for rejected posters (design 09 §16.4).

When a user overrides the auto-pick with a poster that was rejected *before*
its detail features were computed (resolution gate, or any earlier stop), the
bounded residual trainer still needs a full feature vector for that label. Computing it
at feedback time is a rare, one-off cost — far cheaper than computing detail
features for every rejected poster on every run.

This runs the style phase, a single-image OCR pass, and the detail phase
(via a forced-accepted OCR result, since ``FeatureExtractor._complete_one``
only fills detail features for accepted survivors).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from marquee.core.poster_sources.tmdb import PosterCandidate
from marquee.pipeline.features import FeatureExtractor
from marquee.pipeline.ocr_filter import PosterTextFilter
from marquee.pipeline.types import FeatureVector

logger = logging.getLogger(__name__)


def compute_full_features(
    *,
    image_path: Path,
    candidate: PosterCandidate,
    movie_title: str,
    extractor: FeatureExtractor,
) -> FeatureVector:
    """Style + OCR + detail features for one (possibly rejected) poster.

    ``extractor`` must already be preflighted (reuse the RunManager's).
    """
    features = extractor.extract_style(image_path, candidate)

    ocr_result = PosterTextFilter(movie_title, director=None).is_acceptable(image_path)
    # Force-accept so the detail phase runs regardless of the gate verdict —
    # we want the measurements, not a pass/fail decision here.
    forced = replace(ocr_result, accepted=True, image_path=image_path)

    return extractor.complete(features, forced)


def candidate_from_image(image_path: Path, *, language: str | None = None) -> PosterCandidate:
    """Best-effort PosterCandidate from the image file alone.

    Used when TMDB metadata for the poster cannot be recovered (the file is
    still on disk in 0-originals/). Votes default to zero (neutral provenance).
    """
    from PIL import Image  # noqa: PLC0415

    with Image.open(image_path) as image:
        width, height = image.size
    aspect = (width / height) if height else 0.0
    return PosterCandidate(
        file_path=f"/{image_path.name}",
        width=width,
        height=height,
        aspect_ratio=aspect,
        language=language,
        vote_average=0.0,
        vote_count=0,
    )
