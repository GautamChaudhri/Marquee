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
import re
import threading
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from marquee.core.cancellation import raise_if_cancelled
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.runtime_settings import effective_settings as settings
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
from marquee.ml.namespaces import TasteNamespace, get_namespace
from marquee.ml.taste_store import weighted_topk_mean

logger = logging.getLogger(__name__)

_SMALL_PROFILE = 50  # below this, clustering isn't meaningful
_YEAR = re.compile(r"\((\d{4})\)")
_YEAR_SUFFIX = re.compile(r"\s*\(\d{4}\)\s*$")
_DEDUP_SUFFIX = re.compile(r"\s*-\s*\d+$")
_TV_SUFFIX = re.compile(r"-(?:show|season(\d+))$", re.IGNORECASE)


def _map_path(ns: TasteNamespace | None = None) -> Path:
    ns = ns or get_namespace("movies")
    if ns.library == "movies":
        return settings.poster_cache_path.parent / f"taste_map.{pipeline_settings.AI_MODEL}.npz"
    return settings.poster_cache_path.parent / f"taste_map.tv.{pipeline_settings.AI_MODEL}.npz"


def _history_dir(ns: TasteNamespace | None = None) -> Path:
    ns = ns or get_namespace("movies")
    return ns.map_history_dir


def _thumbs_dir(ns: TasteNamespace | None = None) -> Path:
    ns = ns or get_namespace("movies")
    if ns.library == "movies":
        return settings.poster_cache_path.parent / "taste_thumbs"
    return settings.poster_cache_path.parent / "taste_thumbs_tv"


def _parse_name(name: str) -> tuple[str, int | None]:
    stem = Path(name).stem
    year_match = _YEAR.search(stem)
    year = int(year_match.group(1)) if year_match else None
    title = _YEAR_SUFFIX.sub("", stem).strip()
    title = _DEDUP_SUFFIX.sub("", title).strip()
    return title, year


def _parse_tv_name(name: str) -> tuple[str, str, int | None]:
    """Series title, asset kind, and season number from a TV exemplar filename.

    Library-scanned TV artwork is named ``<series>-show.jpg`` / ``<series>-seasonNN.jpg``.
    Profiles built before the identity arrays existed carry only these names, so the
    filename stays a usable fallback for the whole TV lineage.
    """
    stem = Path(name).stem
    match = _TV_SUFFIX.search(stem)
    if match is None:
        return _DEDUP_SUFFIX.sub("", stem).strip(), "show", None
    series = _DEDUP_SUFFIX.sub("", stem[: match.start()]).strip()
    season = match.group(1)
    return (series, "season", int(season)) if season is not None else (series, "show", None)


def _poster_url(
    kind: str,
    *,
    movie_id: int | None,
    series_id: int | None,
    season_id: int | None,
) -> str | None:
    """Return the live library artwork route for a resolved map point."""
    if kind == "season" and season_id:
        return f"/api/library/seasons/{season_id}/poster"
    if kind == "show" and series_id:
        return f"/api/library/series/{series_id}/poster"
    if kind == "movie" and movie_id:
        return f"/api/library/movies/{movie_id}/poster"
    return None


def _resolve_profile_movie_rows(profile: dict) -> list[dict]:
    import psycopg  # noqa: PLC0415

    names: list[str] = profile["poster_names"]
    years = profile.get("years") or [None] * len(names)
    tmdb_ids = profile.get("tmdb_ids") or [None] * len(names)
    movie_ids = profile.get("movie_ids") or [None] * len(names)
    movie_titles = profile.get("movie_titles") or [None] * len(names)
    rows = []
    for index, name in enumerate(names):
        title, parsed_year = _parse_name(name)
        rows.append(
            {
                "title": movie_titles[index] or title,
                "year": years[index] or parsed_year,
                "tmdb_id": tmdb_ids[index] if index < len(tmdb_ids) else None,
                "movie_id": movie_ids[index] if index < len(movie_ids) else None,
                "movie_title": movie_titles[index] or title,
            }
        )

    db_url = settings.db_url_resolved.replace("postgresql+asyncpg://", "postgresql://", 1)
    by_id: dict[int, tuple[int, str, int | None, int | None]] = {}
    by_tmdb: dict[int, tuple[int, str, int | None, int | None]] = {}
    by_title: dict[str, list[tuple[int, str, int | None, int | None]]] = {}
    try:
        with psycopg.connect(db_url) as conn, conn.cursor() as cursor:
            cursor.execute("SELECT id, title, year, tmdb_id FROM movies")
            for movie_id, title, year, tmdb_id in cursor:
                by_id[int(movie_id)] = (int(movie_id), str(title), year, tmdb_id)
                if tmdb_id:
                    by_tmdb[int(tmdb_id)] = (int(movie_id), str(title), year, tmdb_id)
                by_title.setdefault(str(title).lower(), []).append(
                    (int(movie_id), str(title), year, tmdb_id)
                )
    except Exception:  # noqa: BLE001
        logger.warning("Could not resolve taste-map movie metadata from PostgreSQL", exc_info=True)
        return rows

    for row in rows:
        resolved = None
        if row["movie_id"]:
            resolved = by_id.get(int(row["movie_id"]))
        if row["tmdb_id"]:
            resolved = by_tmdb.get(int(row["tmdb_id"]))
        if resolved is None:
            candidates = by_title.get(str(row["title"]).lower(), [])
            if row["year"] is not None:
                resolved = next((item for item in candidates if item[2] == row["year"]), None)
            if resolved is None and len(candidates) == 1:
                resolved = candidates[0]
        if resolved is not None:
            row["movie_id"] = resolved[0]
            row["movie_title"] = resolved[1]
            row["year"] = row["year"] or resolved[2]
            row["tmdb_id"] = row["tmdb_id"] or resolved[3]
    return rows


