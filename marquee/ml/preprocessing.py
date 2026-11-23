"""CLIP-specific image preprocessing.

CLIP uses different normalization statistics and resize behaviour than
standard ImageNet models.  This module provides the preprocessing pipeline
that matches HuggingFace's ``CLIPProcessor`` to ensure identical embeddings
whether running via PyTorch (export) or ONNX Runtime (inference).
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# CLIP ViT-B/16 constants
# ---------------------------------------------------------------------------
CLIP_INPUT_SIZE: int = 224
CLIP_MEAN: tuple[float, float, float] = (0.48145466, 0.4578275, 0.40821073)
CLIP_STD: tuple[float, float, float] = (0.26862954, 0.26130258, 0.27577711)


def preprocess_image(image: Image.Image) -> np.ndarray:
    """Resize and normalise a PIL image for CLIP inference.

    Matches HuggingFace ``CLIPProcessor`` behaviour:
      1. Convert to RGB
      2. Resize to 224×224 via LANCZOS (bicubic-equivalent)
      3. Normalise with CLIP mean/std
      4. Convert to (1, 3, 224, 224) float32

    Args:
        image: A PIL image in any mode (RGB, RGBA, P, L).

    Returns:
        float32 ndarray of shape (1, 3, 224, 224) ready for ONNX.
    """
    image = image.convert("RGB")
    image = image.resize((CLIP_INPUT_SIZE, CLIP_INPUT_SIZE), Image.LANCZOS)

    arr = np.array(image, dtype=np.float32) / 255.0  # (224, 224, 3)

    mean = np.array(CLIP_MEAN, dtype=np.float32)
    std = np.array(CLIP_STD, dtype=np.float32)
    arr = (arr - mean) / std  # broadcast over HW

    arr = arr.transpose(2, 0, 1)[np.newaxis]  # (1, 3, 224, 224)
    return arr
