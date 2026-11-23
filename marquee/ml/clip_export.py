"""One-time export of OpenAI CLIP ViT-B/32 to ONNX (``marquee models fetch`` calls it).

Exports with a dynamic batch axis so the pipeline can batch candidates
through one session.run (the big GPU win). ``quantize_int8`` makes a
dynamically-quantized INT8 model (~4x smaller, ~2-3x faster on CPU-only
hosts). INT8 embeddings live in a slightly different space: set
``AI_MODEL=clip-vit-b-32-int8`` and rebuild the taste profile with that
model before using it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import torch

logger = logging.getLogger(__name__)

_HF_MODEL_ID = "openai/clip-vit-base-patch32"


def _make_vision_wrapper() -> type:
    import torch

    class CLIPVisionWrapper(torch.nn.Module):
        def __init__(self, vision_model, visual_projection):
            super().__init__()
            self.vision_model = vision_model
            self.visual_projection = visual_projection

        def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
            pooled = self.vision_model(pixel_values).pooler_output
            return self.visual_projection(pooled)

    return CLIPVisionWrapper


def export_vision_onnx(output_path: Path) -> torch.nn.Module:
    """Export CLIP's image tower to ``output_path``; return the PyTorch module exported."""
    import torch
    from transformers import CLIPModel

    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Loading %s ...", _HF_MODEL_ID)
    model = CLIPModel.from_pretrained(_HF_MODEL_ID, dtype=torch.float32).cpu().eval()
    wrapper_class = _make_vision_wrapper()
    wrapper = wrapper_class(model.vision_model, model.visual_projection).cpu().eval()

    logger.info("Exporting CLIP B/32 to %s ...", output_path)
    torch.onnx.export(
        wrapper,
        torch.zeros(1, 3, 224, 224, dtype=torch.float32),
        str(output_path),
        input_names=["pixel_values"],
        output_names=["embedding"],
        dynamic_axes={"pixel_values": {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=14,
        do_constant_folding=True,
        dynamo=False,
    )
    logger.info("Exported %.1f MB", output_path.stat().st_size / 1024 / 1024)
    return wrapper


def quantize_int8(model_path: Path, output_path: Path) -> None:
    """Dynamic INT8 quantization for CPU-only deployments."""
    from onnxruntime.quantization import QuantType, quantize_dynamic

    print(f"[INFO] Quantizing {model_path.name} -> {output_path.name} ...")
    quantize_dynamic(
        str(model_path),
        str(output_path),
        weight_type=QuantType.QInt8,
    )
    print(f"[INFO] Quantized {output_path.stat().st_size / 1024 / 1024:.1f} MB")
    print(
        "[NOTE] INT8 embeddings are a distinct space. Set "
        "AI_MODEL=clip-vit-b-32-int8 and rebuild the taste profile:\n"
        "       marquee taste seed --reuse"
    )