def _tv_identity(profile: dict) -> list[tuple[str, str, int | None]]:
    """Per-exemplar (series title, asset kind, season number).

    The stored identity arrays win where a build recorded them; the filename is the
    fallback so profiles built before those arrays existed still place seasons.
    """
    names: list[str] = profile["poster_names"]
    kinds = profile.get("asset_kinds") or []
    titles = profile.get("series_titles") or []
    seasons = profile.get("season_numbers") or []
    identity: list[tuple[str, str, int | None]] = []
    for index, name in enumerate(names):
        parsed_title, parsed_kind, parsed_season = _parse_tv_name(name)
        kind = str(kinds[index]) if index < len(kinds) and kinds[index] else parsed_kind
        title = str(titles[index]) if index < len(titles) and titles[index] else parsed_title
        season = seasons[index] if index < len(seasons) else None
        if season is None or int(season) < 0:
            season = parsed_season
        identity.append((title, kind, None if season is None else int(season)))
    return identity


def _resolve_profile_series_rows(profile: dict) -> list[dict]:
    """Same shape as the movie rows, resolved against the series/season tables.

    TV exemplars carry a series title rather than an id, so the ids the detail panel
    needs to fetch artwork are looked up once here and frozen into the map artifact.
    """
    import psycopg  # noqa: PLC0415

    identity = _tv_identity(profile)
    rows = [
        {
            "title": title,
            "movie_title": title,
            "movie_id": None,
            "tmdb_id": None,
            "year": None,
            "genres": None,
            "asset_kind": kind,
            "season_number": season,
            "series_id": None,
            "season_id": None,
        }
        for title, kind, season in identity
    ]

    db_url = settings.db_url_resolved.replace("postgresql+asyncpg://", "postgresql://", 1)
    by_title: dict[str, tuple[int, str, int | None, int | None, list | None]] = {}
    seasons_by_series: dict[tuple[int, int], int] = {}
    try:
        with psycopg.connect(db_url) as conn, conn.cursor() as cursor:
            cursor.execute("SELECT id, title, year, tmdb_id, genres FROM series")
            for series_id, title, year, tmdb_id, genres in cursor:
                by_title[str(title).lower()] = (
                    int(series_id),
                    str(title),
                    year or None,
                    tmdb_id,
                    genres,
                )
            cursor.execute("SELECT id, series_id, season_number FROM seasons")
            for season_id, series_id, season_number in cursor:
                seasons_by_series[(int(series_id), int(season_number))] = int(season_id)
    except Exception:  # noqa: BLE001
        logger.warning("Could not resolve taste-map series metadata from PostgreSQL", exc_info=True)
        return rows

    for row in rows:
        resolved = by_title.get(str(row["title"]).lower())
        if resolved is None:
            continue
        row["series_id"] = resolved[0]
        row["movie_title"] = resolved[1]
        row["year"] = resolved[2]
        row["tmdb_id"] = resolved[3]
        row["genres"] = resolved[4]
        if row["season_number"] is not None:
            row["season_id"] = seasons_by_series.get((resolved[0], int(row["season_number"])))
    return rows


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
    n = coords.shape[0]
    min_size = max(5, int(n * pipeline_settings.TASTE_MAP_MIN_CLUSTER_SIZE_RATIO))
    min_samples = max(3, min_size // 3)
    epsilon = pipeline_settings.TASTE_MAP_CLUSTER_EPSILON
    method = pipeline_settings.TASTE_MAP_CLUSTER_METHOD
    kwargs: dict = {
        "min_cluster_size": min_size,
        "min_samples": min_samples,
        "cluster_selection_method": method,
    }
    if epsilon > 0:
        kwargs["cluster_selection_epsilon"] = epsilon
    logger.info(
        "TASTE MAP | clustering with min_cluster_size=%d, min_samples=%d, epsilon=%.2f, method=%s",
        min_size,
        min_samples,
        epsilon,
        method,
    )
    return HDBSCAN(**kwargs).fit_predict(coords).astype(np.int64)


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
                for genre in genres[i] or []:
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


def _load_profile_arrays(
    ns: TasteNamespace | None = None,
    *,
    profile_path: Path | None = None,
) -> dict:
    ns = ns or get_namespace("movies")
    path = profile_path or ns.profile_path
    if not path.exists():
        raise FileNotFoundError(f"Taste profile not found: {path}")
    ensure_safe_artifact(path, ns.artifact_kind_profile)
    with load_npz_safe(path) as data:
        result = {
            "embeddings": np.asarray(data["embeddings"], dtype=np.float32),
            "poster_names": decode_unicode_list(data["poster_names"]),
            "mtime": path.stat().st_mtime,
        }
        if "asset_kinds" in data.files:
            result["asset_kinds"] = decode_unicode_list(data["asset_kinds"])
        if GENRES_JSON_KEY in data.files:
            result["genres"] = decode_json_string_array(data[GENRES_JSON_KEY])
        for key in ("years", "tmdb_ids", "movie_ids"):
            if key in data.files:
                result[key] = data[key].tolist()
        if "movie_titles" in data.files:
            result["movie_titles"] = decode_unicode_list(data["movie_titles"])
        if "series_titles" in data.files:
            result["series_titles"] = decode_unicode_list(data["series_titles"])
        if "season_numbers" in data.files:
            result["season_numbers"] = data["season_numbers"].tolist()
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


def build_map(
    progress_callback=None,
    cancel_event: threading.Event | None = None,
    namespace: TasteNamespace | None = None,
    output: Path | None = None,
    profile_path: Path | None = None,
    generate_thumbnails: bool = True,
) -> dict:
    """Project the profile, cluster, generate thumbnails, save atomically.

    ``progress_callback`` (thread-safe ``callback(dict)``, e.g. a
    ``JobProgressBridge.callback``) receives a payload at each phase boundary
    so the job's progress bar narrates the build instead of spinning silently.
    """
    ns = namespace or get_namespace("movies")

    def _phase(stage: str, message: str) -> None:
        raise_if_cancelled(cancel_event, "taste map build cancelled")
        if progress_callback is not None:
            progress_callback({"stage": stage, "state": "start", "message": message})

    _phase("load", "Loading taste profile…")
    profile = _load_profile_arrays(ns, profile_path=profile_path)
    embeddings = profile["embeddings"]

    # Show and season artwork are projected together: one TV coordinate space is what
    # makes season art comparable to the show art it sits under. The map carries the
    # asset kind so a reader can look at either set on its own.

    # L2-normalize for cosine-correct projection + barycentric placement.
    embeddings = embeddings / np.maximum(np.linalg.norm(embeddings, axis=1, keepdims=True), 1e-10)
    n = embeddings.shape[0]

    _phase("project", f"Projecting {n} exemplars to 3D/2D…")
    coords_3d, method = _reduce(embeddings, 3)
    raise_if_cancelled(cancel_event, "taste map build cancelled")
    coords_2d, _ = _reduce(embeddings, 2)
    _phase("cluster", "Clustering the taste space…")
    labels = _cluster(coords_3d)
    raise_if_cancelled(cancel_event, "taste map build cancelled")
    self_knn = _self_knn(embeddings, pipeline_settings.K_NEIGHBORS)
    is_tv = ns.library == "tv"
    movie_rows = (
        _resolve_profile_series_rows(profile) if is_tv else _resolve_profile_movie_rows(profile)
    )
    # A resolved series carries the genres the TV profile itself never stored, so the
    # cluster names and the genre colouring both work from the same resolved list.
    genres = [list(row["genres"] or ()) for row in movie_rows] if is_tv else profile.get("genres")
    if is_tv and not any(genres):
        genres = None
    names_map = _cluster_names(labels, genres)
    # A subject is a title; a duplicate is the same artwork slot filled twice. On TV
    # those differ — one series legitimately contributes a show poster and one poster
    # per season — so the duplicate key carries the slot, not just the title.
    subjects = Counter((row["movie_title"].lower(), row["year"]) for row in movie_rows)
    slots = (
        Counter(
            (row["movie_title"].lower(), row["asset_kind"], row["season_number"])
            for row in movie_rows
        )
        if is_tv
        else subjects
    )
    unique_movie_count = len(subjects)
    duplicate_group_count = sum(1 for count in slots.values() if count > 1)
    noise_count = int(sum(1 for label in labels if int(label) == -1)) if labels is not None else 0

    # Explicit outputs are attempt-workspace artifacts and must not mutate live
    # history. Legacy/operator calls retain the configured-path archive behavior.
    map_path = output or _map_path(ns)
    if output is None and map_path.exists():
        history = _history_dir(ns)
        history.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        (history / f"{stamp}.npz").write_bytes(map_path.read_bytes())

    payload: dict = {
        "coords_3d": coords_3d,
        "coords_2d": coords_2d,
        "poster_names": unicode_array(profile["poster_names"]),
        "self_knn": self_knn,
        "movie_ids": np.asarray(
            [row["movie_id"] if row["movie_id"] is not None else 0 for row in movie_rows],
            dtype=np.int64,
        ),
        "movie_titles": unicode_array([row["movie_title"] for row in movie_rows]),
        "projection_method": unicode_scalar(method),
        "profile_mtime": np.float64(profile["mtime"]),
        "computed_at": unicode_scalar(datetime.now(UTC).isoformat()),
        "cluster_ratio": np.float64(pipeline_settings.TASTE_MAP_MIN_CLUSTER_SIZE_RATIO),
        "cluster_epsilon": np.float64(pipeline_settings.TASTE_MAP_CLUSTER_EPSILON),
        "cluster_method": unicode_scalar(pipeline_settings.TASTE_MAP_CLUSTER_METHOD),
        "unique_movie_count": np.int64(unique_movie_count),
        "duplicate_group_count": np.int64(duplicate_group_count),
        "noise_count": np.int64(noise_count),
    }
    if labels is not None:
        payload["cluster_labels"] = labels
        payload["cluster_names"] = unicode_array(
            [f"{cid}:{name}" for cid, name in names_map.items()]
        )
    if is_tv:
        # Frozen at build time so the read path stays a pure artifact load: these are
        # what let the map show a season's own artwork and its place in the series.
        payload["asset_kinds"] = unicode_array([str(row["asset_kind"]) for row in movie_rows])
        payload["season_numbers"] = np.asarray(
            [
                -1 if row["season_number"] is None else int(row["season_number"])
                for row in movie_rows
            ],
            dtype=np.int64,
        )
        payload["series_ids"] = np.asarray(
            [int(row["series_id"] or 0) for row in movie_rows], dtype=np.int64
        )
        payload["season_ids"] = np.asarray(
            [int(row["season_id"] or 0) for row in movie_rows], dtype=np.int64
        )
        payload["years"] = np.asarray([int(row["year"] or 0) for row in movie_rows], dtype=np.int64)
        payload["tmdb_ids"] = np.asarray(
            [int(row["tmdb_id"] or 0) for row in movie_rows], dtype=np.int64
        )
        if genres is not None:
            payload[GENRES_JSON_KEY] = json_string_array(genres)
    else:
        if "genres" in profile:
            payload[GENRES_JSON_KEY] = json_string_array(profile["genres"])
        for key in ("years", "tmdb_ids"):
            if key in profile:
                payload[key] = np.asarray(profile[key], dtype=np.int64)
    for key in ("aesthetic", "global_colorfulness"):
        if key in profile:
            payload[key] = np.asarray(profile[key], dtype=np.float64)

    _phase("save", "Saving taste map…")
    save_npz_atomic(map_path, payload)
    raise_if_cancelled(cancel_event, "taste map build cancelled")

    if generate_thumbnails:
        _phase("thumbnails", "Generating exemplar thumbnails…")
        _generate_thumbnails(profile["poster_names"], ns)
    logger.info("TASTE MAP | built %d points (%s), %d clusters", n, method, len(names_map))
    return load_map(namespace=ns, path=map_path)


def _is_stale(ns: TasteNamespace | None = None) -> bool:
    ns = ns or get_namespace("movies")
    map_path = _map_path(ns)
    if not map_path.exists():
        return True
    try:
        ensure_safe_artifact(map_path, "taste_map")
        with load_npz_safe(map_path) as data:
            map_mtime = float(np.asarray(data["profile_mtime"]).item())
            # Rebuild if cluster parameters changed.
            for key, current in (
                ("cluster_ratio", pipeline_settings.TASTE_MAP_MIN_CLUSTER_SIZE_RATIO),
                ("cluster_epsilon", pipeline_settings.TASTE_MAP_CLUSTER_EPSILON),
            ):
                if key in data.files and abs(float(np.asarray(data[key]).item()) - current) > 1e-8:
                    return True
    except Exception:
        return True
    profile_path = ns.profile_path
    return profile_path.exists() and profile_path.stat().st_mtime > map_mtime + 1e-6


def load_map(
    recompute: bool = False,
    namespace: TasteNamespace | None = None,
    *,
    path: Path | None = None,
) -> dict:
    """Return the cached map as a JSON-ready dict, rebuilding if stale."""
    ns = namespace or get_namespace("movies")
    if path is None and (recompute or _is_stale(ns)):
        return build_map(namespace=ns)

    map_path = path or _map_path(ns)
    ensure_safe_artifact(map_path, "taste_map")
    with load_npz_safe(map_path) as data:
        names = decode_unicode_list(data["poster_names"])
        coords_3d = data["coords_3d"]
        coords_2d = data["coords_2d"]
        labels = data["cluster_labels"].tolist() if "cluster_labels" in data.files else None
        cluster_names = (
            decode_unicode_list(data["cluster_names"]) if "cluster_names" in data.files else []
        )
        self_knn = data["self_knn"].tolist()
        genres = (
            decode_json_string_array(data[GENRES_JSON_KEY])
            if GENRES_JSON_KEY in data.files
            else None
        )
        years = data["years"].tolist() if "years" in data.files else None
        tmdb_ids = data["tmdb_ids"].tolist() if "tmdb_ids" in data.files else None
        movie_ids = data["movie_ids"].tolist() if "movie_ids" in data.files else None
        movie_titles = (
            decode_unicode_list(data["movie_titles"]) if "movie_titles" in data.files else None
        )
        aesthetic = data["aesthetic"].tolist() if "aesthetic" in data.files else None
        colorfulness = (
            data["global_colorfulness"].tolist() if "global_colorfulness" in data.files else None
        )
        asset_kinds = (
            decode_unicode_list(data["asset_kinds"]) if "asset_kinds" in data.files else None
        )
        season_numbers = data["season_numbers"].tolist() if "season_numbers" in data.files else None
        series_ids = data["series_ids"].tolist() if "series_ids" in data.files else None
        season_ids = data["season_ids"].tolist() if "season_ids" in data.files else None
        method = decode_unicode_scalar(data["projection_method"])
        computed_at = decode_unicode_scalar(data["computed_at"])
        unique_movie_count = (
            int(np.asarray(data["unique_movie_count"]).item())
            if "unique_movie_count" in data.files
            else None
        )
        duplicate_group_count = (
            int(np.asarray(data["duplicate_group_count"]).item())
            if "duplicate_group_count" in data.files
            else None
        )
        noise_count = (
            int(np.asarray(data["noise_count"]).item()) if "noise_count" in data.files else None
        )

    default_kind = "show" if ns.library == "tv" else "movie"
    points = []
    for i, name in enumerate(names):
        title, parsed_year = _parse_name(name)
        is_noise = labels is not None and int(labels[i]) == -1
        # Maps built before the kinds were carried held show artwork only, so they
        # still land in the right view rather than reading as films.
        kind = str(asset_kinds[i]) if asset_kinds is not None else default_kind
        season_number = None
        if season_numbers is not None and int(season_numbers[i]) >= 0:
            season_number = int(season_numbers[i])
        series_id = series_ids[i] if series_ids is not None and series_ids[i] else None
        season_id = season_ids[i] if season_ids is not None and season_ids[i] else None
        movie_id = movie_ids[i] if movie_ids is not None and movie_ids[i] else None
        points.append(
            {
                "name": name,
                "movie_title": movie_titles[i] if movie_titles is not None else title,
                "movie_id": movie_id,
                "asset_kind": kind,
                "season_number": season_number,
                "series_id": series_id,
                "season_id": season_id,
                "poster_url": _poster_url(
                    kind, movie_id=movie_id, series_id=series_id, season_id=season_id
                ),
                "x": float(coords_3d[i][0]),
                "y": float(coords_3d[i][1]),
                "z": float(coords_3d[i][2]),
                "x2": float(coords_2d[i][0]),
                "y2": float(coords_2d[i][1]),
                "cluster": int(labels[i]) if labels is not None else None,
                "is_noise": is_noise,
                "self_knn": float(self_knn[i]),
                "genres": genres[i] if genres is not None else None,
                "year": years[i] if years is not None else parsed_year,
                "aesthetic": aesthetic[i] if aesthetic is not None else None,
                "colorfulness": colorfulness[i] if colorfulness is not None else None,
                "tmdb_id": tmdb_ids[i] if tmdb_ids is not None and tmdb_ids[i] else None,
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

    if unique_movie_count is None:
        grouped = Counter((str(point["movie_title"]).lower(), point["year"]) for point in points)
        unique_movie_count = len(grouped)
        duplicate_group_count = sum(1 for count in grouped.values() if count > 1)
    if noise_count is None:
        noise_count = sum(1 for point in points if point["is_noise"])

    by_kind = Counter(str(point["asset_kind"]) for point in points)
    return {
        "projection": {"method": method, "computed_at": computed_at},
        "points": points,
        "clusters": clusters,
        "summary": {
            "exemplars": len(points),
            "unique_movies": unique_movie_count,
            "duplicate_groups": duplicate_group_count or 0,
            "noise": noise_count,
            "by_kind": dict(by_kind),
        },
        "outliers": outliers,
        "clustering": clusters or None,
        "note": note,
    }


# ---------------------------------------------------------------------------
# Candidate projection (barycentric)
# ---------------------------------------------------------------------------


def project(
    embeddings: np.ndarray,
    k: int | None = None,
    namespace: TasteNamespace | None = None,
) -> list[dict]:
    """k-NN barycentric placement of candidate embeddings into the map space."""
    k = k or pipeline_settings.K_NEIGHBORS
    ns = namespace or get_namespace("movies")
    if _is_stale(ns):
        build_map(namespace=ns)

    profile = _load_profile_arrays(ns)
    profile_emb = profile["embeddings"]
    if ns.library == "tv" and "asset_kinds" in profile:
        keep = [i for i, kind in enumerate(profile["asset_kinds"]) if kind == "show"]
        if keep:
            profile_emb = profile_emb[keep]
            names = [profile["poster_names"][i] for i in keep]
        else:
            names = profile["poster_names"]
    else:
        names = profile["poster_names"]

    profile_emb = profile_emb / np.maximum(
        np.linalg.norm(profile_emb, axis=1, keepdims=True), 1e-10
    )
    map_path = _map_path(ns)
    ensure_safe_artifact(map_path, "taste_map")
    with load_npz_safe(map_path) as data:
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
                "neighbors": [{"name": names[i], "similarity": float(sims[i])} for i in top_idx],
            }
        )
    return results


def neighbors_of(
    poster_name: str,
    k: int | None = None,
    namespace: TasteNamespace | None = None,
    *,
    profile_path: Path | None = None,
) -> list[dict] | None:
    """The k nearest exemplars to a given exemplar (click-to-explore)."""
    k = k or pipeline_settings.K_NEIGHBORS
    ns = namespace or get_namespace("movies")
    profile = _load_profile_arrays(ns, profile_path=profile_path)
    names = profile["poster_names"]
    if poster_name not in names:
        return None
    embeddings = profile["embeddings"]
    embeddings = embeddings / np.maximum(np.linalg.norm(embeddings, axis=1, keepdims=True), 1e-10)
    index = names.index(poster_name)
    sims = embeddings @ embeddings[index]
    sims[index] = -np.inf
    count = min(max(k, 1), sims.shape[0] - 1)
    top_idx = np.argsort(sims)[::-1][:count]
    return [{"name": names[i], "similarity": float(sims[i])} for i in top_idx]


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------


def _generate_thumbnails(poster_names: list[str], ns: TasteNamespace | None = None) -> None:
    # Canonical profiles no longer expose mutable training folders as an asset source.
    return None


def thumbnail_path(poster_name: str, namespace: TasteNamespace | None = None) -> Path | None:
    ns = namespace or get_namespace("movies")
    thumb = _thumbs_dir(ns) / f"{poster_name}.webp"
    return thumb if thumb.is_file() else None


def exemplar_source(poster_name: str, namespace: TasteNamespace | None = None) -> Path | None:
    return None
