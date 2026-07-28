"""Safe NumPy artifact encoding and legacy migration helpers.

Marquee keeps runtime/model artifacts in ``.npz`` files but avoids object
arrays so normal loads can use ``allow_pickle=False`` safely. Legacy artifacts
that stored strings or ragged metadata as object arrays can be migrated in
place by the helpers in this module.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

ArtifactKind = Literal[
    "taste_profile", "taste_profile_tv", "ranking_residual", "zeroshot_axes", "taste_map"
]

CALIB_NAMES_KEY = "calib_feature_names"
GENRES_JSON_KEY = "genres_json"


class ArtifactMigrationError(RuntimeError):
    """Legacy artifact could not be migrated safely."""


@dataclass(frozen=True)
class ArtifactMigrationResult:
    path: Path
    kind: ArtifactKind
    migrated: bool


_STRING_LIST_KEYS: dict[ArtifactKind, set[str]] = {
    "taste_profile": {"poster_names", "neg_poster_names", CALIB_NAMES_KEY, "asset_kinds"},
    "taste_profile_tv": {"poster_names", "neg_poster_names", CALIB_NAMES_KEY, "asset_kinds"},
    "ranking_residual": {"feature_names"},
    "zeroshot_axes": {"axis_names"},
    "taste_map": {"poster_names", "cluster_names"},
}

_SCALAR_STRING_KEYS: dict[ArtifactKind, set[str]] = {
    "taste_profile": {"model_name", "dino_model_name"},
    "taste_profile_tv": {"model_name", "dino_model_name"},
    "ranking_residual": {
        "namespace",
        "baseline_signature",
        "profile_checksum",
        "evidence_revision",
        "evaluation_json",
        "partitions_json",
        "trained_at",
    },
    "zeroshot_axes": {"model_name"},
    "taste_map": {"projection_method", "computed_at"},
}

_JSON_STRING_ARRAY_KEYS: dict[ArtifactKind, set[str]] = {
    "taste_profile": {"genres"},
    "taste_profile_tv": {"genres"},
    "ranking_residual": set(),
    "zeroshot_axes": set(),
    "taste_map": {"genres"},
}

_REQUIRED_KEYS: dict[ArtifactKind, set[str]] = {
    "taste_profile": {"embeddings", "poster_names", "centroid_emb", "model_name"},
    "taste_profile_tv": {"embeddings", "poster_names", "centroid_emb", "model_name"},
    "ranking_residual": {
        "namespace",
        "feature_names",
        "weights",
        "bias",
        "alpha",
        "delta_max",
        "baseline_signature",
        "profile_checksum",
        "evidence_revision",
        "seed",
        "evaluation_json",
        "trained_at",
    },
    "zeroshot_axes": {"axis_names", "directions", "model_name"},
    "taste_map": {"coords_3d", "coords_2d", "poster_names", "self_knn"},
}

_REBUILD_HINTS: dict[ArtifactKind, str] = {
    "taste_profile": "Rebuild it with `python -m marquee.ml.taste_trainer`.",
    "taste_profile_tv": "Rebuild it with `python -m marquee.ml.taste_trainer`.",
    "ranking_residual": "Retrain it with the canonical ranking_residual_train job.",
    "zeroshot_axes": "Rebuild it with `python -m marquee.ml.zeroshot`.",
    "taste_map": "Rebuild it by reloading the taste map endpoint or on the next app start.",
}


def unicode_array(values: list[str]) -> np.ndarray:
    return np.asarray([str(value) for value in values], dtype=np.str_)


def unicode_scalar(value: str) -> np.ndarray:
    return np.asarray(str(value), dtype=np.str_)


def json_string_array(rows: list[list[str]]) -> np.ndarray:
    return np.asarray(
        [json.dumps([str(item) for item in row], ensure_ascii=False) for row in rows],
        dtype=np.str_,
    )


def decode_unicode_list(values: np.ndarray) -> list[str]:
    return [str(value) for value in np.asarray(values).tolist()]


def decode_unicode_scalar(value: np.ndarray) -> str:
    return str(np.asarray(value).item())


def decode_json_string_array(values: np.ndarray) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in decode_unicode_list(values):
        loaded = json.loads(item)
        if not isinstance(loaded, list):
            raise ValueError("Expected JSON list payload")
        rows.append([str(entry) for entry in loaded])
    return rows


def save_npz_atomic(path: str | Path, payload: dict[str, np.ndarray]) -> Path:
    artifact = Path(path)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    tmp = artifact.with_name(artifact.name + ".tmp.npz")
    np.savez(tmp, **payload)
    os.replace(tmp, artifact)
    return artifact


def load_npz_safe(path: str | Path) -> np.lib.npyio.NpzFile:
    return np.load(Path(path), allow_pickle=False)


def _read_all(path: Path, *, allow_pickle: bool) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=allow_pickle) as data:
        return {key: data[key] for key in data.files}


def _artifact_object_keys(kind: ArtifactKind, payload: dict[str, np.ndarray]) -> set[str]:
    return {
        key
        for key, value in payload.items()
        if isinstance(value, np.ndarray) and value.dtype.kind == "O"
    }


def _allowed_legacy_object_keys(kind: ArtifactKind) -> set[str]:
    return _STRING_LIST_KEYS[kind] | _SCALAR_STRING_KEYS[kind] | _JSON_STRING_ARRAY_KEYS[kind]


def needs_migration(kind: ArtifactKind, payload: dict[str, np.ndarray]) -> bool:
    return bool(_artifact_object_keys(kind, payload)) or (
        kind in {"taste_profile", "taste_map"} and "genres" in payload
    )


def _normalize_string_list(value: np.ndarray) -> np.ndarray:
    return unicode_array([str(item) for item in np.asarray(value).tolist()])


def _normalize_scalar_string(value: np.ndarray) -> np.ndarray:
    return unicode_scalar(str(np.asarray(value).item()))


def _normalize_json_rows(value: np.ndarray) -> np.ndarray:
    rows = []
    for row in np.asarray(value).tolist():
        if isinstance(row, np.ndarray):
            row = row.tolist()
        if row is None:
            rows.append([])
            continue
        if not isinstance(row, list):
            raise ArtifactMigrationError(f"Unsupported ragged metadata row: {row!r}")
        rows.append([str(item) for item in row])
    return json_string_array(rows)


def normalize_payload(kind: ArtifactKind, payload: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    unexpected = _artifact_object_keys(kind, payload) - _allowed_legacy_object_keys(kind)
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise ArtifactMigrationError(f"Unsupported legacy object arrays in {kind}: {names}")

    normalized = dict(payload)
    for key in _STRING_LIST_KEYS[kind]:
        if key in normalized:
            normalized[key] = _normalize_string_list(normalized[key])
    for key in _SCALAR_STRING_KEYS[kind]:
        if key in normalized:
            normalized[key] = _normalize_scalar_string(normalized[key])
    for key in _JSON_STRING_ARRAY_KEYS[kind]:
        if key in normalized:
            normalized[GENRES_JSON_KEY] = _normalize_json_rows(normalized.pop(key))
    return normalized


def validate_payload(kind: ArtifactKind, payload: dict[str, np.ndarray]) -> None:
    missing = _REQUIRED_KEYS[kind] - set(payload)
    if missing:
        names = ", ".join(sorted(missing))
        raise ArtifactMigrationError(f"Artifact missing required keys: {names}")

    for key in _STRING_LIST_KEYS[kind]:
        if key in payload and payload[key].dtype.kind == "O":
            raise ArtifactMigrationError(f"{kind} still contains object array {key}")
    for key in _SCALAR_STRING_KEYS[kind]:
        if key in payload and payload[key].dtype.kind == "O":
            raise ArtifactMigrationError(f"{kind} still contains object scalar {key}")
    if "genres" in payload:
        raise ArtifactMigrationError("Legacy genres key must be replaced by genres_json")
    if GENRES_JSON_KEY in payload and payload[GENRES_JSON_KEY].dtype.kind == "O":
        raise ArtifactMigrationError("genres_json must not be an object array")

    if kind in {"taste_profile", "taste_profile_tv"}:
        embeddings = np.asarray(payload["embeddings"], dtype=np.float32)
        centroid = np.asarray(payload["centroid_emb"], dtype=np.float32)
        names = decode_unicode_list(payload["poster_names"])
        if embeddings.ndim != 2 or embeddings.shape[1] != 512:
            raise ArtifactMigrationError(f"Invalid taste embeddings shape: {embeddings.shape}")
        if centroid.shape != (512,):
            raise ArtifactMigrationError(f"Invalid taste centroid shape: {centroid.shape}")
        if len(names) != embeddings.shape[0]:
            raise ArtifactMigrationError("poster_names length does not match embeddings")
        if "neg_embeddings" in payload:
            neg = np.asarray(payload["neg_embeddings"], dtype=np.float32)
            if neg.ndim != 2 or neg.shape[1] != 512:
                raise ArtifactMigrationError(f"Invalid negative embeddings shape: {neg.shape}")
            if "neg_embedding_weights" in payload:
                neg_weights = np.asarray(payload["neg_embedding_weights"], dtype=np.float32)
                if neg_weights.shape != (neg.shape[0],):
                    raise ArtifactMigrationError(
                        "negative evidence weights do not match embeddings"
                    )
                if (
                    not np.all(np.isfinite(neg_weights))
                    or np.any(neg_weights <= 0)
                    or np.any(neg_weights > 1)
                ):
                    raise ArtifactMigrationError(
                        "negative evidence weights must be finite and in (0, 1]"
                    )
        if "embedding_weights" in payload:
            weights = np.asarray(payload["embedding_weights"], dtype=np.float32)
            if weights.shape != (embeddings.shape[0],):
                raise ArtifactMigrationError("positive evidence weights do not match embeddings")
            if not np.all(np.isfinite(weights)) or np.any(weights <= 0) or np.any(weights > 1):
                raise ArtifactMigrationError(
                    "positive evidence weights must be finite and in (0, 1]"
                )
        if "dino_embeddings" in payload:
            dino = np.asarray(payload["dino_embeddings"], dtype=np.float32)
            if dino.shape[0] != embeddings.shape[0]:
                raise ArtifactMigrationError(
                    "dino_embeddings count does not match CLIP exemplar count"
                )
    elif kind == "ranking_residual":
        feature_names = decode_unicode_list(payload["feature_names"])
        weights = np.asarray(payload["weights"], dtype=np.float64)
        if weights.ndim != 1:
            raise ArtifactMigrationError(f"Invalid ranking weight shape: {weights.shape}")
        if len(feature_names) != weights.shape[0]:
            raise ArtifactMigrationError("feature_names length does not match weights")
    elif kind == "zeroshot_axes":
        names = decode_unicode_list(payload["axis_names"])
        directions = np.asarray(payload["directions"], dtype=np.float32)
        if directions.ndim != 2 or directions.shape[1] != 512:
            raise ArtifactMigrationError(f"Invalid zeroshot direction shape: {directions.shape}")
        if len(names) != directions.shape[0]:
            raise ArtifactMigrationError("axis_names length does not match directions")
    elif kind == "taste_map":
        names = decode_unicode_list(payload["poster_names"])
        coords_3d = np.asarray(payload["coords_3d"], dtype=np.float32)
        coords_2d = np.asarray(payload["coords_2d"], dtype=np.float32)
        self_knn = np.asarray(payload["self_knn"], dtype=np.float64)
        if coords_3d.ndim != 2 or coords_3d.shape[1] != 3:
            raise ArtifactMigrationError(f"Invalid 3D map shape: {coords_3d.shape}")
        if coords_2d.ndim != 2 or coords_2d.shape[1] != 2:
            raise ArtifactMigrationError(f"Invalid 2D map shape: {coords_2d.shape}")
        if coords_3d.shape[0] != len(names) or coords_2d.shape[0] != len(names):
            raise ArtifactMigrationError("Taste-map coordinate rows do not match poster_names")
        if self_knn.shape[0] != len(names):
            raise ArtifactMigrationError("self_knn length does not match poster_names")
        if "cluster_names" in payload:
            for entry in decode_unicode_list(payload["cluster_names"]):
                if ":" not in entry:
                    raise ArtifactMigrationError("cluster_names entries must keep id:name shape")
        if GENRES_JSON_KEY in payload:
            rows = decode_json_string_array(payload[GENRES_JSON_KEY])
            if len(rows) != len(names):
                raise ArtifactMigrationError("genres_json length does not match poster_names")


def ensure_safe_artifact(path: str | Path, kind: ArtifactKind) -> ArtifactMigrationResult:
    artifact = Path(path)
    if not artifact.exists():
        return ArtifactMigrationResult(path=artifact, kind=kind, migrated=False)

    try:
        payload = _read_all(artifact, allow_pickle=False)
    except ValueError:
        payload = _read_all(artifact, allow_pickle=True)
    else:
        validate_payload(kind, payload)
        return ArtifactMigrationResult(path=artifact, kind=kind, migrated=False)

    normalized = normalize_payload(kind, payload)
    tmp = artifact.with_name(artifact.name + ".migrated.tmp.npz")
    try:
        np.savez(tmp, **normalized)
        migrated = _read_all(tmp, allow_pickle=False)
        validate_payload(kind, migrated)
        os.replace(tmp, artifact)
    except Exception as exc:
        if tmp.exists():
            tmp.unlink()
        hint = _REBUILD_HINTS[kind]
        raise ArtifactMigrationError(
            f"Failed to migrate legacy {kind} artifact {artifact}: {exc}. {hint}"
        ) from exc
    return ArtifactMigrationResult(path=artifact, kind=kind, migrated=True)
