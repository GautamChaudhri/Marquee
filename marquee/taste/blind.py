"""The mask: a taste profile without one movie's exemplars, for a masked run.

A masked run scores one movie against the active profile with every active exemplar of that
movie left out, positives and negatives. The engine is not changed for it: this module reads
the profile file and writes a copy without the named exemplars, which the unchanged
:class:`marquee.ml.taste_store.NumpyTasteStore` loads.

What a profile file holds (``taste/trainer.py::_run_build`` writes it,
``ml/taste_store.py::_ensure_loaded`` reads it), and what the mask does with each array:

=========================== ================ ====================================
array                       shape            masked
=========================== ================ ====================================
``embeddings``              (P, 512)         rows dropped
``poster_names``            (P,)             rows dropped (the positives' names)
``asset_kinds``             (P,)             rows dropped
``poster_sha256s``          (P,)             rows dropped
``embedding_weights``       (P,)             rows dropped
``series_titles``           (P,)             rows dropped (TV only)
``season_numbers``          (P,)             rows dropped (TV only)
``dino_embeddings``         (P, 384)         rows dropped
``dino_self_knn``           (P,)             rows dropped; the others keep their values
``calib_feature_values``    (F, P)           columns dropped; one column per positive
``neg_embeddings``          (N, 512)         rows dropped
``neg_poster_names``        (N,)             rows dropped (the negatives' names)
``neg_embedding_weights``   (N,)             rows dropped
``neg_dino_embeddings``     (N, 384)         rows dropped
``centroid_emb``            (512,)           recomputed with the trainer's ``compute_centroid``
``model_name``              scalar           kept
``dino_model_name``         scalar           kept
``calib_feature_names``     (F,)             kept
=========================== ================ ====================================

Positives are named by the exemplar's ``staged_name`` and negatives by its ``file_name``,
as :func:`marquee.taste.rebuild.plan_build` stages them. An array this module does not know
is refused rather than copied unmasked. What stays approximate, and is accepted: the other
exemplars' DINO self-kNN values and calibration samples were computed with the dropped
ones present.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from marquee.config import settings
from marquee.ml.artifact_codec import decode_unicode_list, save_npz_atomic

logger = logging.getLogger(__name__)

POSITIVE_ROWS = (
    "embeddings",
    "poster_names",
    "asset_kinds",
    "poster_sha256s",
    "embedding_weights",
    "series_titles",
    "season_numbers",
    "dino_embeddings",
    "dino_self_knn",
)
NEGATIVE_ROWS = (
    "neg_embeddings",
    "neg_poster_names",
    "neg_embedding_weights",
    "neg_dino_embeddings",
)
CALIBRATION_VALUES = "calib_feature_values"
CENTROID = "centroid_emb"
KEPT_AS_IS = ("model_name", "dino_model_name", "calib_feature_names")
KNOWN = frozenset((*POSITIVE_ROWS, *NEGATIVE_ROWS, CALIBRATION_VALUES, CENTROID, *KEPT_AS_IS))


class MaskError(ValueError):
    """The profile cannot be masked as asked; the message says why."""


@dataclass(frozen=True)
class Masked:
    """What a masked profile left out and kept."""

    path: Path
    positives_dropped: int
    negatives_dropped: int
    positives: int
    negatives: int


def blind_profile_path(profile_sha256: str, movie_id: int) -> Path:
    """Where a masked run of ``movie_id`` against the profile ``profile_sha256`` keeps its
    derived profile while it runs."""
    if len(profile_sha256) != 64 or any(c not in "0123456789abcdef" for c in profile_sha256):
        raise MaskError(f"{profile_sha256!r} is not a profile's SHA-256")
    if movie_id < 1:
        raise MaskError(f"{movie_id} is not a movie id")
    return settings.blind_profiles_dir / f"{profile_sha256}-{movie_id}.npz"


def _keep_mask(names: list[str], dropped: set[str], what: str) -> np.ndarray:
    unknown = sorted(dropped - set(names))
    if unknown:
        raise MaskError(f"the profile has no {what} named {unknown[0]!r}")
    return np.asarray([name not in dropped for name in names], dtype=bool)


def mask_profile(
    source: Path,
    destination: Path,
    *,
    positives: Iterable[str] = (),
    negatives: Iterable[str] = (),
) -> Masked:
    """Write ``source`` without the named exemplars to ``destination``. Blocking.

    ``positives`` are names in ``poster_names`` (staged names), ``negatives`` names in
    ``neg_poster_names`` (kept file names). A name the profile does not hold is an error,
    and so is masking every positive. The file is written under a temporary name and
    renamed.
    """
    from marquee.taste.trainer import compute_centroid

    drop_positive, drop_negative = set(positives), set(negatives)
    with np.load(source, allow_pickle=False) as data:
        arrays = {key: data[key] for key in data.files}
    unknown = sorted(set(arrays) - KNOWN)
    if unknown:
        raise MaskError(f"the profile holds arrays the mask does not know: {unknown}")

    names = decode_unicode_list(arrays["poster_names"])
    keep = _keep_mask(names, drop_positive, "positive")
    if not keep.any():
        raise MaskError("masking would leave the profile without a positive exemplar")
    out: dict[str, np.ndarray] = {}
    for key in POSITIVE_ROWS:
        if key in arrays:
            if arrays[key].shape[:1] != (len(names),):
                raise MaskError(f"{key} does not hold one row per positive")
            out[key] = arrays[key][keep]
    if CALIBRATION_VALUES in arrays:
        values = arrays[CALIBRATION_VALUES]
        if values.ndim != 2 or values.shape[1] != len(names):
            raise MaskError(f"{CALIBRATION_VALUES} does not hold one column per positive")
        out[CALIBRATION_VALUES] = values[:, keep]

    negative_names = (
        decode_unicode_list(arrays["neg_poster_names"]) if "neg_poster_names" in arrays else []
    )
    keep_negative = _keep_mask(negative_names, drop_negative, "negative")
    negatives_left = int(keep_negative.sum())
    for key in NEGATIVE_ROWS:
        if key not in arrays:
            continue
        if arrays[key].shape[:1] != (len(negative_names),):
            raise MaskError(f"{key} does not hold one row per negative")
        if negatives_left:
            out[key] = arrays[key][keep_negative]
        # With none left the arrays go, as the trainer writes a profile without negatives.

    for key in KEPT_AS_IS:
        if key in arrays:
            out[key] = arrays[key]
    weights = out.get("embedding_weights")
    out[CENTROID] = compute_centroid(
        np.asarray(out["embeddings"], dtype=np.float32),
        None if weights is None else np.asarray(weights, dtype=np.float32),
    )
    save_npz_atomic(destination, out)
    masked = Masked(
        path=destination,
        positives_dropped=int((~keep).sum()),
        negatives_dropped=len(negative_names) - negatives_left,
        positives=int(keep.sum()),
        negatives=negatives_left,
    )
    logger.info(
        "MASK | %s: %d positive(s) and %d negative(s) left out; %d and %d kept",
        destination.name,
        masked.positives_dropped,
        masked.negatives_dropped,
        masked.positives,
        masked.negatives,
    )
    return masked
