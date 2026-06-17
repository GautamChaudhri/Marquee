"""3D/2D taste-map projection + clustering for the visualization (design 11).

Reduces the taste profile's CLIP embeddings to 3D (and 2D) for an interactive
scatter, detects clusters, and projects live pipeline candidates into the same
space by k-NN barycentric placement (the geometric picture of ``knn_sim``).

Dependency posture (design 11 decision 2):
  - UMAP (``umap-learn``) when installed — meaningful local+global structure.
  - PCA (hand-rolled numpy SVD) otherwise — zero dependency, deterministic.
  - HDBSCAN (``sklearn.cluster``) when installed — else no clustering.

The map is cached at ``data/cache/taste_map.{model}.npz`` and rebuilt when the
profile file is newer (so feedback-loop exemplar appends invalidate it for
free). The previous map is archived to ``data/cache/taste_map_history/``.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from marquee.config import settings
from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.artifact_codec import (
    GENRES_JSON_KEY,
    decode_json_string_array,
    decode_unicode_list,
    decode_unicode_scalar,
    ensure_safe_artifact,
    json_string_array,
    load_npz_safe,
    save_npz_atomic,
    unicode_array,
    unicode_scalar,
)
from marquee.ml.calibration import CALIB_NAMES_KEY, CALIB_VALUES_KEY
from marquee.ml.taste_store import weighted_topk_mean

logger = logging.getLogger(__name__)

_SMALL_PROFILE = 50  # below this, clustering isn't meaningful


def _map_path() -> Path:
    return settings.poster_cache_path.parent / f"taste_map.{pipeline_settings.AI_MODEL}.npz"


def _history_dir() -> Path:
    return settings.poster_cache_path.parent / "taste_map_history"


def _thumbs_dir() -> Path:
    return settings.poster_cache_path.parent / "taste_thumbs"


# ---------------------------------------------------------------------------
# Dimensionality reduction
# ---------------------------------------------------------------------------


def _pca(matrix: np.ndarray, n: int) -> np.ndarray:
    """Deterministic PCA scores via SVD (no sklearn dependency)."""
    centered = matrix - matrix.mean(axis=0)
    _u, s, vt = np.linalg.svd(centered, full_matrices=False)
    components = vt[:n]
    return (centered @ components.T).astype(np.float32)


def _reduce(matrix: np.ndarray, n: int) -> tuple[np.ndarray, str]:
    try:
        import umap  # noqa: PLC0415

        reducer = umap.UMAP(
            n_components=n,
            n_neighbors=min(15, max(2, matrix.shape[0] - 1)),
            min_dist=0.1,
            metric="cosine",
            random_state=42,
        )
        return reducer.fit_transform(matrix).astype(np.float32), "umap"
    except ImportError:
        return _pca(matrix, n), "pca"


def _cluster(coords: np.ndarray) -> np.ndarray | None:
    if coords.shape[0] < _SMALL_PROFILE:
        return None
    try:
        from sklearn.cluster import HDBSCAN  # noqa: PLC0415
    except ImportError:
        return None
    min_size = max(5, coords.shape[0] // 30)
    return HDBSCAN(min_cluster_size=min_size).fit_predict(coords).astype(np.int64)


def _cluster_names(labels: np.ndarray | None, genres: list | None) -> dict[int, str]:
    if labels is None:
        return {}
    names: dict[int, str] = {}
    for cluster_id in sorted({int(label) for label in labels}):
        if cluster_id == -1:
            continue
        members = [i for i, label in enumerate(labels) if int(label) == cluster_id]
        size = len(members)
        if genres is not None:
            genre_counts: Counter[str] = Counter()
            for i in members:
                for genre in (genres[i] or []):
                    genre_counts[genre] += 1
            top = [g for g, _ in genre_counts.most_common(2)]
            label_text = "/".join(top) if top else "Mixed"
            names[cluster_id] = f"{label_text} ({size})"
        else:
            names[cluster_id] = f"Cluster {cluster_id} ({size})"
    return names


def _self_knn(embeddings: np.ndarray, k: int) -> np.ndarray:
    sims = embeddings @ embeddings.T
    np.fill_diagonal(sims, -np.inf)
    return np.asarray(
        [weighted_topk_mean(row[np.isfinite(row)], k, weighting="mean") for row in sims],
        dtype=np.float64,
    )


# ---------------------------------------------------------------------------
# Profile access
# ---------------------------------------------------------------------------


def _load_profile_arrays() -> dict:
    path = Path(pipeline_settings.TASTE_PROFILE_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Taste profile not found: {path}")
    ensure_safe_artifact(path, "taste_profile")
    with load_npz_safe(path) as data:
        result = {
            "embeddings": np.asarray(data["embeddings"], dtype=np.float32),
            "poster_names": decode_unicode_list(data["poster_names"]),
            "mtime": path.stat().st_mtime,
        }
        if GENRES_JSON_KEY in data.files:
            result["genres"] = decode_json_string_array(data[GENRES_JSON_KEY])
        for key in ("years", "tmdb_ids"):
            if key in data.files:
                result[key] = data[key].tolist()
        if "neg_embeddings" in data.files:
            result["neg_embeddings"] = np.asarray(data["neg_embeddings"], dtype=np.float32)
        # Per-exemplar aesthetic / colorfulness from the calibration arrays.
        if CALIB_NAMES_KEY in data.files and CALIB_VALUES_KEY in data.files:
            names = decode_unicode_list(data[CALIB_NAMES_KEY])
            values = np.asarray(data[CALIB_VALUES_KEY], dtype=np.float64)
            for feat in ("aesthetic", "global_colorfulness"):
                if feat in names:
                    result[feat] = values[names.index(feat)].tolist()
    return result


# ---------------------------------------------------------------------------
# Build / load
# ---------------------------------------------------------------------------


def build_map() -> dict:
    """Project the profile, cluster, generate thumbnails, save atomically."""
    profile = _load_profile_arrays()
    embeddings = profile["embeddings"]
    # L2-normalize for cosine-correct projection + barycentric placement.
    embeddings = embeddings / np.maximum(
        np.linalg.norm(embeddings, axis=1, keepdims=True), 1e-10
    )
    n = embeddings.shape[0]

    coords_3d, method = _reduce(embeddings, 3)
    coords_2d, _ = _reduce(embeddings, 2)
    labels = _cluster(coords_3d)
    genres = profile.get("genres")
    names_map = _cluster_names(labels, genres)
    self_knn = _self_knn(embeddings, pipeline_settings.K_NEIGHBORS)

    # Archive the previous map before overwriting.
    map_path = _map_path()
    if map_path.exists():
        history = _history_dir()
        history.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        (history / f"{stamp}.npz").write_bytes(map_path.read_bytes())

    payload: dict = {
        "coords_3d": coords_3d,
        "coords_2d": coords_2d,
        "poster_names": unicode_array(profile["poster_names"]),
        "self_knn": self_knn,
        "projection_method": unicode_scalar(method),
        "profile_mtime": np.float64(profile["mtime"]),
        "computed_at": unicode_scalar(datetime.now(UTC).isoformat()),
    }
    if labels is not None:
        payload["cluster_labels"] = labels
        payload["cluster_names"] = unicode_array(
            [f"{cid}:{name}" for cid, name in names_map.items()]
        )
    if "genres" in profile:
        payload[GENRES_JSON_KEY] = json_string_array(profile["genres"])
    for key in ("years", "tmdb_ids"):
        if key in profile:
            payload[key] = np.asarray(profile[key], dtype=np.int64)
    for key in ("aesthetic", "global_colorfulness"):
        if key in profile:
            payload[key] = np.asarray(profile[key], dtype=np.float64)

    save_npz_atomic(map_path, payload)

    _generate_thumbnails(profile["poster_names"])
    logger.info("TASTE MAP | built %d points (%s), %d clusters", n, method, len(names_map))
    return load_map()


def _is_stale() -> bool:
    map_path = _map_path()
    if not map_path.exists():
        return True
    try:
        ensure_safe_artifact(map_path, "taste_map")
        with load_npz_safe(map_path) as data:
            map_mtime = float(np.asarray(data["profile_mtime"]).item())
    except Exception:
        return True
    profile_path = Path(pipeline_settings.TASTE_PROFILE_PATH)
    return profile_path.exists() and profile_path.stat().st_mtime > map_mtime + 1e-6


def load_map(recompute: bool = False) -> dict:
    """Return the cached map as a JSON-ready dict, rebuilding if stale."""
    if recompute or _is_stale():
        return build_map()

    ensure_safe_artifact(_map_path(), "taste_map")
    with load_npz_safe(_map_path()) as data:
        names = decode_unicode_list(data["poster_names"])
        coords_3d = data["coords_3d"]
        coords_2d = data["coords_2d"]
        labels = data["cluster_labels"].tolist() if "cluster_labels" in data.files else None
        cluster_names = (
            decode_unicode_list(data["cluster_names"])
            if "cluster_names" in data.files
            else []
        )
        self_knn = data["self_knn"].tolist()
        genres = (
            decode_json_string_array(data[GENRES_JSON_KEY])
            if GENRES_JSON_KEY in data.files
            else None
        )
        years = data["years"].tolist() if "years" in data.files else None
        aesthetic = data["aesthetic"].tolist() if "aesthetic" in data.files else None
        colorfulness = (
            data["global_colorfulness"].tolist()
            if "global_colorfulness" in data.files
            else None
        )
        method = decode_unicode_scalar(data["projection_method"])
        computed_at = decode_unicode_scalar(data["computed_at"])

    points = []
    for i, name in enumerate(names):
        points.append(
            {
                "name": name,
                "x": float(coords_3d[i][0]),
                "y": float(coords_3d[i][1]),
                "z": float(coords_3d[i][2]),
                "x2": float(coords_2d[i][0]),
                "y2": float(coords_2d[i][1]),
                "cluster": int(labels[i]) if labels is not None else None,
                "self_knn": float(self_knn[i]),
                "genres": genres[i] if genres is not None else None,
                "year": years[i] if years is not None else None,
                "aesthetic": aesthetic[i] if aesthetic is not None else None,
                "colorfulness": colorfulness[i] if colorfulness is not None else None,
                "thumb_url": f"/api/taste/exemplars/{name}/image?size=thumb",
            }
        )

    # Outliers: bottom 5th percentile of self-knn (profile-curation aid).
    outliers: list[str] = []
    if self_knn:
        threshold = float(np.percentile(self_knn, 5))
        outliers = [names[i] for i, v in enumerate(self_knn) if v <= threshold]

    clusters = []
    note = None
    if labels is not None:
        parsed = {}
        for entry in cluster_names:
            cid, _, label = entry.partition(":")
            parsed[int(cid)] = label
        sizes = Counter(int(label) for label in labels if int(label) != -1)
        clusters = [
            {"id": cid, "name": parsed.get(cid, str(cid)), "size": size}
            for cid, size in sorted(sizes.items())
        ]
    elif len(names) < _SMALL_PROFILE:
        note = "Add more approved posters to reveal visual taste clusters."
    else:
        note = "Install the 'viz' extra (umap-learn/scikit-learn) for clustering."

    return {
        "projection": {"method": method, "computed_at": computed_at},
        "points": points,
        "clusters": clusters,
        "outliers": outliers,
        "clustering": clusters or None,
        "note": note,
    }


# ---------------------------------------------------------------------------
# Candidate projection (barycentric)
# ---------------------------------------------------------------------------


def project(embeddings: np.ndarray, k: int | None = None) -> list[dict]:
    """k-NN barycentric placement of candidate embeddings into the map space."""
    k = k or pipeline_settings.K_NEIGHBORS
    if _is_stale():
        build_map()

    profile = _load_profile_arrays()
    profile_emb = profile["embeddings"]
    profile_emb = profile_emb / np.maximum(
        np.linalg.norm(profile_emb, axis=1, keepdims=True), 1e-10
    )
    names = profile["poster_names"]
    ensure_safe_artifact(_map_path(), "taste_map")
    with load_npz_safe(_map_path()) as data:
        coords = np.asarray(data["coords_3d"], dtype=np.float64)

    temp = pipeline_settings.KNN_SOFTMAX_TEMP
    results = []
    for embedding in np.atleast_2d(embeddings).astype(np.float32):
        norm = embedding / max(float(np.linalg.norm(embedding)), 1e-10)
        sims = profile_emb @ norm
        count = min(max(k, 1), sims.shape[0])
        top_idx = np.argsort(sims)[::-1][:count]
        top_sims = sims[top_idx]
        logits = (top_sims - top_sims.max()) / temp
        weights = np.exp(logits)
        weights /= weights.sum()
        position = weights @ coords[top_idx]
        results.append(
            {
                "x": float(position[0]),
                "y": float(position[1]),
                "z": float(position[2]),
                "knn_sim": float(np.dot(weights, top_sims)),
                "neighbors": [
                    {"name": names[i], "similarity": float(sims[i])} for i in top_idx
                ],
            }
        )
    return results


def neighbors_of(poster_name: str, k: int | None = None) -> list[dict] | None:
    """The k nearest exemplars to a given exemplar (click-to-explore)."""
    k = k or pipeline_settings.K_NEIGHBORS
    profile = _load_profile_arrays()
    names = profile["poster_names"]
    if poster_name not in names:
        return None
    embeddings = profile["embeddings"]
    embeddings = embeddings / np.maximum(
        np.linalg.norm(embeddings, axis=1, keepdims=True), 1e-10
    )
    index = names.index(poster_name)
    sims = embeddings @ embeddings[index]
    sims[index] = -np.inf
    count = min(max(k, 1), sims.shape[0] - 1)
    top_idx = np.argsort(sims)[::-1][:count]
    return [{"name": names[i], "similarity": float(sims[i])} for i in top_idx]


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------


def _generate_thumbnails(poster_names: list[str]) -> None:
    from PIL import Image  # noqa: PLC0415

    training_dir = Path(pipeline_settings.TRAINING_DATA_DIR)
    thumbs = _thumbs_dir()
    thumbs.mkdir(parents=True, exist_ok=True)
    for name in poster_names:
        thumb = thumbs / f"{name}.webp"
        if thumb.exists():
            continue
        source = training_dir / name
        if not source.is_file():
            continue
        try:
            with Image.open(source) as image:
                image = image.convert("RGB")
                ratio = 192 / image.height
                image = image.resize((max(1, int(image.width * ratio)), 192))
                image.save(thumb, "WEBP", quality=80)
        except Exception as exc:  # noqa: BLE001
            logger.warning("thumb failed for %s: %s", name, exc)


def thumbnail_path(poster_name: str) -> Path | None:
    thumb = _thumbs_dir() / f"{poster_name}.webp"
    return thumb if thumb.is_file() else None


def exemplar_source(poster_name: str) -> Path | None:
    source = Path(pipeline_settings.TRAINING_DATA_DIR) / poster_name
    return source if source.is_file() else None
