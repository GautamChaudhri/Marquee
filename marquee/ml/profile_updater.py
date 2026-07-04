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

import hashlib
import logging
import re
from pathlib import Path

import numpy as np

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.artifact_codec import (
    decode_unicode_list,
    ensure_safe_artifact,
    load_npz_safe,
    save_npz_atomic,
    unicode_array,
)
from marquee.ml.namespaces import TasteNamespace, get_namespace

logger = logging.getLogger(__name__)

_YEAR_SUFFIX = re.compile(r"\s*\(\d{4}\)\s*$")
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
_HASH_ARRAY_KEY = "poster_sha256s"


def _profile_path(ns: TasteNamespace | None = None) -> Path:
    ns = ns or get_namespace("movies")
    return ns.profile_path


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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_exemplar_name(
    profile: dict[str, np.ndarray],
    training_dir: Path,
    source_hash: str,
) -> str | None:
    names = decode_unicode_list(profile["poster_names"])
    existing_hashes = (
        decode_unicode_list(profile[_HASH_ARRAY_KEY]) if _HASH_ARRAY_KEY in profile else []
    )
    if existing_hashes:
        for index, digest in enumerate(existing_hashes):
            if digest == source_hash:
                return names[index]

    for name in names:
        candidate = training_dir / name
        if candidate.is_file() and _file_sha256(candidate) == source_hash:
            return name
    return None


def _load_profile(ns: TasteNamespace | None = None) -> dict[str, np.ndarray]:
    ns = ns or get_namespace("movies")
    path = _profile_path(ns)
    if not path.exists():
        raise FileNotFoundError(f"Taste profile not found: {path}")
    ensure_safe_artifact(path, ns.artifact_kind_profile)
    with load_npz_safe(path) as data:
        return {key: data[key] for key in data.files}


def _save_profile(ns: TasteNamespace, payload: dict[str, np.ndarray]) -> None:
    save_npz_atomic(_profile_path(ns), payload)


def add_exemplar(
    *,
    source_image: Path,
    title: str,
    year: int | None,
    clip_embedding: np.ndarray,
    namespace: TasteNamespace | None = None,
    asset_kind: str = "movie",
) -> str:
    """Append a positive exemplar to the profile + training_data folder.

    Returns the training_data filename that was created (recorded on the
    label so an undo can remove exactly this exemplar).
    """
    from marquee.ml.calibration import CALIB_NAMES_KEY, CALIB_VALUES_KEY  # noqa: PLC0415

    ns = namespace or get_namespace("movies")
    profile = _load_profile(ns)

    # 1. Copy the image into training_data (source of truth).
    training_dir = ns.training_dirs.get(asset_kind)
    if not training_dir:
        training_dir = next(iter(ns.training_dirs.values()))
    training_dir.mkdir(parents=True, exist_ok=True)
    source_hash = _file_sha256(source_image)
    if existing := _existing_exemplar_name(profile, training_dir, source_hash):
        logger.info("PROFILE | exemplar already staged as %s; skipping duplicate add", existing)
        return existing
    suffix = source_image.suffix if source_image.suffix.lower() in _IMAGE_EXTENSIONS else ".jpg"
    target = _dedupe_target(training_dir, exemplar_filename(title, year, suffix))
    import shutil  # noqa: PLC0415

    shutil.copy2(source_image, target)

    # 2. Append the CLIP embedding (L2-normalized).
    vector = np.asarray(clip_embedding, dtype=np.float32).reshape(1, -1)
    vector /= np.maximum(np.linalg.norm(vector, axis=1, keepdims=True), 1e-10)
    embeddings = np.concatenate((profile["embeddings"], vector), axis=0)
    profile["embeddings"] = embeddings
    names = decode_unicode_list(profile["poster_names"])
    profile["poster_names"] = unicode_array([*names, target.name])

    # 2b. Append the asset kind
    if "asset_kinds" in profile:
        kinds = decode_unicode_list(profile["asset_kinds"])
    else:
        kinds = ["movie"] * len(names)
    profile["asset_kinds"] = unicode_array([*kinds, asset_kind])

    existing_hashes = (
        decode_unicode_list(profile[_HASH_ARRAY_KEY])
        if _HASH_ARRAY_KEY in profile
        else [
            _file_sha256(training_dir / name) if (training_dir / name).is_file() else ""
            for name in names
        ]
    )
    profile[_HASH_ARRAY_KEY] = unicode_array([*existing_hashes, source_hash])
    profile["centroid_emb"] = _compute_centroid(embeddings)

    # 3. Append a calibration column reindexed to the profile's feature names.
    if CALIB_NAMES_KEY in profile and CALIB_VALUES_KEY in profile:
        calib_names = decode_unicode_list(profile[CALIB_NAMES_KEY])
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

    _save_profile(ns, profile)
    logger.info(
        "PROFILE | added exemplar %s (now %d exemplars)",
        target.name,
        embeddings.shape[0],
    )
    return target.name


def remove_exemplar(poster_name: str, namespace: TasteNamespace | None = None) -> bool:
    """Remove an exemplar by training_data filename (undo). Returns True if found."""
    ns = namespace or get_namespace("movies")
    profile = _load_profile(ns)
    names = decode_unicode_list(profile["poster_names"])
    if poster_name not in names:
        return False
    index = names.index(poster_name)

    keep = [i for i in range(len(names)) if i != index]
    profile["embeddings"] = profile["embeddings"][keep]
    profile["poster_names"] = unicode_array([names[i] for i in keep])
    if "asset_kinds" in profile:
        kinds = decode_unicode_list(profile["asset_kinds"])
        profile["asset_kinds"] = unicode_array([kinds[i] for i in keep])
    if _HASH_ARRAY_KEY in profile:
        hashes = decode_unicode_list(profile[_HASH_ARRAY_KEY])
        profile[_HASH_ARRAY_KEY] = unicode_array([hashes[i] for i in keep])
    profile["centroid_emb"] = _compute_centroid(profile["embeddings"])

    from marquee.ml.calibration import CALIB_VALUES_KEY  # noqa: PLC0415

    if CALIB_VALUES_KEY in profile:
        profile[CALIB_VALUES_KEY] = np.asarray(profile[CALIB_VALUES_KEY])[:, keep]

    if "dino_embeddings" in profile:
        profile["dino_embeddings"] = profile["dino_embeddings"][keep]
        _recompute_dino_self_knn(profile)

    _save_profile(ns, profile)

    # Remove the training_data copy from whichever dir contains it.
    for training_dir in ns.training_dirs.values():
        target = training_dir / poster_name
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
