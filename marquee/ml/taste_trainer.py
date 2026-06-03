"""Taste profile trainer.

Scans a folder of manually curated posters (one per movie), extracts CLIP
embeddings and LAB colour histograms for each, then computes a centroid
taste profile and saves it to a .npz file for use by the scorer.

Adapted from the DINOv2 version in the old project's experiments/profiling/.
Now uses CLIP ViT-B/16 embeddings (512-dim) instead of DINOv2 (1536-dim).

Usage:
    python -m marquee.ml.taste_trainer [--training-dir PATH] [--model PATH] [--output PATH]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
from tqdm import tqdm

from marquee.config import settings
from marquee.ml.color_histogram import extract_color_histogram
from marquee.ml.embedding import CLIPImageEncoder
from marquee.ml.preprocessing import preprocess_image

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_DEFAULT_TRAINING_DIR = (
    Path(__file__).parent.parent.parent / "experiments" / "data" / "training_data"
)
_DEFAULT_OUTPUT = Path(__file__).parent / "taste_profile.npz"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
)


def _load_image_safe(image_path: Path):
    """Attempt to open a PIL image. Returns None on any error."""
    from PIL import Image, ImageFile

    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        img = Image.open(image_path)
        img.load()
        return img
    except Exception:
        return None


def _scan_images(directory: Path) -> list[Path]:
    """Return a sorted list of image paths in the top level of *directory*."""
    if not directory.exists():
        raise FileNotFoundError(f"Training directory not found: {directory}")
    images = sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )
    print(f"[INFO] Found {len(images)} images in {directory}")
    return images


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.clip(np.dot(a, b), -1.0, 1.0))


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def extract_features_from_directory(
    image_dir: Path,
    encoder: CLIPImageEncoder,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Extract CLIP embeddings and LAB colour histograms for all images.

    Returns:
        embeddings:   (N, 512)  CLIP image embeddings
        color_hists:  (N, 48)   LAB colour histograms
        poster_names: list[str]  filenames, same order
    """
    image_paths = _scan_images(image_dir)

    embeddings: list[np.ndarray] = []
    color_hists: list[np.ndarray] = []
    poster_names: list[str] = []

    bar = tqdm(image_paths, desc="Extracting features", unit="poster", ncols=88)
    for path in bar:
        bar.set_postfix_str(path.name[:40], refresh=False)

        # Load image
        img = _load_image_safe(path)
        if img is None:
            tqdm.write(f"[SKIP] {path.name}: could not open image")
            continue

        # CLIP embedding
        try:
            pixel_values = preprocess_image(img)
            emb = encoder.encode(pixel_values)
        except Exception as exc:
            tqdm.write(f"[SKIP] {path.name}: embedding failed — {exc}")
            continue

        # LAB colour histogram
        try:
            color = extract_color_histogram(path)
        except Exception as exc:
            tqdm.write(f"[SKIP] {path.name}: colour histogram failed — {exc}")
            continue

        embeddings.append(emb)
        color_hists.append(color)
        poster_names.append(path.name)

    if not embeddings:
        raise RuntimeError(
            f"No images could be processed from {image_dir}. "
            "Check that the directory contains valid image files."
        )

    return (
        np.stack(embeddings, axis=0),
        np.stack(color_hists, axis=0),
        poster_names,
    )


# ---------------------------------------------------------------------------
# Centroid computation
# ---------------------------------------------------------------------------


