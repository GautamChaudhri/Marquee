"""Build the CLIP taste exemplar profile (positive and optional negative).

Positives come from ``experiments/data/training_data/`` (hand-picked posters
you like). Negatives are optional and come from a sibling
``negative_data/`` directory — posters you explicitly dislike (floating-head
composites, low-quality fan art). When present, the pipeline penalizes
candidates that sit closer to the disliked set than the liked set, which is
the single strongest signal for keeping junk out of the top ranks.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFile
from tqdm import tqdm

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.embedding import CLIPImageEncoder
from marquee.ml.taste_store import weighted_topk_mean

ImageFile.LOAD_TRUNCATED_IMAGES = True

_DEFAULT_TRAINING_DIR = (
    Path(__file__).resolve().parents[1] / "experiments" / "training_data"
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
    *,
    label: str = "exemplars",
) -> tuple[np.ndarray, list[str]]:
    embeddings: list[np.ndarray] = []
    names: list[str] = []
    batch: list[Image.Image] = []
    batch_names: list[str] = []

    def flush() -> None:
        if not batch:
            return
        for vector in encoder.encode_batch(batch):  # batched = much faster on GPU
            embeddings.append(vector)
        names.extend(batch_names)
        batch.clear()
        batch_names.clear()

    for path in tqdm(scan_images(directory), desc=f"Embedding {label}", unit="poster"):
        try:
            with Image.open(path) as image:
                batch.append(image.convert("RGB"))
            batch_names.append(path.name)
        except Exception as exc:
            tqdm.write(f"[SKIP] {path.name}: {exc}")
            continue
        if len(batch) >= 32:
            flush()
    flush()

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
    neg_embeddings: np.ndarray | None = None,
) -> None:
    centroid_sims = embeddings @ centroid
    similarity_matrix = embeddings @ embeddings.T
    np.fill_diagonal(similarity_matrix, -np.inf)
    neighbor_count = min(k, max(len(names) - 1, 1))
    nearest = np.partition(similarity_matrix, -neighbor_count, axis=1)[:, -neighbor_count:]
    knn_means = nearest.mean(axis=1)

    print("\n" + "=" * 64)
    print("CLIP TASTE PROFILE DIAGNOSTICS")
    print(f"Model: {pipeline_settings.AI_MODEL}")
    print(f"Posters: {len(names)}")
    print(f"Centroid cosine: mean={centroid_sims.mean():.4f} std={centroid_sims.std():.4f}")
    print(f"Top-{neighbor_count} neighbor cosine: mean={knn_means.mean():.4f} std={knn_means.std():.4f}")
    print("Top-5 centroid matches:")
    for index in np.argsort(centroid_sims)[::-1][:5]:
        print(f"  {names[index]}: {centroid_sims[index]:.4f}")
    print("Bottom-5 centroid matches:")
    for index in np.argsort(centroid_sims)[:5]:
        print(f"  {names[index]}: {centroid_sims[index]:.4f}")
    if neg_embeddings is not None and len(neg_embeddings):
        # Separation read: for each negative, how close is it to the positive
        # set vs the other negatives? Positive margin = the penalty will fire.
        pos_sims = neg_embeddings @ embeddings.T
        neg_sims = neg_embeddings @ neg_embeddings.T
        np.fill_diagonal(neg_sims, -np.inf)
        margins = []
        for row in range(len(neg_embeddings)):
            pos_score = weighted_topk_mean(pos_sims[row], k, weighting="mean")
            neg_score = weighted_topk_mean(neg_sims[row], k, weighting="mean")
            margins.append(neg_score - pos_score)
        margins_arr = np.asarray(margins)
        print(f"Negative exemplars: {len(neg_embeddings)}")
        print(
            "Negative separation (neg-knn minus pos-knn, higher = cleaner signal): "
            f"mean={margins_arr.mean():.4f} min={margins_arr.min():.4f}"
        )
        if margins_arr.mean() <= 0:
            print(
                "[WARN] Negatives sit closer to your liked posters than to each "
                "other — the penalty will rarely fire. Add more/clearer negatives."
            )
    print("=" * 64)


def save_profile(
    output: Path,
    embeddings: np.ndarray,
    names: list[str],
    *,
    neg_embeddings: np.ndarray | None = None,
    neg_names: list[str] | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, np.ndarray] = {
        "embeddings": embeddings,
        "poster_names": np.asarray(names, dtype=object),
        "centroid_emb": compute_centroid(embeddings),
        "model_name": np.asarray(pipeline_settings.AI_MODEL),
    }
    if neg_embeddings is not None and len(neg_embeddings):
        payload["neg_embeddings"] = neg_embeddings
        payload["neg_poster_names"] = np.asarray(neg_names or [], dtype=object)
    np.savez(output, **payload)
    negatives = 0 if neg_embeddings is None else len(neg_embeddings)
    print(f"[INFO] Saved {len(names)} exemplars (+{negatives} negatives) to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-dir", type=Path, default=_DEFAULT_TRAINING_DIR)
    parser.add_argument(
        "--negative-dir",
        type=Path,
        default=None,
        help=(
            "Directory of disliked posters. Defaults to the 'negative_data' "
            "sibling of --training-dir when it exists."
        ),
    )
    parser.add_argument("--model", type=Path, default=pipeline_settings.CLIP_MODEL_PATH)
    parser.add_argument("--output", type=Path, default=pipeline_settings.TASTE_PROFILE_PATH)
    args = parser.parse_args()

    negative_dir = args.negative_dir
    if negative_dir is None:
        candidate = args.training_dir.parent / "negative_data"
        negative_dir = candidate if candidate.is_dir() else None

    started = time.perf_counter()
    encoder = CLIPImageEncoder(args.model)
    embeddings, names = extract_embeddings(args.training_dir, encoder, label="positives")

    neg_embeddings: np.ndarray | None = None
    neg_names: list[str] | None = None
    if negative_dir is not None and scan_images(negative_dir):
        neg_embeddings, neg_names = extract_embeddings(
            negative_dir, encoder, label="negatives"
        )

    centroid = compute_centroid(embeddings)
    save_profile(
        args.output,
        embeddings,
        names,
        neg_embeddings=neg_embeddings,
        neg_names=neg_names,
    )
    print_diagnostics(
        embeddings,
        centroid,
        names,
        k=pipeline_settings.K_NEIGHBORS,
        neg_embeddings=neg_embeddings,
    )
    print(f"[INFO] Completed in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
