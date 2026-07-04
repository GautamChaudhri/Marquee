"""Build the taste exemplar profile: embeddings + calibration distributions.

Beyond the CLIP (and optional negative) embeddings, the trainer now measures
every extended pipeline feature on each positive exemplar and stores the
raw distributions in the profile. Those distributions power
exemplar-calibrated normalization (``calibration.py``): at runtime a
candidate is scored by how typical each of its values is of *your* picks —
darkness, saturation, title size and placement, face/person composition,
illustrated-vs-photo leaning, even the aesthetic band you actually like.

Also stored when available:
  - **DINOv2 embeddings** of all exemplars (and negatives) for the
    ``dino_knn`` second style opinion, plus each exemplar's own k-NN
    similarity to the rest of the set (the empirical normalization range).
  - **Zero-shot axis values** per exemplar (from the CLIP embeddings).

Feature measurement matches the pipeline exactly: classic-CV features are
computed on width-500-standardized images (the pipeline sees w500
downloads), title geometry comes from the same OCR title-box matching, and
faces/persons use the same detectors. OCR is the slow part (~0.3-0.5s per
exemplar); skip it with ``--skip-ocr`` if you don't care about typography
geometry calibration.

Negative exemplars contribute embeddings only — taste bands are built from
what you like, not diluted by what you don't.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFile
from tqdm import tqdm

from marquee.core.jobs.cancel_registry import raise_if_cancelled
from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.aesthetic import AestheticPredictor
from marquee.ml.artifact_codec import (
    save_npz_atomic,
    unicode_array,
    unicode_scalar,
)
from marquee.ml.calibration import (
    CALIB_NAMES_KEY,
    CALIB_VALUES_KEY,
    TasteCalibration,
)
from marquee.ml.dino import DinoImageEncoder
from marquee.ml.embedding import CLIPImageEncoder
from marquee.ml.face import FaceDetector
from marquee.ml.namespaces import TasteNamespace, get_namespace
from marquee.ml.person import PersonDetector
from marquee.ml.taste_store import DINO_SELF_KNN_KEY, weighted_topk_mean
from marquee.ml.visual_features import (
    composition_features,
    face_geometry,
    palette_features,
    quality_artifact_features,
    standardize_width,
    title_geometry,
)
from marquee.ml.zeroshot import ZeroShotAxes

ImageFile.LOAD_TRUNCATED_IMAGES = True
logger = logging.getLogger(__name__)

_DEFAULT_TRAINING_DIR = Path(pipeline_settings.TRAINING_DATA_DIR)
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
_YEAR_SUFFIX = re.compile(r"\s*\(\d{4}\)\s*$")
_HASH_ARRAY_KEY = "poster_sha256s"
ProgressCallback = Callable[[dict], None]


def _emit_progress(callback: ProgressCallback | None, **payload) -> None:
    if callback is not None:
        callback(payload)


def scan_images(directory: Path) -> list[Path]:
    if not directory.exists():
        raise FileNotFoundError(f"Training directory not found: {directory}")
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS
    )


def title_from_filename(path: Path) -> str:
    """'Movie Title (Year).jpg' -> 'Movie Title'."""
    return _YEAR_SUFFIX.sub("", path.stem).strip()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Embedding extraction (CLIP / DINOv2, batched)
# ---------------------------------------------------------------------------


def extract_embeddings(
    paths: list[Path],
    encoder,
    *,
    label: str,
    progress_callback: ProgressCallback | None = None,
    stage: str | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[np.ndarray, list[Path]]:
    """Batch-embed images; returns embeddings + the paths that succeeded."""
    embeddings: list[np.ndarray] = []
    kept: list[Path] = []
    batch: list[Image.Image] = []
    batch_paths: list[Path] = []

    def flush() -> None:
        if not batch:
            return
        raise_if_cancelled(cancel_event, "taste profile rebuild cancelled")
        for vector in encoder.encode_batch(batch):
            embeddings.append(vector)
        kept.extend(batch_paths)
        _emit_progress(
            progress_callback,
            stage=stage or f"embedding {label}",
            processed=len(kept),
            total=len(paths),
        )
        batch.clear()
        batch_paths.clear()

    _emit_progress(
        progress_callback,
        stage=stage or f"embedding {label}",
        processed=0,
        total=len(paths),
    )
    for path in tqdm(paths, desc=f"Embedding {label}", unit="poster"):
        raise_if_cancelled(cancel_event, "taste profile rebuild cancelled")
        try:
            with Image.open(path) as image:
                batch.append(image.convert("RGB"))
            batch_paths.append(path)
        except Exception as exc:
            tqdm.write(f"[SKIP] {path.name}: {exc}")
            continue
        if len(batch) >= 32:
            flush()
    flush()

    if not embeddings:
        raise RuntimeError(f"No images could be embedded from {paths[:1]}...")
    return np.stack(embeddings).astype(np.float32), kept


# ---------------------------------------------------------------------------
# Per-exemplar extended feature measurement
# ---------------------------------------------------------------------------


def measure_exemplar_features(
    paths: list[Path],
    clip_embeddings: np.ndarray,
    *,
    aesthetic: AestheticPredictor,
    axes: ZeroShotAxes | None,
    face_detector: FaceDetector | None,
    person_detector: PersonDetector | None,
    run_ocr: bool,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[str], np.ndarray]:
    """Measure every calibratable feature on each positive exemplar.

    Returns (feature_names, (F, N) matrix) with NaN marking unmeasurable
    values (corrupt image, no title found, detector unavailable).
    """
    rows: list[dict[str, float]] = []
    raise_if_cancelled(cancel_event, "taste profile rebuild cancelled")

    started = time.perf_counter()
    logger.info("Taste calibration starting for %d exemplars", len(paths))
    ocr_filter_module = None
    if run_ocr:
        from marquee.pipeline import ocr_filter as ocr_filter_module  # noqa: PLC0415

    aesthetic_scores = aesthetic.score_batch(clip_embeddings)
    raise_if_cancelled(cancel_event, "taste profile rebuild cancelled")

    total = len(paths)
    _emit_progress(
        progress_callback,
        stage="calibration",
        substage="starting",
        processed=0,
        total=total,
        message=f"Starting calibration for {total} exemplars.",
    )
    ocr_logged = False
    for index, path in enumerate(tqdm(paths, desc="Measuring features", unit="poster")):
        raise_if_cancelled(cancel_event, "taste profile rebuild cancelled")
        display_index = index + 1
        features: dict[str, float] = {"aesthetic": float(aesthetic_scores[index])}
        item_label = path.name

        if axes is not None:
            _emit_progress(
                progress_callback,
                stage="calibration",
                substage="zeroshot",
                current_item=item_label,
                processed=index,
                total=total,
                message=f"Measuring zero-shot axes for {item_label}.",
            )
            features.update(axes.scores(clip_embeddings[index]))

        try:
            _emit_progress(
                progress_callback,
                stage="calibration",
                substage="cv",
                current_item=item_label,
                processed=index,
                total=total,
                message=f"Measuring CV features for {item_label}.",
            )
            image_bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image_bgr is None:
                raise ValueError("unreadable image")
            standardized = standardize_width(image_bgr)
            features.update(palette_features(standardized))
            features.update(composition_features(standardized))
            features.update(quality_artifact_features(standardized))

            if face_detector is not None:
                _emit_progress(
                    progress_callback,
                    stage="calibration",
                    substage="face",
                    current_item=item_label,
                    processed=index,
                    total=total,
                    message=f"Detecting faces for {item_label}.",
                )
                boxes = face_detector.detect(standardized)
                features.update(face_geometry(boxes, standardized.shape[1], standardized.shape[0]))

            if person_detector is not None:
                _emit_progress(
                    progress_callback,
                    stage="calibration",
                    substage="person",
                    current_item=item_label,
                    processed=index,
                    total=total,
                    message=f"Detecting people for {item_label}.",
                )
                features.update(person_detector.person_features(standardized))
        except Exception as exc:
            tqdm.write(f"[WARN] CV features failed for {path.name}: {exc}")

        if ocr_filter_module is not None:
            try:
                if not ocr_logged:
                    logger.info("Taste calibration loading OCR on first exemplar")
                    ocr_logged = True
                _emit_progress(
                    progress_callback,
                    stage="calibration",
                    substage="ocr",
                    current_item=item_label,
                    processed=index,
                    total=total,
                    message=f"Measuring OCR title geometry for {item_label}.",
                )
                title = title_from_filename(path)
                text_filter = ocr_filter_module.PosterTextFilter(title)
                result = text_filter.is_acceptable(path)
                with Image.open(path) as image:
                    width, height = image.size
                features.update(title_geometry(result.title_bbox, width, height))
            except Exception as exc:
                tqdm.write(f"[WARN] OCR title geometry failed for {path.name}: {exc}")

        rows.append(features)
        logger.info(
            "Taste calibration exemplar %d/%d complete: %s",
            display_index,
            total,
            item_label,
        )
        _emit_progress(
            progress_callback,
            stage="calibration",
            substage="complete",
            current_item=item_label,
            processed=display_index,
            total=total,
            message=f"Calibrated {display_index}/{total}: {item_label}.",
        )

    feature_names = sorted({name for row in rows for name in row})
    matrix = np.full((len(feature_names), len(paths)), np.nan, dtype=np.float64)
    for column, row in enumerate(rows):
        for feature_index, name in enumerate(feature_names):
            value = row.get(name)
            if value is not None and np.isfinite(value):
                matrix[feature_index, column] = float(value)
    logger.info(
        "Taste calibration completed for %d exemplars in %.1fs",
        total,
        time.perf_counter() - started,
    )
    return feature_names, matrix


def compute_dino_self_knn(dino_embeddings: np.ndarray, k: int) -> np.ndarray:
    """Each exemplar's mean top-k similarity to the *other* exemplars.

    This is the empirical distribution of "a poster that belongs in this
    taste profile" — its p5/p95 become the dino_knn normalization range.
    """
    sims = dino_embeddings @ dino_embeddings.T
    np.fill_diagonal(sims, -np.inf)
    return np.asarray(
        [weighted_topk_mean(row[np.isfinite(row)], k, weighting="mean") for row in sims],
        dtype=np.float64,
    )


def compute_centroid(embeddings: np.ndarray) -> np.ndarray:
    centroid = embeddings.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    return (centroid / norm if norm > 1e-10 else centroid).astype(np.float32)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def print_diagnostics(
    embeddings: np.ndarray,
    names: list[str],
    *,
    k: int,
    neg_embeddings: np.ndarray | None,
    calib_names: list[str] | None,
    calib_values: np.ndarray | None,
    dino_self_knn: np.ndarray | None,
) -> None:
    centroid = compute_centroid(embeddings)
    centroid_sims = embeddings @ centroid
    similarity_matrix = embeddings @ embeddings.T
    np.fill_diagonal(similarity_matrix, -np.inf)
    neighbor_count = min(k, max(len(names) - 1, 1))
    nearest = np.partition(similarity_matrix, -neighbor_count, axis=1)[:, -neighbor_count:]
    knn_means = nearest.mean(axis=1)

    print("\n" + "=" * 64)
    print("TASTE PROFILE DIAGNOSTICS")
    print(f"Model: {pipeline_settings.AI_MODEL}")
    print(f"Posters: {len(names)}")
    print(f"Centroid cosine: mean={centroid_sims.mean():.4f} std={centroid_sims.std():.4f}")
    print(
        f"Top-{neighbor_count} neighbor cosine: mean={knn_means.mean():.4f} std={knn_means.std():.4f}"
    )

    if neg_embeddings is not None and len(neg_embeddings):
        pos_sims = neg_embeddings @ embeddings.T
        neg_sims = neg_embeddings @ neg_embeddings.T
        np.fill_diagonal(neg_sims, -np.inf)
        margins = []
        for row in range(len(neg_embeddings)):
            pos_score = weighted_topk_mean(pos_sims[row], k, weighting="mean")
            neg_score = weighted_topk_mean(
                neg_sims[row][np.isfinite(neg_sims[row])], k, weighting="mean"
            )
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

    if dino_self_knn is not None and dino_self_knn.size:
        p5, p50, p95 = np.percentile(dino_self_knn, [5, 50, 95])
        print(
            f"DINOv2 self k-NN: p5={p5:.4f} median={p50:.4f} p95={p95:.4f} "
            "(runtime dino_knn normalization range = p5..p95)"
        )

    if calib_names and calib_values is not None:
        calibration = TasteCalibration(calib_names, calib_values)
        print(f"Calibration bands ({len(calibration.calibrated_features)} active):")
        for line in calibration.describe():
            print(f"  {line}")
    print("=" * 64)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def rebuild_profile(
    *,
    training_dir: Path | None = None,
    negative_dir: Path | None = None,
    model: Path | None = None,
    output: Path | None = None,
    skip_ocr: bool = False,
    skip_dino: bool = False,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    namespace: TasteNamespace | None = None,
) -> Path:
    """Build the taste profile from the training folders and save it.

    Callable entry point behind ``POST /api/taste/retrain`` and the CLI. All
    arguments default to the configured paths.
    """
    from argparse import Namespace

    ns = namespace or get_namespace("movies")
    args = Namespace(
        training_dir=training_dir,
        negative_dir=negative_dir,
        model=model or pipeline_settings.CLIP_MODEL_PATH,
        output=output or ns.profile_path,
        skip_ocr=skip_ocr,
        skip_dino=skip_dino,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
        namespace=ns,
    )
    return _run_build(args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-dir", type=Path, default=None)
    parser.add_argument(
        "--negative-dir",
        type=Path,
        default=None,
        help=(
            "Directory of disliked posters. Defaults to the namespace's negative_dir "
            "when it exists."
        ),
    )
    parser.add_argument("--model", type=Path, default=pipeline_settings.CLIP_MODEL_PATH)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--skip-ocr",
        action="store_true",
        help="Skip OCR title-geometry measurement (the slow part, ~0.4s/poster)",
    )
    parser.add_argument(
        "--skip-dino",
        action="store_true",
        help="Skip DINOv2 embeddings even if the model file exists",
    )
    args = parser.parse_args()
    args.namespace = get_namespace("movies")
    if args.output is None:
        args.output = args.namespace.profile_path
    _run_build(args)


def _run_build(args) -> Path:
    ns = getattr(args, "namespace", None) or get_namespace("movies")
    negative_dir = args.negative_dir
    if negative_dir is None:
        negative_dir = ns.negative_dir

    started = time.perf_counter()
    progress_callback = getattr(args, "progress_callback", None)
    cancel_event = getattr(args, "cancel_event", None)
    raise_if_cancelled(cancel_event, "taste profile rebuild cancelled")
    _emit_progress(
        progress_callback,
        stage="starting",
        processed=0,
        total=0,
        message="Starting taste profile rebuild.",
    )
    logger.info("Taste profile rebuild starting")
    encoder = CLIPImageEncoder(args.model)
    raise_if_cancelled(cancel_event, "taste profile rebuild cancelled")

    path_to_kind: dict[Path, str] = {}
    paths = []
    if args.training_dir is not None:
        subdirs_found = False
        for kind in ns.training_dirs:
            subdir = args.training_dir / kind
            if subdir.is_dir():
                subdirs_found = True
                kpaths = scan_images(subdir)
                paths.extend(kpaths)
                for p in kpaths:
                    path_to_kind[p] = kind
        if not subdirs_found:
            paths = scan_images(args.training_dir)
            for p in paths:
                kind = next(iter(ns.training_dirs.keys())) if ns.training_dirs else "movie"
                path_to_kind[p] = kind
    else:
        paths = []
        for kind, tdir in ns.training_dirs.items():
            if tdir.is_dir():
                kpaths = scan_images(tdir)
                paths.extend(kpaths)
                for p in kpaths:
                    path_to_kind[p] = kind

    embeddings, kept_paths = extract_embeddings(
        paths,
        encoder,
        label="positives (CLIP)",
        progress_callback=progress_callback,
        stage="clip",
        cancel_event=cancel_event,
    )
    raise_if_cancelled(cancel_event, "taste profile rebuild cancelled")
    logger.info("Taste profile CLIP embeddings complete: %d exemplars", len(kept_paths))

    neg_embeddings: np.ndarray | None = None
    neg_paths: list[Path] = []
    if negative_dir is not None and scan_images(negative_dir):
        neg_paths_all = scan_images(negative_dir)
        neg_embeddings, neg_paths = extract_embeddings(
            neg_paths_all,
            encoder,
            label="negatives (CLIP)",
            progress_callback=progress_callback,
            stage="clip-negatives",
            cancel_event=cancel_event,
        )

    # ── DINOv2 space (optional) ──────────────────────────────────────
    dino_embeddings: np.ndarray | None = None
    neg_dino: np.ndarray | None = None
    dino_self_knn: np.ndarray | None = None
    dino_model_name: str | None = None
    dino_encoder = DinoImageEncoder()
    raise_if_cancelled(cancel_event, "taste profile rebuild cancelled")
    if args.skip_dino:
        print("[INFO] DINOv2 skipped (--skip-dino)")
    elif not dino_encoder.available:
        print(
            "[WARN] DINOv2 model not found — profile will have no dino_knn "
            "support. Export it with: python -m marquee.ml.dino"
        )
    else:
        dino_embeddings, dino_kept = extract_embeddings(
            kept_paths,
            dino_encoder,
            label="positives (DINOv2)",
            progress_callback=progress_callback,
            stage="dino",
            cancel_event=cancel_event,
        )
        if dino_kept != kept_paths:
            raise RuntimeError(
                "DINOv2 embedded a different exemplar set than CLIP — "
                "fix or remove the unreadable files and rerun."
            )
        dino_model_name = dino_encoder.model_name
        dino_self_knn = compute_dino_self_knn(dino_embeddings, pipeline_settings.K_NEIGHBORS)
        logger.info("Taste profile DINO embeddings complete: %d exemplars", len(dino_kept))
        if neg_paths:
            neg_dino, neg_dino_kept = extract_embeddings(
                neg_paths,
                dino_encoder,
                label="negatives (DINOv2)",
                progress_callback=progress_callback,
                stage="dino-negatives",
                cancel_event=cancel_event,
            )
            if neg_dino_kept != neg_paths:
                raise RuntimeError(
                    "DINOv2 embedded a different negative set than CLIP — "
                    "fix or remove the unreadable files and rerun."
                )

    # ── Extended feature distributions over positives ────────────────
    aesthetic = AestheticPredictor()
    axes = ZeroShotAxes.load()
    face_detector = FaceDetector()
    if not face_detector.model_path.exists():
        print("[WARN] Face model missing — face geometry not calibrated")
        face_detector = None
    person_detector: PersonDetector | None = PersonDetector()
    if not person_detector.available:
        print("[WARN] Person model missing — person features not calibrated")
        person_detector = None

    calib_names, calib_values = measure_exemplar_features(
        kept_paths,
        embeddings,
        aesthetic=aesthetic,
        axes=axes,
        face_detector=face_detector,
        person_detector=person_detector,
        run_ocr=not args.skip_ocr,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )
    raise_if_cancelled(cancel_event, "taste profile rebuild cancelled")

    # ── Save ─────────────────────────────────────────────────────────
    _emit_progress(
        progress_callback,
        stage="saving",
        substage="profile",
        processed=len(kept_paths),
        total=len(kept_paths),
        message="Saving rebuilt taste profile.",
    )
    asset_kinds = [path_to_kind.get(p, "movie") for p in kept_paths]
    payload: dict[str, np.ndarray] = {
        "embeddings": embeddings,
        "poster_names": unicode_array([p.name for p in kept_paths]),
        "asset_kinds": unicode_array(asset_kinds),
        _HASH_ARRAY_KEY: unicode_array([_file_sha256(path) for path in kept_paths]),
        "centroid_emb": compute_centroid(embeddings),
        "model_name": unicode_scalar(pipeline_settings.AI_MODEL),
        CALIB_NAMES_KEY: unicode_array(calib_names),
        CALIB_VALUES_KEY: calib_values,
    }
    if neg_embeddings is not None and len(neg_embeddings):
        payload["neg_embeddings"] = neg_embeddings
        payload["neg_poster_names"] = unicode_array([p.name for p in neg_paths])
    if dino_embeddings is not None:
        payload["dino_embeddings"] = dino_embeddings
        payload["dino_model_name"] = unicode_scalar(dino_model_name)
        payload[DINO_SELF_KNN_KEY] = dino_self_knn
        if neg_dino is not None:
            payload["neg_dino_embeddings"] = neg_dino
    save_npz_atomic(args.output, payload)
    raise_if_cancelled(cancel_event, "taste profile rebuild cancelled")
    negatives = 0 if neg_embeddings is None else len(neg_embeddings)
    print(
        f"[INFO] Saved {len(kept_paths)} exemplars (+{negatives} negatives, "
        f"dino={'yes' if dino_embeddings is not None else 'no'}, "
        f"{len(calib_names)} calibration features) to {args.output}"
    )

    print_diagnostics(
        embeddings,
        [p.name for p in kept_paths],
        k=pipeline_settings.K_NEIGHBORS,
        neg_embeddings=neg_embeddings,
        calib_names=calib_names,
        calib_values=calib_values,
        dino_self_knn=dino_self_knn,
    )
    print(f"[INFO] Completed in {time.perf_counter() - started:.1f}s")
    elapsed = time.perf_counter() - started
    logger.info("Taste profile rebuild completed in %.1fs", elapsed)
    _emit_progress(
        progress_callback,
        stage="completed",
        substage=None,
        current_item=None,
        processed=len(kept_paths),
        total=len(kept_paths),
        message=f"Taste profile rebuild completed in {elapsed:.1f}s.",
    )
    return Path(args.output)


if __name__ == "__main__":
    main()
