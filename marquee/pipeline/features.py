"""Stage 4 scalar feature extraction, split into two phases.

**Style phase** (cheap, embedding-driven): CLIP embedding -> knn_sim +
aesthetic + zero-shot style axes, plus the metadata scalars (resolution,
provenance, lang_match). Runs BEFORE OCR so the style/aesthetic gates can
remove off-style and low-quality candidates without paying the expensive
multi-pass OCR for them. Embeddings are batched through one ONNX run and
cached on disk.

**Detail phase** (survivors only): DINOv2 second-opinion k-NN (batched, GPU
tiers), face/person geometry, the classic-CV palette/composition pack,
quality artifacts, title geometry, sharpness, text_residual — then the
exemplar-calibrated typicality aggregation and final normalization.

Every measured value lands either in the scorer-level FeatureVector fields
or in ``FeatureVector.extended``; both are logged and serialized to
``pipeline_run.json`` so each ranking decision can be cross-referenced
feature by feature.
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
from marquee.ml.dino import DinoImageEncoder, dino_active, preprocess_dino
from marquee.ml.embedding import CLIPImageEncoder
from marquee.ml.face import FaceDetector
from marquee.ml.normalize import normalize_features, quality_artifact_raw
from marquee.ml.person import PersonDetector
from marquee.ml.preprocessing import preprocess_image
from marquee.ml.taste_store import NumpyTasteStore, TasteStore
from marquee.ml.visual_features import (
    composition_features,
    face_geometry,
    palette_features,
    quality_artifact_features,
    standardize_width,
    title_geometry,
)
from marquee.ml.zeroshot import ZeroShotAxes
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


def load_cached_embedding(
    orig_filename: str,
    config: PipelineSettings = pipeline_settings,
) -> np.ndarray | None:
    """Load a candidate's CLIP embedding from the on-disk run cache.

    The pipeline persists every candidate's embedding here during the style
    stage, so the feedback path can add an approved poster to the taste
    profile without re-encoding on the GPU. Returns None if not cached.
    """
    key = hashlib.sha256(f"{config.AI_MODEL}:{orig_filename}".encode()).hexdigest()
    cache_path = config.EMBEDDING_CACHE_DIR / config.AI_MODEL / f"{key}.npz"
    if not cache_path.exists():
        return None
    try:
        with np.load(cache_path, allow_pickle=False) as cached:
            model_name = str(np.asarray(cached["model_name"]).item())
            cached_filename = str(np.asarray(cached["orig_filename"]).item())
            if model_name == config.AI_MODEL and cached_filename == orig_filename:
                return np.asarray(cached["embedding"], dtype=np.float32)
    except (OSError, ValueError, KeyError):
        logger.warning("Discarding invalid embedding cache: %s", cache_path)
    return None


class FeatureExtractor:
    """Compute and normalize the complete feature vector."""

    def __init__(
        self,
        *,
        config: PipelineSettings = pipeline_settings,
        encoder: CLIPImageEncoder | None = None,
        aesthetic: AestheticPredictor | None = None,
        face_detector: FaceDetector | None = None,
        taste_store: TasteStore | None = None,
        dino_encoder: DinoImageEncoder | None = None,
        person_detector: PersonDetector | None = None,
        zeroshot_axes: ZeroShotAxes | None = None,
    ):
        self.config = config
        self.encoder = encoder or CLIPImageEncoder()
        self.aesthetic = aesthetic or AestheticPredictor()
        self.face_detector = face_detector or FaceDetector()
        self.taste_store = taste_store or NumpyTasteStore()
        self.dino_encoder = dino_encoder or DinoImageEncoder()
        self.person_detector = person_detector or PersonDetector()
        self._zeroshot = zeroshot_axes
        self._zeroshot_loaded = zeroshot_axes is not None
        self._dino_on = False
        self._person_on = False
        self._quality_on = False
        self._calibration = None
        self._dino_knn_range: tuple[float, float] | None = None

    # ------------------------------------------------------------------
    # Preflight — resolve and LOG every optional-feature decision
    # ------------------------------------------------------------------

    def preflight(self) -> None:
        """Load required artifacts, resolve optional features, log decisions."""
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

        # ── DINOv2 (rec 4): hardware-tiered, model + profile must agree ──
        self._dino_on = False
        if dino_active(log=True):
            if not self.dino_encoder.available:
                logger.error(
                    "DINO | requested but model file missing (%s) — dino_knn "
                    "disabled for this run. Export with: python -m marquee.ml.dino",
                    self.dino_encoder._model_path,
                )
            elif not getattr(self.taste_store, "has_dino", False):
                logger.error(
                    "DINO | model present but taste profile has no DINOv2 "
                    "embeddings — rebuild it: python -m marquee.ml.taste_trainer"
                )
            elif self.taste_store.dino_model_name != self.dino_encoder.model_name:
                logger.error(
                    "DINO | model mismatch: encoder=%r profile=%r — dino_knn "
                    "disabled. Rebuild the profile.",
                    self.dino_encoder.model_name,
                    self.taste_store.dino_model_name,
                )
            else:
                _ = self.dino_encoder.session
                self._dino_on = True
                self._dino_knn_range = self.taste_store.dino_knn_norm_range()
                logger.info(
                    "DINO | ACTIVE | model=%s | knn norm range=%s",
                    self.dino_encoder.model_name,
                    (
                        f"profile p5..p95 = {self._dino_knn_range[0]:.4f}.."
                        f"{self._dino_knn_range[1]:.4f}"
                        if self._dino_knn_range
                        else "fixed fallback"
                    ),
                )

        # ── Quality pack + person detector (rec 5) ───────────────────────
        self._quality_on = self.config.EXTRA_QUALITY_ENABLED
        self._person_on = False
        if not self._quality_on:
            logger.info("QUALITY | disabled (EXTRA_QUALITY_ENABLED=false)")
        else:
            logger.info("QUALITY | artifact metrics active (blockiness + noise)")
            if self.person_detector.available:
                _ = self.person_detector.session
                self._person_on = True
                logger.info(
                    "PERSON | ACTIVE | model=%s", self.person_detector.model_path.name
                )
            else:
                logger.warning(
                    "PERSON | model missing (%s) — person features skipped. "
                    "Export with: yolo export model=yolo11n.pt format=onnx dynamic=True",
                    self.person_detector.model_path,
                )

        # ── Zero-shot axes (rec 1) ───────────────────────────────────────
        if not self._zeroshot_loaded:
            self._zeroshot = ZeroShotAxes.load()  # logs its own status
            self._zeroshot_loaded = True

        # ── Exemplar calibration (rec 2) ─────────────────────────────────
        self._calibration = None
        if not self.config.CALIBRATION_ENABLED:
            logger.info("CALIBRATION | disabled (CALIBRATION_ENABLED=false)")
        else:
            calibration = getattr(self.taste_store, "calibration", None)
            if calibration is None:
                logger.warning(
                    "CALIBRATION | taste profile has no feature distributions — "
                    "taste_typicality disabled. Rebuild the profile: "
                    "python -m marquee.ml.taste_trainer"
                )
            else:
                self._calibration = calibration
                for line in calibration.describe():
                    logger.info(line)

    # ------------------------------------------------------------------
    # Style phase — embedding + metadata, batched
    # ------------------------------------------------------------------

    def extract_style_batch(
        self,
        items: list[tuple[Path, PosterCandidate]],
        *,
        primary_name: str | None = None,
    ) -> list[FeatureVector | Exception]:
        """Compute the embedding-driven and metadata scalars for many posters.

        Per-item failures (corrupt image, decode error) are returned as the
        exception in that slot; a session-level failure raises (systemic).

        When ``primary_name`` (the movie's TMDB primary poster filename) is
        among the items, every candidate additionally gets
        ``official_family`` — its CLIP cosine to that primary, the "official
        key-art family" signal.
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

        primary_embedding: np.ndarray | None = None
        if primary_name is not None:
            primary_embedding = next(
                (
                    embeddings[index]
                    for index, (image_path, _candidate) in enumerate(items)
                    if image_path.name == primary_name and index in embeddings
                ),
                None,
            )
            if primary_embedding is None:
                logger.warning(
                    "OFFICIAL | primary poster %s not embedded this run — "
                    "official_family disabled (weight redistributed)",
                    primary_name,
                )
            else:
                logger.info(
                    "OFFICIAL | primary=%s — scoring official_family for %d "
                    "candidate(s)",
                    primary_name,
                    len(embeddings),
                )

        for index, (_image_path, candidate) in enumerate(items):
            if index not in embeddings:
                continue
            official_family = (
                float(np.dot(embeddings[index], primary_embedding))
                if primary_embedding is not None
                else None
            )
            results[index] = self._style_features(
                embeddings[index],
                candidate,
                official_family=official_family,
            )
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
        *,
        official_family: float | None = None,
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
            official_family=official_family,
            title_found=False,
        )
        if self._zeroshot is not None:
            features.extended.update(self._zeroshot.scores(embedding))
        features.extended["aspect_ratio_deviation"] = abs(
            candidate.aspect_ratio - 2.0 / 3.0
        )
        normalize_features(features, self.config)
        return features

    # ------------------------------------------------------------------
    # Detail phase — survivors only, dino batched up front
    # ------------------------------------------------------------------

    def complete_batch(
        self,
        items: list[tuple[FeatureVector, OCRCandidateResult]],
    ) -> list[FeatureVector | Exception]:
        """Fill in the detail scalars for OCR/pHash survivors.

        DINOv2 runs as one batched pass over all survivors; the per-poster
        CV work follows. Per-item failures come back as exceptions in their
        slot, everything else continues (design 04 §13).
        """
        dino_scores: dict[int, float] = {}
        if self._dino_on and items:
            dino_indices: list[int] = []
            dino_pixels: list[np.ndarray] = []
            for index, (_features, ocr_result) in enumerate(items):
                try:
                    with Image.open(ocr_result.image_path) as image:
                        dino_pixels.append(preprocess_dino(image.convert("RGB")))
                    dino_indices.append(index)
                except Exception:  # noqa: BLE001 — handled per-item below
                    continue  # the CV pass will surface the read error
            if dino_indices:
                encoded = self.dino_encoder.encode_batch(dino_pixels)
                for index, vector in zip(dino_indices, encoded, strict=True):
                    dino_scores[index] = self.taste_store.dino_style_score(
                        vector, k=self.config.K_NEIGHBORS
                    )

        results: list[FeatureVector | Exception] = []
        for index, (features, ocr_result) in enumerate(items):
            try:
                results.append(
                    self._complete_one(
                        features,
                        ocr_result,
                        dino_knn=dino_scores.get(index),
                    )
                )
            except Exception as exc:  # per-candidate data problem
                results.append(exc)
        return results

    def complete(
        self,
        features: FeatureVector,
        ocr_result: OCRCandidateResult,
    ) -> FeatureVector:
        """Single-survivor detail phase (batch of one)."""
        result = self.complete_batch([(features, ocr_result)])[0]
        if isinstance(result, Exception):
            raise result
        return result

    def _complete_one(
        self,
        features: FeatureVector,
        ocr_result: OCRCandidateResult,
        *,
        dino_knn: float | None,
    ) -> FeatureVector:
        if not ocr_result.accepted:
            raise ValueError("Features may only be completed for OCR survivors")

        image_bgr = cv2.imread(str(ocr_result.image_path), cv2.IMREAD_COLOR)
        if image_bgr is None:
            raise ValueError(f"Could not load image: {ocr_result.image_path}")
        standardized = standardize_width(image_bgr)
        std_h, std_w = standardized.shape[:2]

        # ── Core detail scalars (unchanged semantics) ────────────────────
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

        face_boxes = self.face_detector.detect(image_bgr)
        face_total = sum(
            max(0.0, x2 - x1) * max(0.0, y2 - y1) for x1, y1, x2, y2 in face_boxes
        )
        features.face_area = (
            min(face_total / image_area, 1.0) if image_area > 0 else 0.0
        )

        # ── Extended features (recs 1, 3, 5) ─────────────────────────────
        features.extended.update(palette_features(standardized))
        features.extended.update(composition_features(standardized))
        features.extended.update(
            face_geometry(
                face_boxes, image_bgr.shape[1], image_bgr.shape[0]
            )
        )
        features.extended.update(
            title_geometry(
                ocr_result.title_bbox, image_bgr.shape[1], image_bgr.shape[0]
            )
        )
        if self._quality_on:
            artifacts = quality_artifact_features(standardized)
            features.extended.update(artifacts)
            features.quality_artifacts = quality_artifact_raw(
                artifacts["blockiness"],
                artifacts["noise_sigma"],
                self.config,
            )
            if self._person_on:
                features.extended.update(
                    self.person_detector.person_features(standardized)
                )

        features.dino_knn = dino_knn

        # ── Exemplar-calibrated typicality (rec 2) ───────────────────────
        if self._calibration is not None:
            features.taste_typicality = self._aggregate_typicality(features)

        normalize_features(
            features,
            self.config,
            dino_knn_range=self._dino_knn_range,
        )
        return features

    def _aggregate_typicality(self, features: FeatureVector) -> float | None:
        """Mean KDE typicality over the configured fine-grained features.

        Per-feature values land in ``typicality_detail`` for the logs; the
        aggregate is the scorer-level taste_typicality. Features without a
        calibration band or without a measured value simply don't vote.
        """
        detail: dict[str, float] = {}
        for name in self.config.TYPICALITY_FEATURES:
            value = (
                features.aesthetic
                if name == "aesthetic"
                else features.extended.get(name)
            )
            if value is None or not np.isfinite(value):
                continue
            typicality = self._calibration.typicality(name, float(value))
            if typicality is not None:
                detail[name] = typicality
        features.typicality_detail = detail
        if not detail:
            return None
        return float(np.mean(list(detail.values())))

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
