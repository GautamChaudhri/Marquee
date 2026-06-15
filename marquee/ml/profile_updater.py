"""Incremental taste-profile maintenance — add/remove a single exemplar.

Approving or overriding appends the selected poster to the taste profile
*without* a full rebuild: the poster is copied into ``training_data/`` (the
human-auditable source of truth), and its CLIP embedding, optional DINOv2
embedding, and one calibration column are appended to the ``.npz``. The
centroid and DINO self-kNN range are recomputed (microseconds at this scale)
and the file is written atomically.

A full ``taste_trainer`` rebuild remains the consistency anchor; this keeps
the profile current between rebuilds. ``remove_exemplar`` reverses an add
(for feedback undo).

OCR title-geometry calibration is skipped for incremental adds (it would add
an OCR worker spawn for one image); those columns are NaN, which the KDE
calibration already tolerates. A full rebuild fills them in.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import numpy as np

from marquee.core.pipeline_config import pipeline_settings

logger = logging.getLogger(__name__)

_YEAR_SUFFIX = re.compile(r"\s*\(\d{4}\)\s*$")
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}


def _profile_path() -> Path:
    return Path(pipeline_settings.TASTE_PROFILE_PATH)


def exemplar_filename(title: str, year: int | None, suffix: str = ".jpg") -> str:
    """'Die Hard', 1988 -> 'Die Hard (1988).jpg', sanitized for the FS."""
    base = title.strip()
    if year:
        base = f"{base} ({year})"
    base = re.sub(r'[<>:"/\\|?*]', "_", base).strip()
    return f"{base}{suffix}"


def _dedupe_target(directory: Path, filename: str) -> Path:
    """Return a non-colliding path: 'name.jpg', 'name - 2.jpg', ..."""
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = directory / filename
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem} - {counter}{suffix}"
        counter += 1
    return candidate


def _compute_centroid(embeddings: np.ndarray) -> np.ndarray:
    centroid = embeddings.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    return (centroid / norm if norm > 1e-10 else centroid).astype(np.float32)


def _load_profile() -> dict[str, np.ndarray]:
    path = _profile_path()
    if not path.exists():
        raise FileNotFoundError(f"Taste profile not found: {path}")
    with np.load(path, allow_pickle=True) as data:
        return {key: data[key] for key in data.files}


def _save_profile(payload: dict[str, np.ndarray]) -> None:
    path = _profile_path()
    # tmp ends in .npz so np.savez writes it verbatim (no extension append).
    tmp = path.with_name(path.name + ".tmp.npz")
    np.savez(tmp, **payload)
    os.replace(tmp, path)


def add_exemplar(
    *,
    source_image: Path,
    title: str,
    year: int | None,
    clip_embedding: np.ndarray,
) -> str:
    """Append a positive exemplar to the profile + training_data folder.

    Returns the training_data filename that was created (recorded on the
    label so an undo can remove exactly this exemplar).
    """
    from marquee.ml.calibration import CALIB_NAMES_KEY, CALIB_VALUES_KEY  # noqa: PLC0415

    profile = _load_profile()

    # 1. Copy the image into training_data (source of truth).
    training_dir = Path(pipeline_settings.TRAINING_DATA_DIR)
    training_dir.mkdir(parents=True, exist_ok=True)
    suffix = source_image.suffix if source_image.suffix.lower() in _IMAGE_EXTENSIONS else ".jpg"
    target = _dedupe_target(training_dir, exemplar_filename(title, year, suffix))
    import shutil  # noqa: PLC0415

    shutil.copy2(source_image, target)

    # 2. Append the CLIP embedding (L2-normalized).
    vector = np.asarray(clip_embedding, dtype=np.float32).reshape(1, -1)
    vector /= np.maximum(np.linalg.norm(vector, axis=1, keepdims=True), 1e-10)
    embeddings = np.concatenate((profile["embeddings"], vector), axis=0)
    profile["embeddings"] = embeddings
    profile["poster_names"] = np.asarray(
        [*profile["poster_names"].tolist(), target.name], dtype=object
    )
    profile["centroid_emb"] = _compute_centroid(embeddings)

    # 3. Append a calibration column reindexed to the profile's feature names.
    if CALIB_NAMES_KEY in profile and CALIB_VALUES_KEY in profile:
        calib_names = [str(n) for n in profile[CALIB_NAMES_KEY].tolist()]
        measured = _measure_single(target, vector[0])
        column = np.asarray(
            [measured.get(name, np.nan) for name in calib_names], dtype=np.float64
        ).reshape(-1, 1)
        profile[CALIB_VALUES_KEY] = np.concatenate(
            (np.asarray(profile[CALIB_VALUES_KEY], dtype=np.float64), column), axis=1
        )

    # 4. Append the DINOv2 embedding + recompute self-kNN (if the profile has it).
    if "dino_embeddings" in profile:
        _append_dino(profile, target)

    _save_profile(profile)
    logger.info(
        "PROFILE | added exemplar %s (now %d exemplars)",
        target.name,
        embeddings.shape[0],
    )
    return target.name


def remove_exemplar(poster_name: str) -> bool:
    """Remove an exemplar by training_data filename (undo). Returns True if found."""
    profile = _load_profile()
    names = [str(n) for n in profile["poster_names"].tolist()]
    if poster_name not in names:
        return False
    index = names.index(poster_name)

    keep = [i for i in range(len(names)) if i != index]
    profile["embeddings"] = profile["embeddings"][keep]
    profile["poster_names"] = np.asarray([names[i] for i in keep], dtype=object)
    profile["centroid_emb"] = _compute_centroid(profile["embeddings"])

    from marquee.ml.calibration import CALIB_VALUES_KEY  # noqa: PLC0415

    if CALIB_VALUES_KEY in profile:
        profile[CALIB_VALUES_KEY] = np.asarray(profile[CALIB_VALUES_KEY])[:, keep]

    if "dino_embeddings" in profile:
        profile["dino_embeddings"] = profile["dino_embeddings"][keep]
        _recompute_dino_self_knn(profile)

    _save_profile(profile)

    # Remove the training_data copy.
    target = Path(pipeline_settings.TRAINING_DATA_DIR) / poster_name
    if target.exists():
        target.unlink()
    logger.info("PROFILE | removed exemplar %s", poster_name)
    return True


# ---------------------------------------------------------------------------
# ML helpers (lazy — keep this module importable without ML extras)
# ---------------------------------------------------------------------------


def _measure_single(image_path: Path, clip_embedding: np.ndarray) -> dict[str, float]:
    """Measure the calibratable features for one exemplar (no OCR)."""
    from marquee.ml.aesthetic import AestheticPredictor  # noqa: PLC0415
    from marquee.ml.face import FaceDetector  # noqa: PLC0415
    from marquee.ml.person import PersonDetector  # noqa: PLC0415
    from marquee.ml.taste_trainer import measure_exemplar_features  # noqa: PLC0415
    from marquee.ml.zeroshot import ZeroShotAxes  # noqa: PLC0415

    face = FaceDetector()
    if not face.model_path.exists():
        face = None
    person = PersonDetector()
    if not person.available:
        person = None

    names, matrix = measure_exemplar_features(
        [image_path],
        clip_embedding.reshape(1, -1),
        aesthetic=AestheticPredictor(),
        axes=ZeroShotAxes.load(),
        face_detector=face,
        person_detector=person,
        run_ocr=False,
    )
    return {name: float(matrix[i, 0]) for i, name in enumerate(names)}


def _append_dino(profile: dict[str, np.ndarray], image_path: Path) -> None:
    from PIL import Image  # noqa: PLC0415

    from marquee.ml.dino import DinoImageEncoder, preprocess_dino  # noqa: PLC0415

    encoder = DinoImageEncoder()
    if not encoder.available:
        return
    with Image.open(image_path) as image:
        pixels = preprocess_dino(image.convert("RGB"))
    vector = encoder.encode_batch([pixels])[0].reshape(1, -1)
    profile["dino_embeddings"] = np.concatenate(
        (profile["dino_embeddings"], vector.astype(np.float32)), axis=0
    )
    _recompute_dino_self_knn(profile)


def _recompute_dino_self_knn(profile: dict[str, np.ndarray]) -> None:
    from marquee.ml.taste_store import DINO_SELF_KNN_KEY  # noqa: PLC0415
    from marquee.ml.taste_trainer import compute_dino_self_knn  # noqa: PLC0415

    profile[DINO_SELF_KNN_KEY] = compute_dino_self_knn(
        np.asarray(profile["dino_embeddings"], dtype=np.float32),
        pipeline_settings.K_NEIGHBORS,
    )
