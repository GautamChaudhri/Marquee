"""Taste exemplar storage backed by model-tagged NumPy embeddings.

Two upgrades over the plain top-k mean:

  - **Similarity-weighted k-NN** (``KNN_WEIGHTING=softmax``): the k nearest
    exemplars are combined with softmax weights so the closest neighbours
    dominate. This sharpens multimodal taste — a candidate sitting on top of
    your horror cluster is not diluted by 9 weaker neighbours from other
    clusters — without the noise of k=1.
  - **Negative exemplars** (optional ``neg_embeddings`` in the profile): a
    second store of posters you explicitly dislike (floating heads, fan junk).
    A candidate is penalized only when it is *closer to the disliked set than
    to the liked set*: ``score = pos - w * max(0, neg - pos)``. This leaves
    ordinary candidates untouched (no global shift, gate thresholds stay
    valid) while pushing look-alikes of known junk down hard.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from marquee.core.pipeline_config import pipeline_settings

logger = logging.getLogger(__name__)


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
        with np.load(self.profile_path, allow_pickle=True) as data:
            stored_model = str(np.asarray(data["model_name"]).item())
            if stored_model != self.expected_model_name:
                raise RuntimeError(
                    f"Taste profile model mismatch: artifact={stored_model!r}, "
                    f"configured={self.expected_model_name!r}. Rebuild the profile."
                )
            self._embeddings = np.asarray(data["embeddings"], dtype=np.float32)
            self._centroid = np.asarray(data["centroid_emb"], dtype=np.float32)
            names = data["poster_names"].tolist()
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
        logger.info(
            "Loaded %d taste exemplars (%d negative) from %s",
            self.size,
            self.negative_size,
            self.profile_path,
        )

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

    def add(self, embedding: np.ndarray, *, metadata: dict | None = None) -> None:
        self._ensure_loaded()
        vector = np.asarray(embedding, dtype=np.float32).reshape(1, 512)
        vector /= np.maximum(np.linalg.norm(vector, axis=1, keepdims=True), 1e-10)
        self._embeddings = np.concatenate((self._embeddings, vector), axis=0)  # type: ignore[arg-type]
        self._metadata.append(metadata)
        self._centroid = _compute_centroid(self._embeddings)

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
        vector = np.asarray(embedding, dtype=np.float32).reshape(512)
        positive = weighted_topk_mean(self._embeddings @ vector, k)  # type: ignore[operator]
        if self._neg_embeddings is None or pipeline_settings.TASTE_NEG_WEIGHT <= 0:
            return positive
        negative = weighted_topk_mean(
            self._neg_embeddings @ vector,
            min(k, self.negative_size),
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
