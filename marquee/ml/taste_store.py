"""Taste store — abstraction over poster embedding storage.

Defines the interface for storing and querying poster embeddings for taste
matching. Two implementations:

  - ``NumpyTasteStore`` — .npz-backed, centroid-based scoring (current).
  - ``ChromaTasteStore`` — ChromaDB-backed, k-NN queries, incremental adds
    (future — for the "liked posters" approval feature).

The ``TasteScorer`` depends on the abstract interface, not any specific
backend — plug in either implementation without changing pipeline code.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class TasteStore(ABC):
    """Abstract storage for poster embeddings and taste queries.

    Implementations must provide:
      - ``centroid_emb`` / ``centroid_color`` — the current taste centroids
      - ``add()`` — append a new poster's embedding and colour histogram
      - ``size`` — how many posters are stored
      - ``query_similar()`` — find nearest neighbours (k-NN)
      - ``get_all()`` — return all stored data (for diagnostics/training)
    """

    @property
    @abstractmethod
    def centroid_emb(self) -> np.ndarray:
        """(512,) L2-normalised CLIP embedding centroid."""
        ...

    @property
    @abstractmethod
    def centroid_color(self) -> np.ndarray:
        """(48,) L2-normalised LAB colour histogram centroid."""
        ...

    @property
    @abstractmethod
    def size(self) -> int:
        """Number of posters stored."""
        ...

    @abstractmethod
    def add(
        self,
        embedding: np.ndarray,
        color_hist: np.ndarray,
        *,
        metadata: dict | None = None,
    ) -> None:
        """Add one poster's embedding and colour histogram to the store.

        The centroid is recomputed after each add so ``query_similar()``
        always reflects the full stored set.

        Args:
            embedding: (512,) L2-normalised CLIP embedding.
            color_hist: (48,) L2-normalised LAB colour histogram.
            metadata: Optional dict (filename, media_type, etc.) for
                future filtering queries.
        """
        ...

    @abstractmethod
    def query_similar(
        self,
        embedding: np.ndarray,
        k: int = 5,
    ) -> list[tuple[float, dict | None]]:
        """Return the k nearest stored posters by cosine similarity.

        Returns a list of (score, metadata) tuples sorted most-similar first.
        """
        ...

    @abstractmethod
    def get_all(self) -> tuple[np.ndarray, np.ndarray, list[dict | None]]:
        """Return all stored data as (embeddings, color_hists, metadata_list).

        Used for diagnostics and retraining.
        """
        ...


# ---------------------------------------------------------------------------
# NumpyTasteStore (.npz-backed)
# ---------------------------------------------------------------------------


class NumpyTasteStore(TasteStore):
    """Taste store backed by a .npz file and numpy arrays.

    Loads from a .npz file produced by ``taste_trainer.py``.  Supports
    incremental adds by appending to in-memory arrays and recomputing the
    centroid (O(N) per add — acceptable for personal library scale).

    This is the current implementation.  Swap to ``ChromaTasteStore`` when
    the incremental approval feature is built.
    """

    def __init__(self, profile_path: str | Path | None = None):
        self._profile_path = Path(
            profile_path or (Path(__file__).parent / "taste_profile.npz")
        )
        self._embeddings: np.ndarray | None = None
        self._color_hists: np.ndarray | None = None
        self._metadata: list[dict | None] = []
        self._centroid_emb: np.ndarray | None = None
        self._centroid_color: np.ndarray | None = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Lazy load
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        if not self._profile_path.exists():
            logger.info(
                "Taste profile not found at %s — starting empty.",
                self._profile_path,
            )
            self._embeddings = np.empty((0, 512), dtype=np.float32)
            self._color_hists = np.empty((0, 48), dtype=np.float32)
            self._metadata = []
            self._centroid_emb = np.zeros(512, dtype=np.float32)
            self._centroid_color = np.zeros(48, dtype=np.float32)
            self._loaded = True
            return

        data = np.load(str(self._profile_path), allow_pickle=True)

        self._centroid_emb = data["centroid_emb"]
        self._centroid_color = data["centroid_color"]

        self._embeddings = data.get("embeddings")
        self._color_hists = data.get("color_hists")

        poster_names = data.get("poster_names", [])
        self._metadata = [
            {"filename": str(n)} if n is not None else None
            for n in poster_names
        ]

        if self._embeddings is None:
            self._embeddings = np.empty((0, 512), dtype=np.float32)
        if self._color_hists is None:
            self._color_hists = np.empty((0, 48), dtype=np.float32)

        self._loaded = True
        logger.info(
            "Loaded taste profile: %s (%d posters)",
            self._profile_path, len(self._metadata),
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def centroid_emb(self) -> np.ndarray:
        self._ensure_loaded()
        return self._centroid_emb  # type: ignore[return-value]

    @property
    def centroid_color(self) -> np.ndarray:
        self._ensure_loaded()
        return self._centroid_color  # type: ignore[return-value]

    @property
    def size(self) -> int:
        self._ensure_loaded()
        return len(self._metadata)

    # ------------------------------------------------------------------
    # Add
    # ------------------------------------------------------------------

    def add(
        self,
        embedding: np.ndarray,
        color_hist: np.ndarray,
        *,
        metadata: dict | None = None,
    ) -> None:
        self._ensure_loaded()

        emb = embedding.astype(np.float32).reshape(1, -1)
        col = color_hist.astype(np.float32).reshape(1, -1)

        if self._embeddings is not None and self._embeddings.shape[0] > 0:
            self._embeddings = np.concatenate([self._embeddings, emb], axis=0)
            self._color_hists = np.concatenate([self._color_hists, col], axis=0)
        else:
            self._embeddings = emb
            self._color_hists = col

        self._metadata.append(metadata)

        # Recompute centroids
        self._centroid_emb = _compute_centroid(self._embeddings)
        self._centroid_color = _compute_centroid(self._color_hists)

        logger.info(
            "Added poster to taste store — %d total", self.size,
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query_similar(
        self,
        embedding: np.ndarray,
        k: int = 5,
    ) -> list[tuple[float, dict | None]]:
        self._ensure_loaded()

        if self._embeddings is None or self._embeddings.shape[0] == 0:
            return []

        # Cosine similarity to all stored embeddings
        sims = np.dot(self._embeddings, embedding)  # (N,)
        top_k = np.argsort(sims)[::-1][:k]

        results = []
        for idx in top_k:
            score = float(sims[idx])
            meta = self._metadata[idx] if idx < len(self._metadata) else None
            results.append((score, meta))

        return results

    # ------------------------------------------------------------------
    # Get all
    # ------------------------------------------------------------------

    def get_all(self) -> tuple[np.ndarray, np.ndarray, list[dict | None]]:
        self._ensure_loaded()
        return (
            self._embeddings,  # type: ignore[return-value]
            self._color_hists,  # type: ignore[return-value]
            list(self._metadata),
        )


# ---------------------------------------------------------------------------
# Centroid helper
# ---------------------------------------------------------------------------


def _compute_centroid(vectors: np.ndarray) -> np.ndarray:
    """Compute L2-normalised mean of a set of normalised vectors."""
    if vectors.shape[0] == 0:
        return np.zeros(vectors.shape[1], dtype=np.float32)
    mean = vectors.mean(axis=0)
    norm = np.linalg.norm(mean)
    if norm > 1e-10:
        mean = mean / norm
    return mean.astype(np.float32)
