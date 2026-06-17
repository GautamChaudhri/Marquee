"""Taste exemplar storage backed by model-tagged NumPy embeddings.

The profile carries up to four kinds of taste signal:

  - **CLIP embeddings** (positive + optional negative) for the primary
    ``knn_sim`` style score, combined with softmax-weighted k-NN.
  - **DINOv2 embeddings** (optional, positive + negative) for the
    ``dino_knn`` second style opinion — texture/medium similarity that CLIP's
    content-dominated space blurs. Same k-NN machinery, separate space.
  - **Negative exemplars**: a candidate closer to the disliked set than the
    liked set is penalized (``score = pos - w * max(0, neg - pos)``) without
    shifting scores for ordinary candidates.
  - **Calibration arrays**: per-feature raw values over the exemplars,
    powering exemplar-calibrated normalization (see ``calibration.py``).

Every embedding space is model-tagged; a mismatch between artifact and
configured model fails loudly (design 04 §12).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.artifact_codec import (
    GENRES_JSON_KEY,
    decode_json_string_array,
    decode_unicode_list,
    decode_unicode_scalar,
    ensure_safe_artifact,
    load_npz_safe,
)
from marquee.ml.calibration import TasteCalibration

logger = logging.getLogger(__name__)

DINO_SELF_KNN_KEY = "dino_self_knn"


def weighted_topk_mean(
    similarities: np.ndarray,
    k: int,
    *,
    weighting: str | None = None,
    temperature: float | None = None,
) -> float:
    """Combine the top-k cosine similarities into one style scalar."""
    if similarities.size == 0:
        return 0.0
    count = min(max(k, 1), int(similarities.size))
    top = np.partition(similarities, -count)[-count:]
    mode = weighting or pipeline_settings.KNN_WEIGHTING
    if mode == "softmax" and count > 1:
        temp = temperature or pipeline_settings.KNN_SOFTMAX_TEMP
        logits = (top - top.max()) / temp
        weights = np.exp(logits)
        weights /= weights.sum()
        return float(np.dot(weights, top))
    return float(top.mean())


class TasteStore(ABC):
    @property
    @abstractmethod
    def centroid_emb(self) -> np.ndarray: ...

    @property
    @abstractmethod
    def size(self) -> int: ...

    @abstractmethod
    def add(self, embedding: np.ndarray, *, metadata: dict | None = None) -> None: ...

    @abstractmethod
    def query_similar(self, embedding: np.ndarray, k: int = 10) -> list[float]: ...

    @abstractmethod
    def style_score(self, embedding: np.ndarray, k: int = 10) -> float: ...

    @abstractmethod
    def get_all(self) -> tuple[np.ndarray, list[dict | None]]: ...


class NumpyTasteStore(TasteStore):
    def __init__(
        self,
        profile_path: str | Path | None = None,
        *,
        expected_model_name: str | None = None,
    ):
        self.profile_path = Path(profile_path or pipeline_settings.TASTE_PROFILE_PATH)
        self.expected_model_name = expected_model_name or pipeline_settings.AI_MODEL
        self._embeddings: np.ndarray | None = None
        self._neg_embeddings: np.ndarray | None = None
        self._dino_embeddings: np.ndarray | None = None
        self._neg_dino_embeddings: np.ndarray | None = None
        self._dino_model_name: str | None = None
        self._dino_self_knn: np.ndarray | None = None
        self._calibration: TasteCalibration | None = None
        self._metadata: list[dict | None] = []
        self._centroid: np.ndarray | None = None

    def _ensure_loaded(self) -> None:
        if self._embeddings is not None:
            return
        if not self.profile_path.exists():
            raise FileNotFoundError(
                f"Taste profile not found: {self.profile_path}. "
                "Rebuild it with `python -m marquee.ml.taste_trainer`."
            )
        ensure_safe_artifact(self.profile_path, "taste_profile")
        with load_npz_safe(self.profile_path) as data:
            stored_model = decode_unicode_scalar(data["model_name"])
            if stored_model != self.expected_model_name:
                raise RuntimeError(
                    f"Taste profile model mismatch: artifact={stored_model!r}, "
                    f"configured={self.expected_model_name!r}. Rebuild the profile."
                )
            self._embeddings = np.asarray(data["embeddings"], dtype=np.float32)
            self._centroid = np.asarray(data["centroid_emb"], dtype=np.float32)
            names = decode_unicode_list(data["poster_names"])
            if self._embeddings.ndim != 2 or self._embeddings.shape[1] != 512:
                raise RuntimeError(
                    f"Invalid taste embedding shape: {self._embeddings.shape}"
                )
            if self._centroid.shape != (512,):
                raise RuntimeError(
                    f"Invalid taste centroid shape: {self._centroid.shape}"
                )
            if len(names) != self._embeddings.shape[0]:
                raise RuntimeError(
                    "Taste profile poster_names length does not match embeddings"
                )
            self._metadata = [{"filename": str(name)} for name in names]

            if "neg_embeddings" in data:
                negatives = np.asarray(data["neg_embeddings"], dtype=np.float32)
                if negatives.ndim != 2 or negatives.shape[1] != 512:
                    raise RuntimeError(
                        f"Invalid negative embedding shape: {negatives.shape}"
                    )
                self._neg_embeddings = negatives

            # Optional DINOv2 space (second style opinion).
            if "dino_embeddings" in data:
                self._dino_embeddings = np.asarray(
                    data["dino_embeddings"], dtype=np.float32
                )
                self._dino_model_name = decode_unicode_scalar(data["dino_model_name"])
                if self._dino_embeddings.shape[0] != self._embeddings.shape[0]:
                    raise RuntimeError(
                        "dino_embeddings count does not match CLIP exemplar count"
                    )
                if "neg_dino_embeddings" in data:
                    self._neg_dino_embeddings = np.asarray(
                        data["neg_dino_embeddings"], dtype=np.float32
                    )
                if DINO_SELF_KNN_KEY in data:
                    self._dino_self_knn = np.asarray(
                        data[DINO_SELF_KNN_KEY], dtype=np.float64
                    )
            profile_arrays = {key: data[key] for key in data.files}
            if GENRES_JSON_KEY in profile_arrays:
                profile_arrays["genres"] = decode_json_string_array(profile_arrays[GENRES_JSON_KEY])
            self._calibration = TasteCalibration.from_profile_arrays(profile_arrays)

        logger.info(
            "Loaded taste profile %s: %d exemplars (%d negative), "
            "dino=%s, calibration=%s",
            self.profile_path.name,
            self.size,
            self.negative_size,
            self._dino_model_name or "absent",
            (
                f"{len(self._calibration.calibrated_features)} features"
                if self._calibration
                else "absent"
            ),
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def centroid_emb(self) -> np.ndarray:
        self._ensure_loaded()
        return self._centroid  # type: ignore[return-value]

    @property
    def size(self) -> int:
        self._ensure_loaded()
        return int(self._embeddings.shape[0])  # type: ignore[union-attr]

    @property
    def negative_size(self) -> int:
        self._ensure_loaded()
        if self._neg_embeddings is None:
            return 0
        return int(self._neg_embeddings.shape[0])

    @property
    def has_dino(self) -> bool:
        self._ensure_loaded()
        return self._dino_embeddings is not None

    @property
    def dino_model_name(self) -> str | None:
        self._ensure_loaded()
        return self._dino_model_name

    @property
    def calibration(self) -> TasteCalibration | None:
        self._ensure_loaded()
        return self._calibration

    def dino_knn_norm_range(self) -> tuple[float, float] | None:
        """p5/p95 of the exemplars' own dino k-NN sims — the empirically
        correct normalization range for dino_knn on this profile."""
        self._ensure_loaded()
        if self._dino_self_knn is None or self._dino_self_knn.size < 5:
            return None
        p5, p95 = np.percentile(self._dino_self_knn, [5, 95])
        if p95 - p5 <= 1e-6:
            return None
        return float(p5), float(p95)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, embedding: np.ndarray, *, metadata: dict | None = None) -> None:
        self._ensure_loaded()
        vector = np.asarray(embedding, dtype=np.float32).reshape(1, 512)
        vector /= np.maximum(np.linalg.norm(vector, axis=1, keepdims=True), 1e-10)
        self._embeddings = np.concatenate((self._embeddings, vector), axis=0)  # type: ignore[arg-type]
        self._metadata.append(metadata)
        self._centroid = _compute_centroid(self._embeddings)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def query_similar(self, embedding: np.ndarray, k: int = 10) -> list[float]:
        self._ensure_loaded()
        if self.size == 0:
            return []
        vector = np.asarray(embedding, dtype=np.float32).reshape(512)
        similarities = self._embeddings @ vector  # type: ignore[operator]
        count = min(max(k, 1), self.size)
        indices = np.argsort(similarities)[::-1][:count]
        return [float(similarities[index]) for index in indices]

    def style_score(self, embedding: np.ndarray, k: int = 10) -> float:
        """The knn_sim scalar: weighted positive k-NN minus junk-proximity penalty."""
        self._ensure_loaded()
        return self._contrastive_knn(
            np.asarray(embedding, dtype=np.float32).reshape(512),
            self._embeddings,  # type: ignore[arg-type]
            self._neg_embeddings,
            k,
        )

    def dino_style_score(self, dino_embedding: np.ndarray, k: int = 10) -> float:
        """Second-opinion style score in the DINOv2 space (same contrastive k-NN)."""
        self._ensure_loaded()
        if self._dino_embeddings is None:
            raise RuntimeError(
                "Taste profile has no DINOv2 embeddings — rebuild it with "
                "`python -m marquee.ml.taste_trainer` after exporting the model."
            )
        dim = self._dino_embeddings.shape[1]
        return self._contrastive_knn(
            np.asarray(dino_embedding, dtype=np.float32).reshape(dim),
            self._dino_embeddings,
            self._neg_dino_embeddings,
            k,
        )

    @staticmethod
    def _contrastive_knn(
        vector: np.ndarray,
        positives: np.ndarray,
        negatives: np.ndarray | None,
        k: int,
    ) -> float:
        positive = weighted_topk_mean(positives @ vector, k)
        if negatives is None or pipeline_settings.TASTE_NEG_WEIGHT <= 0:
            return positive
        negative = weighted_topk_mean(
            negatives @ vector,
            min(k, int(negatives.shape[0])),
        )
        penalty = pipeline_settings.TASTE_NEG_WEIGHT * max(0.0, negative - positive)
        return positive - penalty

    def get_all(self) -> tuple[np.ndarray, list[dict | None]]:
        self._ensure_loaded()
        return self._embeddings.copy(), list(self._metadata)  # type: ignore[union-attr]


def _compute_centroid(vectors: np.ndarray) -> np.ndarray:
    centroid = vectors.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    return (centroid / norm if norm > 1e-10 else centroid).astype(np.float32)
