"""Stage 4 scalar feature extraction for OCR survivors."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.core.poster_sources.tmdb import PosterCandidate
from marquee.ml.aesthetic import AestheticPredictor
from marquee.ml.colorfulness import title_colorfulness
from marquee.ml.embedding import CLIPImageEncoder
from marquee.ml.face import FaceDetector
from marquee.ml.normalize import normalize_features
from marquee.ml.taste_store import NumpyTasteStore, TasteStore
from marquee.pipeline.types import FeatureVector, OCRCandidateResult

logger = logging.getLogger(__name__)


def calculate_text_residual(
    box_count: int,
    area_fraction: float,
    config: PipelineSettings = pipeline_settings,
) -> float:
    """Blend residual text count and area into a capped 0-1 raw value."""
    count_component = min(box_count / config.RESIDUAL_COUNT_SAT, 1.0)
    return min(
        config.RESIDUAL_WEIGHT_COUNT * count_component
        + config.RESIDUAL_WEIGHT_AREA * area_fraction,
        1.0,
    )


class FeatureExtractor:
    """Compute and normalize the complete Phase-0 feature vector."""

    def __init__(
        self,
        *,
        config: PipelineSettings = pipeline_settings,
        encoder: CLIPImageEncoder | None = None,
        aesthetic: AestheticPredictor | None = None,
        face_detector: FaceDetector | None = None,
        taste_store: TasteStore | None = None,
    ):
        self.config = config
        self.encoder = encoder or CLIPImageEncoder()
        self.aesthetic = aesthetic or AestheticPredictor()
        self.face_detector = face_detector or FaceDetector()
        self.taste_store = taste_store or NumpyTasteStore()

    def preflight(self) -> None:
        """Load every required artifact before per-candidate error handling."""
        if self.encoder.model_name != self.config.AI_MODEL:
            raise RuntimeError(
                f"Encoder model mismatch: encoder={self.encoder.model_name!r}, "
                f"configured={self.config.AI_MODEL!r}"
            )
        if self.aesthetic.model_name != self.config.AI_MODEL:
            raise RuntimeError(
                f"Aesthetic model mismatch: head={self.aesthetic.model_name!r}, "
                f"configured={self.config.AI_MODEL!r}"
            )
        if self.taste_store.size <= 0:
            raise RuntimeError("Taste store contains no exemplars")
        _ = self.encoder.session
        self.aesthetic.load()
        _ = self.face_detector.session

    def extract(
        self,
        ocr_result: OCRCandidateResult,
        candidate: PosterCandidate,
    ) -> FeatureVector:
        if not ocr_result.accepted:
            raise ValueError("Features may only be extracted for OCR survivors")

        image_bgr = cv2.imread(str(ocr_result.image_path), cv2.IMREAD_COLOR)
        if image_bgr is None:
            raise ValueError(f"Could not load image: {ocr_result.image_path}")

        embedding = self._get_embedding(
            ocr_result.image_path,
            ocr_result.image_path.name,
        )
        similarities = self.taste_store.query_similar(
            embedding,
            k=self.config.K_NEIGHBORS,
        )
        if not similarities:
            raise RuntimeError("Taste store contains no exemplars")

        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        image_area = float(image_bgr.shape[0] * image_bgr.shape[1])
        residual_area_fraction = (
            sum(box.area for box in ocr_result.residual_boxes) / image_area
            if image_area > 0
            else 0.0
        )
        text_residual = calculate_text_residual(
            len(ocr_result.residual_boxes),
            residual_area_fraction,
            self.config,
        )

        vote_count = max(candidate.vote_count, 0)
        adjusted_vote = (
            (vote_count / (vote_count + self.config.PROV_CONFIDENCE))
            * candidate.vote_average
            + (
                self.config.PROV_CONFIDENCE
                / (vote_count + self.config.PROV_CONFIDENCE)
            )
            * self.config.PROV_PRIOR_MEAN
        )

        features = FeatureVector(
            knn_sim=float(np.mean(similarities)),
            aesthetic=self.aesthetic.score(embedding),
            title_colorfulness=title_colorfulness(
                image_bgr,
                ocr_result.title_bbox,
            ),
            text_residual=text_residual,
            resolution=(candidate.width * candidate.height) / 1_000_000,
            sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
            face_area=self.face_detector.face_area(image_bgr),
            provenance=max(0.0, min(adjusted_vote / 10.0, 1.0)),
            lang_match=self._language_match(candidate.language),
        )
        normalize_features(features, self.config)
        return features

    def _get_embedding(self, image_path: Path, orig_filename: str) -> np.ndarray:
        cache_path = self._cache_path(orig_filename)
        if cache_path.exists():
            try:
                with np.load(cache_path, allow_pickle=False) as cached:
                    model_name = str(np.asarray(cached["model_name"]).item())
                    cached_filename = str(np.asarray(cached["orig_filename"]).item())
                    if (
                        model_name == self.config.AI_MODEL
                        and cached_filename == orig_filename
                    ):
                        return np.asarray(cached["embedding"], dtype=np.float32)
            except (OSError, ValueError, KeyError):
                logger.warning("Discarding invalid embedding cache: %s", cache_path)

        with Image.open(image_path) as image:
            embedding = self.encoder.encode(image.convert("RGB"))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            cache_path,
            embedding=embedding,
            model_name=np.asarray(self.config.AI_MODEL),
            orig_filename=np.asarray(orig_filename),
        )
        return embedding

    def _cache_path(self, orig_filename: str) -> Path:
        key = hashlib.sha256(
            f"{self.config.AI_MODEL}:{orig_filename}".encode()
        ).hexdigest()
        return (
            self.config.EMBEDDING_CACHE_DIR
            / self.config.AI_MODEL
            / f"{key}.npz"
        )

    def _language_match(self, language: str | None) -> float:
        if language == self.config.PREFERRED_LANG:
            return 1.0
        if language is None:
            return 0.5
        return 0.2
