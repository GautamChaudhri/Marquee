"""One-time export of OpenAI CLIP ViT-B/32 to ONNX.

Exports with a dynamic batch axis so the pipeline can batch candidates
through one session.run (the big GPU win). ``--quantize`` additionally emits
a dynamically-quantized INT8 model (~4x smaller, ~2-3x faster on CPU-only
hosts like an Intel N150). INT8 embeddings live in a slightly different
space: set ``AI_MODEL=clip-vit-b-32-int8`` and rebuild the taste profile
with that model before using it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from marquee.core.pipeline_config import pipeline_settings

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


def export_vision_onnx(output_path: Path) -> None:
    import torch
    from transformers import CLIPModel

    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] Loading {_HF_MODEL_ID} ...")
    model = CLIPModel.from_pretrained(_HF_MODEL_ID).cpu().eval()
    wrapper_class = _make_vision_wrapper()
    wrapper = wrapper_class(model.vision_model, model.visual_projection).cpu().eval()

    print(f"[INFO] Exporting CLIP B/32 to {output_path} ...")
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
    print(f"[INFO] Exported {output_path.stat().st_size / 1024 / 1024:.1f} MB")


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
        "       python -m marquee.ml.taste_trainer"
    )


def verify_export(model_path: Path) -> None:
    import onnx
    import onnxruntime as ort

    onnx.checker.check_model(str(model_path))
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    first_dim = session.get_inputs()[0].shape[0]
    batch = 2 if not isinstance(first_dim, int) else first_dim
    output = session.run(
        None,
        {input_name: np.zeros((batch, 3, 224, 224), dtype=np.float32)},
    )[0]
    if output.shape != (batch, 512):
        raise RuntimeError(f"Unexpected CLIP output shape: {output.shape}")
    dynamic = "dynamic batch" if not isinstance(first_dim, int) else f"fixed batch {first_dim}"
    print(f"[INFO] ONNX verification passed: ({batch}, 3, 224, 224) -> ({batch}, 512) [{dynamic}]")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=pipeline_settings.CLIP_MODEL_PATH,
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--quantize",
        action="store_true",
        help="Also emit an INT8-quantized model alongside the FP32 export",
    )
    args = parser.parse_args()

    if args.output.exists() and not args.force:
        print(f"[INFO] Model already exists: {args.output}")
    else:
        export_vision_onnx(args.output)
    verify_export(args.output)

    if args.quantize:
        int8_path = args.output.with_name(f"{args.output.stem}-int8.onnx")
        quantize_int8(args.output, int8_path)
        verify_export(int8_path)


if __name__ == "__main__":
    main()
