"""Build the CLIP B/32 taste exemplar profile."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFile
from tqdm import tqdm

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.embedding import CLIPImageEncoder

ImageFile.LOAD_TRUNCATED_IMAGES = True

_DEFAULT_TRAINING_DIR = (
    Path(__file__).resolve().parents[2] / "experiments" / "data" / "training_data"
)
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}


def scan_images(directory: Path) -> list[Path]:
    if not directory.exists():
        raise FileNotFoundError(f"Training directory not found: {directory}")
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS
    )


def extract_embeddings(
    directory: Path,
    encoder: CLIPImageEncoder,
) -> tuple[np.ndarray, list[str]]:
    embeddings: list[np.ndarray] = []
    names: list[str] = []
    for path in tqdm(scan_images(directory), desc="Embedding exemplars", unit="poster"):
        try:
            with Image.open(path) as image:
                embeddings.append(encoder.encode(image.convert("RGB")))
            names.append(path.name)
        except Exception as exc:
            tqdm.write(f"[SKIP] {path.name}: {exc}")
    if not embeddings:
        raise RuntimeError(f"No training images could be embedded from {directory}")
    return np.stack(embeddings).astype(np.float32), names


def compute_centroid(embeddings: np.ndarray) -> np.ndarray:
    centroid = embeddings.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    return (centroid / norm if norm > 1e-10 else centroid).astype(np.float32)


def print_diagnostics(
    embeddings: np.ndarray,
    centroid: np.ndarray,
    names: list[str],
    *,
    k: int,
) -> None:
    centroid_sims = embeddings @ centroid
    similarity_matrix = embeddings @ embeddings.T
    np.fill_diagonal(similarity_matrix, -np.inf)
    neighbor_count = min(k, max(len(names) - 1, 1))
    nearest = np.partition(similarity_matrix, -neighbor_count, axis=1)[:, -neighbor_count:]
    knn_means = nearest.mean(axis=1)

    print("\n" + "=" * 64)
    print("CLIP B/32 TASTE PROFILE DIAGNOSTICS")
    print(f"Posters: {len(names)}")
    print(f"Centroid cosine: mean={centroid_sims.mean():.4f} std={centroid_sims.std():.4f}")
    print(f"Top-{neighbor_count} neighbor cosine: mean={knn_means.mean():.4f} std={knn_means.std():.4f}")
    print("Top-5 centroid matches:")
    for index in np.argsort(centroid_sims)[::-1][:5]:
        print(f"  {names[index]}: {centroid_sims[index]:.4f}")
    print("Bottom-5 centroid matches:")
    for index in np.argsort(centroid_sims)[:5]:
        print(f"  {names[index]}: {centroid_sims[index]:.4f}")
    print("=" * 64)


def save_profile(
    output: Path,
    embeddings: np.ndarray,
    names: list[str],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        output,
        embeddings=embeddings,
        poster_names=np.asarray(names, dtype=object),
        centroid_emb=compute_centroid(embeddings),
        model_name=np.asarray(pipeline_settings.AI_MODEL),
    )
    print(f"[INFO] Saved {len(names)} exemplars to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-dir", type=Path, default=_DEFAULT_TRAINING_DIR)
    parser.add_argument("--model", type=Path, default=pipeline_settings.CLIP_MODEL_PATH)
    parser.add_argument("--output", type=Path, default=pipeline_settings.TASTE_PROFILE_PATH)
    args = parser.parse_args()

    started = time.perf_counter()
    embeddings, names = extract_embeddings(
        args.training_dir,
        CLIPImageEncoder(args.model),
    )
    centroid = compute_centroid(embeddings)
    save_profile(args.output, embeddings, names)
    print_diagnostics(embeddings, centroid, names, k=pipeline_settings.K_NEIGHBORS)
    print(f"[INFO] Completed in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
