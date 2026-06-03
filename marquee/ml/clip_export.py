"""One-time export: CLIP ViT-B/16 → ONNX + pre-encode negative prompts.

After this script runs, torch is no longer needed for inference — only
onnxruntime is used.  The negative prompt text embeddings are saved as .npy
alongside the ONNX model so that inference-time code never needs torch.

Usage:
    python -m marquee.ml.clip_export [--output PATH] [--force]

Requires (one-time only):
    torch, transformers
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_MODELS_DIR = Path(__file__).parent / "models"
_DEFAULT_MODEL_PATH = _MODELS_DIR / "clip_vit_b16.onnx"
_DEFAULT_PROMPTS_PATH = _MODELS_DIR / "negative_prompts.npy"
_HF_MODEL_ID = "openai/clip-vit-base-patch16"


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------


def _check_deps() -> None:
    missing = []
    for pkg in ("torch", "transformers"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[ERROR] Missing packages: {' '.join(missing)}")
        print(f"        Install with: pip install {' '.join(missing)}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Vision model wrapper for ONNX export
# ---------------------------------------------------------------------------


def _make_vision_wrapper() -> type:
    """Return a wrapper that exposes the CLIP vision pipeline as a single
    traceable ``forward()``.

    CLIP's vision_model returns a BaseModelOutputWithPooling, which the
    TorchScript exporter cannot trace because it contains non-tensor fields.
    This wrapper extracts only the pooler_output and passes it through the
    visual projection head.
    """
    import torch

    class CLIPVisionWrapper(torch.nn.Module):
        def __init__(self, vision_model, visual_projection):
            super().__init__()
            self.vision_model = vision_model
            self.visual_projection = visual_projection

        def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
            out = self.vision_model(pixel_values)
            pooled = out.pooler_output          # (B, 768)
            emb = self.visual_projection(pooled)  # (B, 512)
            return emb

    return CLIPVisionWrapper


# ---------------------------------------------------------------------------
# Export vision model to ONNX
# ---------------------------------------------------------------------------


def export_vision_onnx(output_path: Path) -> None:
    """Load CLIP ViT-B/16 from HuggingFace and export the vision pipeline."""
    import torch
    from transformers import CLIPModel

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Load model ---
    print(f"[INFO] Loading {_HF_MODEL_ID} from HuggingFace ...")
    print("       (Weights will be downloaded on first run, ~600 MB)")
    model = CLIPModel.from_pretrained(_HF_MODEL_ID)
    model = model.cpu().eval()

    # --- Build wrapper ---
    WrapperClass = _make_vision_wrapper()
    wrapper = WrapperClass(model.vision_model, model.visual_projection)
    wrapper = wrapper.cpu().eval()

    dummy = torch.zeros(1, 3, 224, 224, dtype=torch.float32)

    # --- Export ---
    print(f"[INFO] Exporting vision model to ONNX (opset 14, static 224×224) ...")
    print(f"       Output: {output_path}")

    torch.onnx.export(
        wrapper,
        dummy,
        str(output_path),
        input_names=["pixel_values"],
        output_names=["embedding"],
        opset_version=14,
        do_constant_folding=True,
        dynamo=False,
    )

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"[INFO] Export complete: {output_path} ({size_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# Encode negative prompts (one-time, text encoder)
# ---------------------------------------------------------------------------


def encode_negative_prompts(
    prompts: list[str],
    output_path: Path,
) -> np.ndarray:
    """Encode negative prompt texts with CLIP text encoder and save as .npy.

    Returns the prompt embeddings for inference-time use.
    """
    import torch
    from transformers import CLIPModel, CLIPProcessor

    output_path.parent.mkdir(parents=True, exist_ok=True)

    model = CLIPModel.from_pretrained(_HF_MODEL_ID)
    processor = CLIPProcessor.from_pretrained(_HF_MODEL_ID)
    model = model.cpu().eval()

    print(f"[INFO] Encoding {len(prompts)} negative prompts ...")
    embeddings = []
    for prompt in prompts:
        inputs = processor(text=prompt, return_tensors="pt", padding=True)
        with torch.no_grad():
            # Get pooled output from text_model, then project to 512-dim
            text_out = model.text_model(**inputs)
            pooled = text_out.pooler_output   # (1, 512) — EOS token embedding
            vec = model.text_projection(pooled)[0].cpu().numpy()  # (512,)
        # L2-normalise for cosine similarity
        norm = np.linalg.norm(vec)
        if norm > 1e-10:
            vec = vec / norm
        embeddings.append(vec.astype(np.float32))
        print(f"       \"{prompt[:60]}...\"  →  shape={vec.shape}")

    emb_array = np.stack(embeddings, axis=0)  # (N, 512)
    np.save(str(output_path), emb_array)
    print(f"[INFO] Saved negative prompt embeddings: {output_path}")
    return emb_array


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def verify_export(model_path: Path) -> None:
    """Verify the ONNX model with ONNX Runtime."""
    import onnx
    import onnxruntime as ort

    print("[INFO] Verifying ONNX model ...")

    try:
        onnx.checker.check_model(str(model_path))
    except Exception as exc:
        print(f"[WARNING] onnx.checker skipped ({type(exc).__name__}: {exc})")

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    dummy = np.zeros((1, 3, 224, 224), dtype=np.float32)
    input_name = session.get_inputs()[0].name
    outputs = session.run(None, {input_name: dummy})

    shape = outputs[0].shape
    expected = (1, 512)
    if shape != expected:
        print(f"[ERROR] Unexpected output shape: {shape}, expected {expected}")
        sys.exit(1)

    print(f"[INFO] Input name:     {input_name!r}")
    print(f"[INFO] Output shape:   {shape}  (expected: {expected})")
    print("[INFO] Export verified successfully.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    from marquee.config import settings

    parser = argparse.ArgumentParser(
        description="Export CLIP ViT-B/16 vision model to ONNX and encode "
        "negative prompts for zero-shot filtering."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_MODEL_PATH,
        help=f"Destination for the ONNX file (default: {_DEFAULT_MODEL_PATH})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-export even if the output file already exists.",
    )
    args = parser.parse_args()

    _check_deps()

    model_path = args.output
    prompts_path = model_path.parent / "negative_prompts.npy"

    if model_path.exists() and not args.force:
        print(f"[INFO] Model already exists: {model_path}")
        print("       Use --force to re-export.")
        verify_export(model_path)
    else:
        export_vision_onnx(model_path)
        verify_export(model_path)

    # Encode negative prompts (always regenerate if model was just re-exported,
    # or if the prompts file doesn't exist)
    if not prompts_path.exists() or args.force:
        encode_negative_prompts(settings.NEGATIVE_PROMPTS, prompts_path)
    else:
        print(f"[INFO] Negative prompts already encoded: {prompts_path}")


if __name__ == "__main__":
    main()
