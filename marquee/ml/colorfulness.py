"""Title-region colorfulness feature."""

from __future__ import annotations

import cv2
import numpy as np

from marquee.pipeline.types import BoundingBox


def hasler_susstrunk(image_rgb: np.ndarray) -> float:
    """Return the Hasler-Susstrunk colorfulness metric for an RGB image."""
    if image_rgb.size == 0:
        return 0.0
    rgb = image_rgb.astype(np.float32)
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    rg = red - green
    yb = 0.5 * (red + green) - blue
    return float(
        np.sqrt(np.std(rg) ** 2 + np.std(yb) ** 2)
        + 0.3 * np.sqrt(np.mean(rg) ** 2 + np.mean(yb) ** 2)
    )


def title_colorfulness(image_bgr: np.ndarray, bbox: BoundingBox | None) -> float:
    """Measure colorfulness inside an OCR title bounding box."""
    if bbox is None:
        return 0.0
    height, width = image_bgr.shape[:2]
    xs = [point[0] for point in bbox]
    ys = [point[1] for point in bbox]
    x1 = max(0, min(width, int(np.floor(min(xs)))))
    y1 = max(0, min(height, int(np.floor(min(ys)))))
    x2 = max(0, min(width, int(np.ceil(max(xs)))))
    y2 = max(0, min(height, int(np.ceil(max(ys)))))
    if x2 <= x1 or y2 <= y1:
        return 0.0
    crop_rgb = cv2.cvtColor(image_bgr[y1:y2, x1:x2], cv2.COLOR_BGR2RGB)
    return hasler_susstrunk(crop_rgb)
