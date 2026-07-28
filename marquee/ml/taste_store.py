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

import hashlib
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


def compute_taste_profile_hash(profile_path: Path | str | None = None) -> str:
    """Compute SHA-256 hash of the taste profile for reproducibility tracking.

    Returns the first 12 hex characters of the hash (48 bits — collision-free
    for practical use). Used to version pipeline runs so rescoring against
    different profiles is detectable.

    Returns "missing" if the profile file doesn't exist.
    """
    path = Path(profile_path or pipeline_settings.TASTE_PROFILE_PATH)
    if not path.is_file():
        return "missing"
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return digest[:12]  # 48 bits — more than enough for uniqueness
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to hash taste profile at %s: %s", path, exc)
        return "error"


def weighted_topk_mean(
    similarities: np.ndarray,
    k: int,
    *,
    evidence_weights: np.ndarray | None = None,
    weighting: str | None = None,
    temperature: float | None = None,
) -> float:
    """Combine the top-k cosine similarities into one style scalar."""
    if similarities.size == 0:
        return 0.0
    if evidence_weights is not None:
        if evidence_weights.shape != similarities.shape:
            raise ValueError("taste evidence weights do not match similarities")
        if (
            not np.all(np.isfinite(evidence_weights))
            or np.any(evidence_weights <= 0)
            or np.any(evidence_weights > 1)
        ):
            raise ValueError("taste evidence weights must be finite and in (0, 1]")
    count = min(max(k, 1), int(similarities.size))
    indices = np.argpartition(similarities, -count)[-count:]
    top = similarities[indices]
    top_evidence = evidence_weights[indices] if evidence_weights is not None else None
    mode = weighting or pipeline_settings.KNN_WEIGHTING
    if mode == "softmax" and count > 1:
        temp = temperature or pipeline_settings.KNN_SOFTMAX_TEMP
        logits = (top - top.max()) / temp
        weights = np.exp(logits)
        if top_evidence is not None:
            weights *= top_evidence
        weights /= weights.sum()
        return float(np.dot(weights, top))
    if top_evidence is not None:
        return float(np.average(top, weights=top_evidence))
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
        self._embedding_weights: np.ndarray | None = None
        self._neg_embeddings: np.ndarray | None = None
        self._neg_embedding_weights: np.ndarray | None = None
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
        _kind = (
            "taste_profile_tv" if "taste_profile_tv" in self.profile_path.name else "taste_profile"
        )
        ensure_safe_artifact(self.profile_path, _kind)
        with load_npz_safe(self.profile_path) as data:
            stored_model = decode_unicode_scalar(data["model_name"])
            if stored_model != self.expected_model_name:
                raise RuntimeError(
                    f"Taste profile model mismatch: artifact={stored_model!r}, "
                    f"configured={self.expected_model_name!r}. Rebuild the profile."
                )
            self._embeddings = np.asarray(data["embeddings"], dtype=np.float32)
            self._embedding_weights = _profile_weights(
                data.get("embedding_weights", None),
                self._embeddings.shape[0],
                label="positive",
            )
            self._centroid = np.asarray(data["centroid_emb"], dtype=np.float32)
            names = decode_unicode_list(data["poster_names"])
            kinds = (
                decode_unicode_list(data["asset_kinds"])
                if "asset_kinds" in data
                else ["movie"] * len(names)
            )
            if self._embeddings.ndim != 2 or self._embeddings.shape[1] != 512:
                raise RuntimeError(f"Invalid taste embedding shape: {self._embeddings.shape}")
            if self._centroid.shape != (512,):
                raise RuntimeError(f"Invalid taste centroid shape: {self._centroid.shape}")
            if len(names) != self._embeddings.shape[0]:
                raise RuntimeError("Taste profile poster_names length does not match embeddings")
            # TV profiles built from the library carry the subject each poster came
            # from; movie profiles do not, so those two keys stay absent rather than
            # being filled with placeholders.
            series_titles = (
                decode_unicode_list(data["series_titles"])
                if "series_titles" in data
                else [""] * len(names)
            )
            seasons = (
                [int(value) for value in np.asarray(data["season_numbers"]).tolist()]
                if "season_numbers" in data
                else [-1] * len(names)
            )
            self._metadata = [
                {
                    "filename": str(name),
                    "asset_kind": str(kind),
                    **({"series_title": title} if title else {}),
                    **({"season_number": season} if season >= 0 else {}),
                }
                for name, kind, title, season in zip(
                    names, kinds, series_titles, seasons, strict=True
                )
            ]

            if "neg_embeddings" in data:
                negatives = np.asarray(data["neg_embeddings"], dtype=np.float32)
                if negatives.ndim != 2 or negatives.shape[1] != 512:
                    raise RuntimeError(f"Invalid negative embedding shape: {negatives.shape}")
                self._neg_embeddings = negatives
                self._neg_embedding_weights = _profile_weights(
                    data.get("neg_embedding_weights", None),
                    negatives.shape[0],
                    label="negative",
                )

            # Optional DINOv2 space (second style opinion).
            if "dino_embeddings" in data:
                self._dino_embeddings = np.asarray(data["dino_embeddings"], dtype=np.float32)
                self._dino_model_name = decode_unicode_scalar(data["dino_model_name"])
                if self._dino_embeddings.shape[0] != self._embeddings.shape[0]:
                    raise RuntimeError("dino_embeddings count does not match CLIP exemplar count")
                if "neg_dino_embeddings" in data:
                    self._neg_dino_embeddings = np.asarray(
                        data["neg_dino_embeddings"], dtype=np.float32
                    )
                if DINO_SELF_KNN_KEY in data:
                    self._dino_self_knn = np.asarray(data[DINO_SELF_KNN_KEY], dtype=np.float64)
            profile_arrays = {key: data[key] for key in data.files}
            if GENRES_JSON_KEY in profile_arrays:
                profile_arrays["genres"] = decode_json_string_array(profile_arrays[GENRES_JSON_KEY])
            self._calibration = TasteCalibration.from_profile_arrays(profile_arrays)

        logger.info(
            "Loaded taste profile %s: %d exemplars (%d negative), dino=%s, calibration=%s",
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

    @property
    def metadata(self) -> list[dict | None]:
        self._ensure_loaded()
        return list(self._metadata)

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
        self._embedding_weights = np.concatenate((self._embedding_weights, np.ones(1)))
        self._metadata.append(metadata)
        self._centroid = _compute_centroid(self._embeddings, self._embedding_weights)

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
            self._embedding_weights,
            self._neg_embeddings,
            self._neg_embedding_weights,
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
            self._embedding_weights,
            self._neg_dino_embeddings,
            self._neg_embedding_weights,
            k,
        )

    @staticmethod
    def _contrastive_knn(
        vector: np.ndarray,
        positives: np.ndarray,
        positive_weights: np.ndarray | None,
        negatives: np.ndarray | None,
        negative_weights: np.ndarray | None,
        k: int,
    ) -> float:
        positive = weighted_topk_mean(positives @ vector, k, evidence_weights=positive_weights)
        if negatives is None or pipeline_settings.TASTE_NEG_WEIGHT <= 0:
            return positive
        negative = weighted_topk_mean(
            negatives @ vector,
            min(k, int(negatives.shape[0])),
            evidence_weights=negative_weights,
        )
        penalty = pipeline_settings.TASTE_NEG_WEIGHT * max(0.0, negative - positive)
        return positive - penalty

    def get_all(self) -> tuple[np.ndarray, list[dict | None]]:
        self._ensure_loaded()
        return self._embeddings.copy(), list(self._metadata)  # type: ignore[union-attr]


def _profile_weights(values: np.ndarray | None, count: int, *, label: str) -> np.ndarray:
    """Load native frozen weights while retaining compatibility with legacy profiles."""
    weights = (
        np.ones(count, dtype=np.float32) if values is None else np.asarray(values, dtype=np.float32)
    )
    if weights.shape != (count,):
        raise RuntimeError(f"{label} taste evidence weights do not match embeddings")
    if not np.all(np.isfinite(weights)) or np.any(weights <= 0) or np.any(weights > 1):
        raise RuntimeError(f"{label} taste evidence weights must be finite and in (0, 1]")
    return weights


def _compute_centroid(vectors: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    centroid = (
        vectors.mean(axis=0) if weights is None else np.average(vectors, axis=0, weights=weights)
    )
    norm = float(np.linalg.norm(centroid))
    return (centroid / norm if norm > 1e-10 else centroid).astype(np.float32)
