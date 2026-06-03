"""LAB color histogram extraction for taste profiling.

Uses OpenCV to compute a 48-dim L2-normalised histogram over CIE LAB colour
space.  LAB is perceptually uniform — equal distances correspond to equal
perceived colour differences — making it a better match signal than RGB or
HSV for aesthetic taste modeling.

Process:
  1. Read image with cv2 (BGR) → resize to 256×256 for consistent sampling
  2. Convert to LAB
  3. ``calcHist`` for each of 3 channels, 16 bins, range [0, 256]
     (upper bound is exclusive — [0, 255] silently misses pixel value 255)
  4. Normalise each channel's histogram to sum=1 independently
  5. Concatenate → 48-dim, then L2-normalise for cosine similarity

Returns shape (48,) float32.

Salvaged from the old project's ``experiments/profiling/profiling_utils.py``.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
COLOR_DIM: int = 48        # 3 LAB channels × 16 bins each
COLOR_BINS: int = 16
COLOR_RESIZE: int = 256    # Standardise before histogram computation


def extract_color_histogram(image_path: str | Path) -> np.ndarray:
    """Extract a 48-dim L2-normalised LAB colour histogram.

    Args:
        image_path: Path to an image file readable by OpenCV.

    Returns:
        float32 ndarray of shape (48,).

    Raises:
        ValueError: If OpenCV cannot decode the file.
    """
    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"cv2 could not read image: {image_path}")

    img = cv2.resize(
        img, (COLOR_RESIZE, COLOR_RESIZE), interpolation=cv2.INTER_AREA
    )
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)

    # LAB range in OpenCV 8-bit: all channels [0, 255]
    # (L encodes 0–100, A/B shifted)
    channel_range = [0, 256]  # exclusive upper bound
    hists = []
    for ch in range(3):
        h = cv2.calcHist([lab], [ch], None, [COLOR_BINS], channel_range)
        h = h.flatten().astype(np.float32)
        total = h.sum()
        if total > 0:
            h /= total  # normalise to sum=1 per channel
        hists.append(h)

    hist = np.concatenate(hists)  # (48,)

    # L2-normalise for cosine similarity
    norm = np.linalg.norm(hist)
    if norm > 1e-10:
        hist = hist / norm

    return hist.astype(np.float32)
