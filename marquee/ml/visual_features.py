"""Classic-CV visual features: palette, composition, geometry, artifacts.

Every function operates on a width-standardized image (see
``standardize_width``) so values are comparable between pipeline candidates
(w500 downloads) and taste-profile exemplars (arbitrary resolutions resized
to the same width). Resolution-dependent metrics (edge density, noise,
blockiness) would otherwise drift between the two and corrupt calibration.

All features are cheap (a few ms per poster on a weak CPU) and feed the
exemplar-calibrated typicality score — none of them is monotonic
"higher = better" on its own.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from marquee.ml.colorfulness import hasler_susstrunk
from marquee.pipeline.types import BoundingBox

STANDARD_WIDTH = 500


def standardize_width(image_bgr: np.ndarray, width: int = STANDARD_WIDTH) -> np.ndarray:
    """Resize to the standard feature width (keeping aspect) if needed."""
    h, w = image_bgr.shape[:2]
    if w == width:
        return image_bgr
    new_h = max(1, round(h * width / w))
    return cv2.resize(image_bgr, (width, new_h), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------------------
# Palette / mood
# ---------------------------------------------------------------------------


def palette_features(image_bgr: np.ndarray) -> dict[str, float]:
    """Darkness, saturation, hue entropy, global colorfulness, RMS contrast."""
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0

    value = hsv[..., 2].astype(np.float32) / 255.0
    saturation = hsv[..., 1].astype(np.float32) / 255.0

    # Hue entropy over sufficiently saturated+bright pixels only — hue is
    # meaningless noise on near-gray pixels. Monochrome poster -> ~0.
    chromatic = (hsv[..., 1] > 32) & (hsv[..., 2] > 32)
    if chromatic.sum() > 64:
        hist, _ = np.histogram(hsv[..., 0][chromatic], bins=36, range=(0, 180))
        p = hist / hist.sum()
        p = p[p > 0]
        hue_entropy = float(-(p * np.log2(p)).sum() / math.log2(36))  # 0..1
    else:
        hue_entropy = 0.0

    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    return {
        "darkness": float(1.0 - value.mean()),
        "mean_saturation": float(saturation.mean()),
        "hue_entropy": hue_entropy,
        "global_colorfulness": hasler_susstrunk(rgb),
        "contrast_rms": float(gray.std()),
    }


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def composition_features(image_bgr: np.ndarray) -> dict[str, float]:
    """Negative space, edge density, entropy, left-right symmetry."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edge_density = float((edges > 0).mean())

    # Negative space: fraction of 16x16 cells with almost no edge content —
    # the "clean air" minimalist posters are built from.
    cell = 16
    h, w = edges.shape
    cells_h, cells_w = h // cell, w // cell
    if cells_h and cells_w:
        trimmed = edges[: cells_h * cell, : cells_w * cell]
        blocks = trimmed.reshape(cells_h, cell, cells_w, cell)
        cell_edges = (blocks > 0).mean(axis=(1, 3))
        negative_space = float((cell_edges < 0.02).mean())
    else:
        negative_space = 0.0

    hist, _ = np.histogram(gray, bins=64, range=(0, 256))
    p = hist / max(hist.sum(), 1)
    p = p[p > 0]
    visual_entropy = float(-(p * np.log2(p)).sum() / math.log2(64))  # 0..1

    small = cv2.resize(gray, (64, 96), interpolation=cv2.INTER_AREA).astype(np.float32)
    mirrored = small[:, ::-1]
    symmetry = float(1.0 - np.abs(small - mirrored).mean() / 255.0)

    return {
        "edge_density": edge_density,
        "negative_space_frac": negative_space,
        "visual_entropy": visual_entropy,
        "symmetry": symmetry,
    }


# ---------------------------------------------------------------------------
# Quality artifacts (rec 5) — monotonic badness signals, model-free
# ---------------------------------------------------------------------------


def noise_sigma(gray: np.ndarray) -> float:
    """Immerkær's fast noise-variance estimate (sensor/compression noise).

    Convolves with a Laplacian-difference kernel that cancels image
    structure and keeps noise; sigma is recovered from the mean absolute
    response. Robust on natural images; high values on noisy fan scans.
    """
    kernel = np.array(
        [[1, -2, 1], [-2, 4, -2], [1, -2, 1]],
        dtype=np.float32,
    )
    response = cv2.filter2D(gray.astype(np.float32), -1, kernel)
    h, w = gray.shape
    return float(
        math.sqrt(math.pi / 2.0) * np.abs(response).sum() / (6.0 * w * h)
    )


def blockiness(gray: np.ndarray) -> float:
    """JPEG 8x8 block-boundary energy vs in-block energy.

    Heavily recompressed images show stronger discontinuities at multiples
    of 8 than inside blocks; clean sources score near 0.
    """
    g = gray.astype(np.float32)
    col_diff = np.abs(np.diff(g, axis=1))
    row_diff = np.abs(np.diff(g, axis=0))

    # Column differences at indices 7, 15, ... are block boundaries.
    boundary_cols = col_diff[:, 7::8]
    boundary_rows = row_diff[7::8, :]
    mask_cols = np.ones(col_diff.shape[1], dtype=bool)
    mask_cols[7::8] = False
    mask_rows = np.ones(row_diff.shape[0], dtype=bool)
    mask_rows[7::8] = False
    inner = (
        float(col_diff[:, mask_cols].mean()) + float(row_diff[mask_rows, :].mean())
    ) / 2.0
    boundary = (float(boundary_cols.mean()) + float(boundary_rows.mean())) / 2.0
    return float(max(0.0, boundary - inner))


def quality_artifact_features(image_bgr: np.ndarray) -> dict[str, float]:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    return {
        "blockiness": blockiness(gray),
        "noise_sigma": noise_sigma(gray),
    }


# ---------------------------------------------------------------------------
# Geometry derived from data other stages already produce (rec 1 — free)
# ---------------------------------------------------------------------------


def title_geometry(
    bbox: BoundingBox | None,
    image_width: int,
    image_height: int,
) -> dict[str, float]:
    """Title size and placement from the OCR title box. NaN when no title."""
    if bbox is None or image_width <= 0 or image_height <= 0:
        return {
            "title_height_frac": float("nan"),
            "title_y_center": float("nan"),
            "title_centeredness": float("nan"),
        }
    xs = [point[0] for point in bbox]
    ys = [point[1] for point in bbox]
    height_frac = (max(ys) - min(ys)) / image_height
    y_center = (max(ys) + min(ys)) / 2.0 / image_height
    x_center = (max(xs) + min(xs)) / 2.0 / image_width
    return {
        "title_height_frac": float(height_frac),
        "title_y_center": float(y_center),  # 0 = top, 1 = bottom
        "title_centeredness": float(1.0 - 2.0 * abs(x_center - 0.5)),
    }


def face_geometry(
    face_boxes: list[tuple[float, float, float, float]],
    image_width: int,
    image_height: int,
) -> dict[str, float]:
    """Face count and dominance from the SCRFD boxes the pipeline already has."""
    image_area = float(image_width * image_height)
    if image_area <= 0:
        return {"face_count": 0.0, "largest_face_frac": 0.0}
    areas = [
        max(0.0, x2 - x1) * max(0.0, y2 - y1) for x1, y1, x2, y2 in face_boxes
    ]
    return {
        "face_count": float(len(face_boxes)),
        "largest_face_frac": float(max(areas) / image_area) if areas else 0.0,
    }