def compute_taste_profile(
    embeddings: np.ndarray,
    color_hists: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute L2-normalised centroid vectors.

    The centroid is the mean of all individual vectors, re-normalised.
    Since individual embeddings are already L2-normalised, the centroid
    points toward the "average direction" of the taste distribution.
    """
    mean_emb = embeddings.mean(axis=0)
    norm = np.linalg.norm(mean_emb)
    centroid_emb = (
        (mean_emb / norm if norm > 1e-10 else mean_emb).astype(np.float32)
    )

    mean_color = color_hists.mean(axis=0)
    norm = np.linalg.norm(mean_color)
    centroid_color = (
        (mean_color / norm if norm > 1e-10 else mean_color).astype(np.float32)
    )

    return centroid_emb, centroid_color


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def save_taste_profile(
    output_path: Path,
    centroid_emb: np.ndarray,
    centroid_color: np.ndarray,
    embeddings: np.ndarray,
    color_hists: np.ndarray,
    poster_names: list[str],
) -> None:
    """Save all taste profile data to a .npz file.

    Stores individual embeddings and histograms (not just centroids)
    for future analysis: outlier removal, variance measurement, or
    switching to k-NN without re-running the trainer.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    np.savez(
        str(output_path),
        centroid_emb=centroid_emb,
        centroid_color=centroid_color,
        embeddings=embeddings,
        color_hists=color_hists,
        poster_names=np.array(poster_names, dtype=object),
        model_name=np.array(settings.AI_MODEL),
    )

    size_kb = output_path.stat().st_size / 1024
    print(f"\n[INFO] Saved taste profile: {output_path} ({size_kb:.0f} KB)")
    print(
        f"       {len(poster_names)} posters | "
        f"embeddings {embeddings.shape} | "
        f"colour_hists {color_hists.shape}"
    )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def print_diagnostics(
    embeddings: np.ndarray,
    color_hists: np.ndarray,
    centroid_emb: np.ndarray,
    centroid_color: np.ndarray,
    poster_names: list[str],
) -> None:
    """Print quality diagnostics to evaluate the taste profile.

    A high mean similarity (> 0.65) with low std dev (< 0.10) indicates a
    cohesive, consistent visual taste. Low mean or high variance suggests
    the training set is visually diverse.
    """
    n = len(poster_names)
    emb_weight = settings.TASTE_EMB_WEIGHT
    color_weight = settings.TASTE_COLOR_WEIGHT

    # Per-poster similarities
    emb_sims = np.array([
        _cosine_similarity(embeddings[i], centroid_emb) for i in range(n)
    ])
    color_sims = np.array([
        _cosine_similarity(color_hists[i], centroid_color) for i in range(n)
    ])
    combined = emb_weight * emb_sims + color_weight * color_sims

    print("\n" + "=" * 60)
    print("  TASTE PROFILE DIAGNOSTICS")
    print("=" * 60)
    print(f"  Model:                  {settings.AI_MODEL}")
    print(f"  Posters processed:      {n}")
    print(f"  Visual similarity:      mean={emb_sims.mean():.4f}  std={emb_sims.std():.4f}")
    print(f"  Colour similarity:      mean={color_sims.mean():.4f}  std={color_sims.std():.4f}")
    print(f"  Combined ({emb_weight:.0%}/{color_weight:.0%}): "
          f"mean={combined.mean():.4f}  std={combined.std():.4f}")
    print()

    # Top-5 "on-taste"
    top_idx = np.argsort(emb_sims)[::-1][:5]
    print("  Top-5 most on-taste (visual):")
    for rank, i in enumerate(top_idx, 1):
        print(f"    {rank}. {poster_names[i]:<50s}  sim={emb_sims[i]:.4f}")

    print()

    # Bottom-5 "off-taste"
    bot_idx = np.argsort(emb_sims)[:5]
    print("  Bottom-5 most off-taste (visual):")
    for rank, i in enumerate(bot_idx, 1):
        print(f"    {rank}. {poster_names[i]:<50s}  sim={emb_sims[i]:.4f}")

    print("=" * 60)
    print()
    print("  Interpretation guide:")
    print("    Visual mean > 0.65 → cohesive taste, sharp selections expected")
    print("    Visual mean < 0.50 → diverse taste, consider curating training set")
    print("    Visual std  < 0.10 → consistent style preferences")
    print("    Visual std  > 0.15 → wide stylistic range in training set")
    print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a CLIP taste profile from a folder of curated posters."
    )
    parser.add_argument(
        "--training-dir",
        type=Path,
        default=_DEFAULT_TRAINING_DIR,
        help="Folder of manually curated training posters",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=None,
        help="Path to CLIP ONNX model (default: marquee/ml/models/clip_vit_b16.onnx)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Destination for the taste profile .npz file",
    )
    args = parser.parse_args()

    print(f"[INFO] Model:        {settings.AI_MODEL}")
    print(f"[INFO] Training dir: {args.training_dir}")
    print(f"[INFO] Output:       {args.output}")
    print()

    encoder = CLIPImageEncoder(model_path=args.model)

    t0 = time.time()
    embeddings, color_hists, poster_names = extract_features_from_directory(
        args.training_dir, encoder
    )
    elapsed = time.time() - t0
    print(
        f"\n[INFO] Feature extraction complete in {elapsed:.1f}s "
        f"({elapsed / len(poster_names):.2f}s/poster)"
    )

    centroid_emb, centroid_color = compute_taste_profile(embeddings, color_hists)

    save_taste_profile(
        args.output,
        centroid_emb,
        centroid_color,
        embeddings,
        color_hists,
        poster_names,
    )

    print_diagnostics(
        embeddings, color_hists, centroid_emb, centroid_color, poster_names
    )


if __name__ == "__main__":
    main()
