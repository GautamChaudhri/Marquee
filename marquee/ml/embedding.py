"""CLIP image embeddings via ONNX Runtime, batched and EP-portable."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.hardware import (
    choose_execution_providers,  # noqa: F401 - re-exported for compatibility
    effective_clip_batch_size,
)
from marquee.ml.hardware import (
    create_onnx_session as _create_hardware_session,
)
from marquee.ml.preprocessing import preprocess_image

logger = logging.getLogger(__name__)


def create_onnx_session(
    model_path: str | Path | None = None,
    *,
    execution_provider: str | None = None,
) -> ort.InferenceSession:
    path = Path(model_path or pipeline_settings.CLIP_MODEL_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"CLIP ONNX model not found: {path}. Run `python -m marquee.ml.clip_export` first."
        )
    return _create_hardware_session(path, execution_provider=execution_provider)


class CLIPImageEncoder:
    """Lazily loaded CLIP image encoder with dynamic-batch support.

    The model name is the ONNX filename stem (the design mandates the model
    name in the filename), so an int8-quantized export named
    ``clip-vit-b-32-int8.onnx`` is automatically tracked as a distinct
    embedding space.
    """

    embedding_dim = 512

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        execution_provider: str | None = None,
        session: ort.InferenceSession | None = None,
    ):
        self._model_path = Path(model_path or pipeline_settings.CLIP_MODEL_PATH)
        self._execution_provider = execution_provider
        self._session = session
        self.model_name = self._model_path.stem

    @property
    def session(self) -> ort.InferenceSession:
        if self._session is None:
            self._session = create_onnx_session(
                self._model_path,
                execution_provider=self._execution_provider,
            )
        return self._session

    @property
    def supports_batching(self) -> bool:
        """True when the export has a dynamic batch axis (str/None dim)."""
        first_dim = self.session.get_inputs()[0].shape[0]
        return not isinstance(first_dim, int) or first_dim > 1

    def encode(self, image_or_pixels: Image.Image | np.ndarray) -> np.ndarray:
        if isinstance(image_or_pixels, Image.Image):
            pixel_values = preprocess_image(image_or_pixels)
        else:
            pixel_values = image_or_pixels
        return self._run(pixel_values.astype(np.float32))[0]

    def encode_batch(self, images: list[Image.Image | np.ndarray]) -> np.ndarray:
        """Encode many images, batching through the model when it allows.

        Fixed-batch exports (older single-image models) fall back to a loop,
        so callers can always use this entry point. Returns (N, 512)
        L2-normalized embeddings in input order.
        """
        if not images:
            return np.empty((0, self.embedding_dim), dtype=np.float32)

        pixels = np.concatenate(
            [preprocess_image(item) if isinstance(item, Image.Image) else item for item in images],
            axis=0,
        ).astype(np.float32)

        if not self.supports_batching:
            return np.stack([self._run(pixels[i : i + 1])[0] for i in range(len(images))])

        batch_size = max(1, effective_clip_batch_size())
        chunks = [
            self._run(pixels[start : start + batch_size])
            for start in range(0, len(images), batch_size)
        ]
        return np.concatenate(chunks, axis=0)

    def _run(self, pixel_values: np.ndarray) -> np.ndarray:
        input_name = self.session.get_inputs()[0].name
        vectors = self.session.run(None, {input_name: pixel_values})[0]
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if np.any(norms <= 1e-10):
            raise RuntimeError("CLIP produced a zero-length embedding")
        return (vectors / norms).astype(np.float32)
