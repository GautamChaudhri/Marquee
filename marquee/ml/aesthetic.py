"""LAION v1 B/32 linear aesthetic predictor."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from marquee.core.pipeline_config import pipeline_settings


class AestheticPredictor:
    """Apply the LAION B/32 linear head to an L2-normalized CLIP embedding."""

    model_name = "clip-vit-b-32"

    def __init__(self, model_path: str | Path | None = None):
        self.model_path = Path(model_path or pipeline_settings.AESTHETIC_MODEL_PATH)
        self._weight: np.ndarray | None = None
        self._bias: float | None = None

    def load(self) -> None:
        if self._weight is not None:
            return
        if not self.model_path.exists():
            raise FileNotFoundError(f"LAION aesthetic head not found: {self.model_path}")

        import torch

        state = torch.load(self.model_path, map_location="cpu", weights_only=True)
        self._weight = state["weight"].detach().cpu().numpy().reshape(512).astype(np.float32)
        self._bias = float(state["bias"].detach().cpu().numpy().reshape(-1)[0])

    def score(self, embedding: np.ndarray) -> float:
        self.load()
        vector = np.asarray(embedding, dtype=np.float32).reshape(512)
        norm = float(np.linalg.norm(vector))
        if norm > 1e-10:
            vector = vector / norm
        return float(np.dot(self._weight, vector) + self._bias)  # type: ignore[arg-type]
