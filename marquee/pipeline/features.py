"""Stage 4 scalar feature extraction, split into two phases.

**Style phase** (cheap, embedding-driven): CLIP embedding -> knn_sim +
aesthetic, plus the metadata scalars (resolution, provenance, lang_match).
Runs BEFORE OCR so the style/aesthetic gates can remove off-style and
low-quality candidates without paying the expensive multi-pass OCR for them.
Embeddings are batched through one ONNX run and cached on disk.

**Detail phase** (per-survivor): face detection, title colorfulness,
sharpness, and the OCR-derived text_residual — computed only for candidates
that passed the style gates, OCR, and pHash.
"""

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
from marquee.ml.preprocessing import preprocess_image
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
        # The LAION head works on the B/32 embedding family, including the
        # int8-quantized variant of the same backbone.
        if not self.config.AI_MODEL.startswith(self.aesthetic.model_name):
            raise RuntimeError(
                f"Aesthetic model mismatch: head={self.aesthetic.model_name!r}, "
                f"configured={self.config.AI_MODEL!r}"
            )
        if self.taste_store.size <= 0:
            raise RuntimeError("Taste store contains no exemplars")
        _ = self.encoder.session
        self.aesthetic.load()
        _ = self.face_detector.session

    # ------------------------------------------------------------------
    # Style phase — embedding + metadata, batched
    # ------------------------------------------------------------------

    def extract_style_batch(
        self,
        items: list[tuple[Path, PosterCandidate]],
    ) -> list[FeatureVector | Exception]:
        """Compute the embedding-driven and metadata scalars for many posters.

        Per-item failures (corrupt image, decode error) are returned as the
        exception in that slot; a session-level failure raises (systemic).
        """
        results: list[FeatureVector | Exception] = [
            Exception("not computed") for _ in items
        ]
        embeddings: dict[int, np.ndarray] = {}
        miss_indices: list[int] = []
        miss_pixels: list[np.ndarray] = []

        for index, (image_path, _candidate) in enumerate(items):
            cached = self._load_cached_embedding(image_path.name)
            if cached is not None:
                embeddings[index] = cached
                continue
            try:
                with Image.open(image_path) as image:
                    miss_pixels.append(preprocess_image(image.convert("RGB")))
                miss_indices.append(index)
            except Exception as exc:  # per-candidate data problem
                results[index] = exc

        if miss_indices:
            encoded = self.encoder.encode_batch(miss_pixels)
            for index, vector in zip(miss_indices, encoded, strict=True):
                embeddings[index] = vector
                self._save_cached_embedding(items[index][0].name, vector)

        for index, (_image_path, candidate) in enumerate(items):
            if index not in embeddings:
                continue
            embedding = embeddings[index]
            features = self._style_features(embedding, candidate)
            results[index] = features
        return results

    def extract_style(
        self,
        image_path: Path,
        candidate: PosterCandidate,
    ) -> FeatureVector:
        """Single-poster style phase (batch of one)."""
        result = self.extract_style_batch([(image_path, candidate)])[0]
        if isinstance(result, Exception):
            raise result
        return result

    def _style_features(
        self,
        embedding: np.ndarray,
        candidate: PosterCandidate,
    ) -> FeatureVector:
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
            knn_sim=self.taste_store.style_score(
                embedding,
                k=self.config.K_NEIGHBORS,
            ),
            aesthetic=self.aesthetic.score(embedding),
            title_colorfulness=0.0,
            text_residual=0.0,
            resolution=(candidate.width * candidate.height) / 1_000_000,
            sharpness=0.0,
            face_area=0.0,
            provenance=max(0.0, min(adjusted_vote / 10.0, 1.0)),
            lang_match=self._language_match(candidate.language),
            title_found=False,
        )
        normalize_features(features, self.config)
        return features

    # ------------------------------------------------------------------
    # Detail phase — image-level CV + OCR geometry, survivors only
    # ------------------------------------------------------------------

    def complete(
        self,
        features: FeatureVector,
        ocr_result: OCRCandidateResult,
    ) -> FeatureVector:
        """Fill in the detail scalars on an OCR/pHash survivor and renormalize."""
        if not ocr_result.accepted:
            raise ValueError("Features may only be completed for OCR survivors")

        image_bgr = cv2.imread(str(ocr_result.image_path), cv2.IMREAD_COLOR)
        if image_bgr is None:
            raise ValueError(f"Could not load image: {ocr_result.image_path}")

        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        image_area = float(image_bgr.shape[0] * image_bgr.shape[1])
        residual_area_fraction = (
            sum(box.area for box in ocr_result.residual_boxes) / image_area
            if image_area > 0
            else 0.0
        )

        features.text_residual = calculate_text_residual(
            len(ocr_result.residual_boxes),
            residual_area_fraction,
            self.config,
        )
        features.title_colorfulness = title_colorfulness(
            image_bgr,
            ocr_result.title_bbox,
        )
        features.title_found = ocr_result.title_bbox is not None
        features.sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        features.face_area = self.face_detector.face_area(image_bgr)
        normalize_features(features, self.config)
        return features

    def extract(
        self,
        ocr_result: OCRCandidateResult,
        candidate: PosterCandidate,
    ) -> FeatureVector:
        """One-shot extraction (style + detail) for a single OCR survivor."""
        features = self.extract_style(ocr_result.image_path, candidate)
        return self.complete(features, ocr_result)

    # ------------------------------------------------------------------
    # Embedding cache
    # ------------------------------------------------------------------

    def _load_cached_embedding(self, orig_filename: str) -> np.ndarray | None:
        cache_path = self._cache_path(orig_filename)
        if not cache_path.exists():
            return None
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
        return None

    def _save_cached_embedding(self, orig_filename: str, embedding: np.ndarray) -> None:
        cache_path = self._cache_path(orig_filename)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            cache_path,
            embedding=embedding,
            model_name=np.asarray(self.config.AI_MODEL),
            orig_filename=np.asarray(orig_filename),
        )

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
