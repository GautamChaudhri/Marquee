"""One-time export of OpenAI CLIP ViT-B/32 to ONNX."""

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
        opset_version=14,
        do_constant_folding=True,
        dynamo=False,
    )
    print(f"[INFO] Exported {output_path.stat().st_size / 1024 / 1024:.1f} MB")


def verify_export(model_path: Path) -> None:
    import onnx
    import onnxruntime as ort

    onnx.checker.check_model(str(model_path))
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    output = session.run(
        None,
        {input_name: np.zeros((1, 3, 224, 224), dtype=np.float32)},
    )[0]
    if output.shape != (1, 512):
        raise RuntimeError(f"Unexpected CLIP output shape: {output.shape}")
    print("[INFO] ONNX verification passed: (1, 3, 224, 224) -> (1, 512)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=pipeline_settings.CLIP_MODEL_PATH,
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.output.exists() and not args.force:
        print(f"[INFO] Model already exists: {args.output}")
    else:
        export_vision_onnx(args.output)
    verify_export(args.output)


if __name__ == "__main__":
    main()
