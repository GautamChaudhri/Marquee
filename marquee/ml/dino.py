"""DINOv2-S/14 embeddings — the second style opinion for taste k-NN.

CLIP embeddings are dominated by semantic content; DINOv2's self-supervised
features encode texture, medium, and rendering style (painterly vs
airbrushed vs photo-composite) far better — exactly the axis the design doc
(04 §9) names as the upgrade path for style discrimination. The pipeline
uses it as a second k-NN against DINOv2 embeddings of the same taste
exemplars (``dino_knn``), never as a CLIP replacement: the aesthetic head
and zero-shot axes stay on CLIP.

Activation is hardware-tiered: ``DINO_ENABLED=auto`` turns it on for GPU
tiers (cuda / openvino-gpu / coreml) and off for CPU-only hosts, where the
extra ~200-300ms/poster is not worth it. The decision is logged every run.

Export the ONNX model once (needs torch, downloads ~88MB from Meta):

    python -m marquee.ml.dino
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.hardware import (
    create_onnx_session,
    detect_hardware,
    effective_clip_batch_size,
)

logger = logging.getLogger(__name__)

DINO_INPUT_SIZE = 224  # 16x16 patches of 14px
DINO_EMBED_DIM = 384  # ViT-S/14
# ImageNet statistics — DINOv2's training preprocessing, NOT CLIP's.
_DINO_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_DINO_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_GPU_TIERS = {"cuda", "openvino-gpu", "coreml"}


def dino_active(*, log: bool = False) -> bool:
    """Resolve the DINO_ENABLED auto/on/off switch against the hardware tier."""
    setting = pipeline_settings.DINO_ENABLED
    tier = detect_hardware().tier
    if setting == "on":
        active, why = True, "forced on"
    elif setting == "off":
        active, why = False, "forced off"
    else:
        active = tier in _GPU_TIERS
        why = f"auto -> {'on' if active else 'off'} (tier={tier})"
    if log:
        logger.info(
            "DINO | enabled=%s | %s | model=%s",
            active,
            why,
            pipeline_settings.DINO_MODEL_PATH.name,
        )
    return active


def preprocess_dino(image: Image.Image) -> np.ndarray:
    """RGB -> (1, 3, 224, 224) float32 with ImageNet normalization."""
    image = image.convert("RGB").resize(
        (DINO_INPUT_SIZE, DINO_INPUT_SIZE), Image.LANCZOS
    )
    arr = np.asarray(image, dtype=np.float32) / 255.0
    arr = (arr - _DINO_MEAN) / _DINO_STD
    return arr.transpose(2, 0, 1)[np.newaxis]


class DinoImageEncoder:
    """Lazily loaded DINOv2 encoder mirroring CLIPImageEncoder's interface."""

    embedding_dim = DINO_EMBED_DIM

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        execution_provider: str | None = None,
        session: ort.InferenceSession | None = None,
    ):
        self._model_path = Path(model_path or pipeline_settings.DINO_MODEL_PATH)
        self._execution_provider = execution_provider
        self._session = session
        self.model_name = self._model_path.stem

    @property
    def available(self) -> bool:
        return self._session is not None or self._model_path.exists()

    @property
    def session(self) -> ort.InferenceSession:
        if self._session is None:
            if not self._model_path.exists():
                raise FileNotFoundError(
                    f"DINOv2 ONNX model not found: {self._model_path}. "
                    "Export it once with `python -m marquee.ml.dino`."
                )
            self._session = create_onnx_session(
                self._model_path,
                execution_provider=self._execution_provider,
            )
        return self._session

    @property
    def supports_batching(self) -> bool:
        first_dim = self.session.get_inputs()[0].shape[0]
        return not isinstance(first_dim, int) or first_dim > 1

    def encode(self, image_or_pixels: Image.Image | np.ndarray) -> np.ndarray:
        if isinstance(image_or_pixels, Image.Image):
            pixels = preprocess_dino(image_or_pixels)
        else:
            pixels = image_or_pixels
        return self._run(pixels.astype(np.float32))[0]

    def encode_batch(self, images: list[Image.Image | np.ndarray]) -> np.ndarray:
        if not images:
            return np.empty((0, self.embedding_dim), dtype=np.float32)
        pixels = np.concatenate(
            [
                preprocess_dino(item) if isinstance(item, Image.Image) else item
                for item in images
            ],
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
            raise RuntimeError("DINOv2 produced a zero-length embedding")
        return (vectors / norms).astype(np.float32)


# ---------------------------------------------------------------------------
# One-time ONNX export (torch required; downloads weights from Meta)
# ---------------------------------------------------------------------------


def export_dino_onnx(output: Path | None = None) -> Path:
    import torch

    output_path = Path(output or pipeline_settings.DINO_MODEL_PATH)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("[INFO] Loading dinov2_vits14 via torch.hub (downloads ~88MB once) ...")
    model = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14")
    model = model.cpu().eval()

    class _CLSWrapper(torch.nn.Module):
        """Pin the signature to pixel_values only — the hub model's optional
        ``masks`` argument otherwise leaks into the ONNX graph as an input."""

        def __init__(self, backbone):
            super().__init__()
            self.backbone = backbone

        def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
            return self.backbone(pixel_values)

    wrapper = _CLSWrapper(model).cpu().eval()

    print(f"[INFO] Exporting DINOv2-S/14 to {output_path} ...")
    torch.onnx.export(
        wrapper,
        torch.zeros(1, 3, DINO_INPUT_SIZE, DINO_INPUT_SIZE, dtype=torch.float32),
        str(output_path),
        input_names=["pixel_values"],
        output_names=["embedding"],
        dynamic_axes={"pixel_values": {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=17,
        do_constant_folding=True,
        dynamo=False,
    )
    print(f"[INFO] Exported {output_path.stat().st_size / 1024 / 1024:.1f} MB")

    session = ort.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
    out = session.run(
        None,
        {"pixel_values": np.zeros((2, 3, DINO_INPUT_SIZE, DINO_INPUT_SIZE), np.float32)},
    )[0]
    if out.shape != (2, DINO_EMBED_DIM):
        raise RuntimeError(f"Unexpected DINOv2 output shape: {out.shape}")
    print(f"[INFO] ONNX verification passed: (2, 3, 224, 224) -> {out.shape}")
    return output_path


if __name__ == "__main__":
    export_dino_onnx()
