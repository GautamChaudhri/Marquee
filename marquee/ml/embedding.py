"""CLIP ViT-B/32 image embeddings via ONNX Runtime."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.preprocessing import preprocess_image

logger = logging.getLogger(__name__)


def choose_execution_providers(override: str | None = None) -> list[str]:
    """Choose an available provider with a portable CPU fallback."""
    available = ort.get_available_providers()
    requested = override or pipeline_settings.EXECUTION_PROVIDER

    if requested and requested.lower() != "auto":
        if requested not in available:
            raise RuntimeError(
                f"Requested ONNX execution provider {requested!r} is unavailable; "
                f"available providers: {available}"
            )
        return [requested, "CPUExecutionProvider"] if requested != "CPUExecutionProvider" else [
            "CPUExecutionProvider"
        ]

    preferred = (
        "CUDAExecutionProvider",
        "OpenVINOExecutionProvider",
        "CoreMLExecutionProvider",
        "CPUExecutionProvider",
    )
    return [provider for provider in preferred if provider in available]


def create_onnx_session(
    model_path: str | Path | None = None,
    *,
    execution_provider: str | None = None,
) -> ort.InferenceSession:
    path = Path(model_path or pipeline_settings.CLIP_MODEL_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"CLIP B/32 ONNX model not found: {path}. "
            "Run `python -m marquee.ml.clip_export` first."
        )

    providers = choose_execution_providers(execution_provider)
    session = ort.InferenceSession(str(path), providers=providers)
    logger.info("CLIP ONNX providers: %s", session.get_providers())
    return session


class CLIPImageEncoder:
    """Lazily loaded CLIP B/32 image encoder."""

    model_name = "clip-vit-b-32"
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

    @property
    def session(self) -> ort.InferenceSession:
        if self._session is None:
            self._session = create_onnx_session(
                self._model_path,
                execution_provider=self._execution_provider,
            )
        return self._session

    def encode(self, image_or_pixels: Image.Image | np.ndarray) -> np.ndarray:
        if isinstance(image_or_pixels, Image.Image):
            pixel_values = preprocess_image(image_or_pixels)
        else:
            pixel_values = image_or_pixels

        input_name = self.session.get_inputs()[0].name
        vec = self.session.run(None, {input_name: pixel_values.astype(np.float32)})[0][0]
        norm = float(np.linalg.norm(vec))
        if norm <= 1e-10:
            raise RuntimeError("CLIP produced a zero-length embedding")
        return (vec / norm).astype(np.float32)
