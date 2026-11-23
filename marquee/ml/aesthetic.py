"""LAION v1 B/32 linear aesthetic predictor.

The head is a single Linear(512, 1) — 513 floats. At runtime it is loaded
from a numpy ``.npz`` sidecar so PyTorch is NOT a runtime dependency (a
multi-gigabyte install for one dot product). The first load of a ``.pth``
converts it to the sidecar automatically when torch is available; fresh
deployments can ship only the ``.npz``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from marquee.core.pipeline_config import pipeline_settings

logger = logging.getLogger(__name__)


def convert_pth_to_npz(pth_path: Path, npz_path: Path) -> None:
    """One-time conversion of the LAION torch checkpoint to numpy."""
    import torch

    state = torch.load(pth_path, map_location="cpu", weights_only=True)
    weight = state["weight"].detach().cpu().numpy().reshape(512).astype(np.float32)
    bias = float(state["bias"].detach().cpu().numpy().reshape(-1)[0])
    np.savez(npz_path, weight=weight, bias=np.float32(bias))
    logger.info("Converted aesthetic head %s -> %s", pth_path.name, npz_path.name)


class AestheticPredictor:
    """Apply the LAION B/32 linear head to an L2-normalized CLIP embedding."""

    model_name = "clip-vit-b-32"

    def __init__(self, model_path: str | Path | None = None):
        self.model_path = Path(model_path or pipeline_settings.AESTHETIC_MODEL_PATH)
        self._weight: np.ndarray | None = None
        self._bias: float | None = None

    def _npz_path(self) -> Path:
        if self.model_path.suffix == ".npz":
            return self.model_path
        return self.model_path.with_suffix(".npz")

    def load(self) -> None:
        if self._weight is not None:
            return

        npz_path = self._npz_path()
        if not npz_path.exists():
            if not self.model_path.exists():
                raise FileNotFoundError(
                    f"LAION aesthetic head not found: {self.model_path} "
                    f"(nor numpy sidecar {npz_path.name})"
                )
            try:
                convert_pth_to_npz(self.model_path, npz_path)
            except ImportError as exc:
                raise RuntimeError(
                    f"Aesthetic head {self.model_path.name} is a torch checkpoint and "
                    f"PyTorch is not installed. Convert it once on a machine with "
                    f"torch: python -m marquee.ml.aesthetic"
                ) from exc

        with np.load(npz_path) as data:
            self._weight = np.asarray(data["weight"], dtype=np.float32).reshape(512)
            self._bias = float(np.asarray(data["bias"]))

    def score(self, embedding: np.ndarray) -> float:
        self.load()
        vector = np.asarray(embedding, dtype=np.float32).reshape(512)
        norm = float(np.linalg.norm(vector))
        if norm > 1e-10:
            vector = vector / norm
        return float(np.dot(self._weight, vector) + self._bias)  # type: ignore[arg-type]

    def score_batch(self, embeddings: np.ndarray) -> np.ndarray:
        """Score (N, 512) embeddings in one matmul."""
        self.load()
        matrix = np.asarray(embeddings, dtype=np.float32).reshape(-1, 512)
        norms = np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-10)
        return (matrix / norms) @ self._weight + self._bias  # type: ignore[operator]


def main() -> None:
    """CLI: convert the configured .pth head to its .npz sidecar."""
    predictor = AestheticPredictor()
    predictor.load()
    print(f"[INFO] Aesthetic head ready: {predictor._npz_path()}")


if __name__ == "__main__":
    main()
