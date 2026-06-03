"""CLIP image encoder via ONNX Runtime.

Platform-agnostic inference — uses the fastest available execution provider:
  - macOS Apple Silicon → CoreMLExecutionProvider (ANE/GPU/CPU)
  - Intel CPU/iGPU    → OpenVINOExecutionProvider  (if installed)
  - NVIDIA GPU         → CUDAExecutionProvider      (if installed)
  - Fallback           → CPUExecutionProvider       (always available)

The ONNX model is produced by ``clip_export.py`` (one-time, requires torch).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import onnxruntime as ort

from marquee.ml.preprocessing import CLIP_INPUT_SIZE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default paths (resolved relative to this module)
# ---------------------------------------------------------------------------
_MODELS_DIR = Path(__file__).parent / "models"


def _choose_providers() -> list[str]:
    """Return the best available execution providers for this platform."""
    available = ort.get_available_providers()
    preferred = []
    for p in ("CoreMLExecutionProvider", "OpenVINOExecutionProvider",
               "CUDAExecutionProvider"):
        if p in available:
            preferred.append(p)
    preferred.append("CPUExecutionProvider")
    return preferred


# ---------------------------------------------------------------------------
# ONNX session factory
# ---------------------------------------------------------------------------


def create_onnx_session(
    model_path: str | Path | None = None,
) -> ort.InferenceSession:
    """Create an ONNX Runtime session for CLIP image encoding.

    Args:
        model_path: Path to the .onnx file.  Defaults to
            ``marquee/ml/models/clip_vit_b16.onnx``.

    Returns:
        An ``ort.InferenceSession`` ready for ``encode()`` calls.

    Raises:
        FileNotFoundError: If the model file does not exist (run
            ``clip_export.py`` first).
    """
    if model_path is None:
        model_path = _MODELS_DIR / "clip_vit_b16.onnx"
    model_path = Path(model_path)

    if not model_path.exists():
        raise FileNotFoundError(
            f"CLIP ONNX model not found: {model_path}\n"
            "Run `python marquee/ml/clip_export.py` first to generate it."
        )

    providers = _choose_providers()
    session = ort.InferenceSession(str(model_path), providers=providers)

    active = session.get_providers()
    logger.info("ONNX session created — providers: %s", active)
    return session


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------


class CLIPImageEncoder:
    """Encode images to 512-dim CLIP embeddings via ONNX Runtime.

    Usage::

        encoder = CLIPImageEncoder()
        emb = encoder.encode(pixel_values)   # pixel_values from preprocess_image()

    The session is created lazily on first ``encode()`` call, so importing
    this module does not require the ONNX file to exist yet.
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        session: ort.InferenceSession | None = None,
    ):
        """Create an encoder.

        Args:
            model_path: Path to .onnx file (default: models/clip_vit_b16.onnx).
            session: Pre-created ``ort.InferenceSession``. If provided,
                ``model_path`` is ignored.
        """
        self._model_path = Path(model_path) if model_path else None
        self._session = session

    @property
    def session(self) -> ort.InferenceSession:
        """The ONNX Runtime session (created lazily)."""
        if self._session is None:
            self._session = create_onnx_session(self._model_path)
        return self._session

    def encode(self, pixel_values: np.ndarray) -> np.ndarray:
        """Run CLIP inference and return a 512-dim L2-normalised embedding.

        Args:
            pixel_values: float32 ndarray of shape (1, 3, 224, 224)
                as produced by ``preprocess_image()``.

        Returns:
            float32 ndarray of shape (512,) — the CLIP image embedding,
            L2-normalised for cosine similarity.
        """
        input_name = self.session.get_inputs()[0].name
        outputs = self.session.run(None, {input_name: pixel_values})
        vec = outputs[0][0]  # (512,)

        # L2-normalise for cosine similarity
        norm = np.linalg.norm(vec)
        if norm > 1e-10:
            vec = vec / norm

        return vec.astype(np.float32)

    @property
    def embedding_dim(self) -> int:
        return 512
