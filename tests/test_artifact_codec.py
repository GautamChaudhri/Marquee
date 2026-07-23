from __future__ import annotations

import numpy as np
import pytest

from marquee.ml.artifact_codec import (
    GENRES_JSON_KEY,
    ArtifactMigrationError,
    decode_json_string_array,
    decode_unicode_list,
    decode_unicode_scalar,
    ensure_safe_artifact,
    json_string_array,
    load_npz_safe,
    unicode_array,
    unicode_scalar,
)
from marquee.ml.taste_map import load_map
from marquee.ml.taste_store import NumpyTasteStore
from marquee.ml.zeroshot import ZeroShotAxes


def test_codec_roundtrip_helpers():
    rows = [["Action", "Drama"], [], ["Comedy"]]
    encoded = json_string_array(rows)
    assert decode_json_string_array(encoded) == rows
    names = unicode_array(["a.jpg", "b.jpg"])
    assert decode_unicode_list(names) == ["a.jpg", "b.jpg"]
    assert decode_unicode_scalar(unicode_scalar("clip-vit-b-32")) == "clip-vit-b-32"


def test_legacy_taste_profile_migrates_and_loads(tmp_path):
    path = tmp_path / "taste_profile.clip-vit-b-32.npz"
    embeddings = np.eye(3, 512, dtype=np.float32)
    centroid = embeddings.mean(axis=0)
    centroid /= np.linalg.norm(centroid)
    np.savez(
        path,
        embeddings=embeddings,
        poster_names=np.asarray(["a.jpg", "b.jpg", "c.jpg"], dtype=object),
        centroid_emb=centroid.astype(np.float32),
        model_name=np.asarray("clip-vit-b-32"),
        calib_feature_names=np.asarray(["darkness"], dtype=object),
        calib_feature_values=np.asarray([[0.4, 0.5, 0.6]], dtype=np.float64),
        genres=np.asarray([["Action"], ["Drama"], []], dtype=object),
    )

    result = ensure_safe_artifact(path, "taste_profile")
    assert result.migrated is True
    with load_npz_safe(path) as data:
        assert decode_unicode_list(data["poster_names"]) == ["a.jpg", "b.jpg", "c.jpg"]
        assert decode_unicode_list(data["calib_feature_names"]) == ["darkness"]
        assert decode_json_string_array(data[GENRES_JSON_KEY]) == [
            ["Action"],
            ["Drama"],
            [],
        ]
    store = NumpyTasteStore(path)
    assert store.size == 3


def test_legacy_zeroshot_axes_migrates_and_loads(tmp_path):
    path = tmp_path / "zeroshot_axes.clip-vit-b-32.npz"
    np.savez(
        path,
        axis_names=np.asarray(["axis_one", "axis_two"], dtype=object),
        directions=np.zeros((2, 512), dtype=np.float32),
        model_name=np.asarray("clip-vit-b-32"),
    )

    assert ensure_safe_artifact(path, "zeroshot_axes").migrated is True
    loaded = ZeroShotAxes.load(path, expected_model_name="clip-vit-b-32")
    assert loaded is not None
    assert loaded.axis_names == ["axis_one", "axis_two"]


def test_legacy_taste_map_migrates_and_loads(tmp_path, monkeypatch):
    profile_path = tmp_path / "taste_profile.clip-vit-b-32.npz"
    embeddings = np.eye(3, 512, dtype=np.float32)
    centroid = embeddings.mean(axis=0)
    centroid /= np.linalg.norm(centroid)
    np.savez(
        profile_path,
        embeddings=embeddings,
        poster_names=unicode_array(["a.jpg", "b.jpg", "c.jpg"]),
        centroid_emb=centroid.astype(np.float32),
        model_name=unicode_scalar("clip-vit-b-32"),
    )
    map_path = tmp_path / "cache" / "taste_map.clip-vit-b-32.npz"
    map_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        map_path,
        coords_3d=np.zeros((3, 3), dtype=np.float32),
        coords_2d=np.zeros((3, 2), dtype=np.float32),
        poster_names=np.asarray(["a.jpg", "b.jpg", "c.jpg"], dtype=object),
        self_knn=np.asarray([0.9, 0.8, 0.7], dtype=np.float64),
        projection_method=np.asarray("pca"),
        computed_at=np.asarray("2026-06-17T00:00:00+00:00"),
        profile_mtime=np.float64(profile_path.stat().st_mtime),
        cluster_names=np.asarray(["0:Cluster 0 (3)"], dtype=object),
        genres=np.asarray([["Action"], [], ["Drama"]], dtype=object),
        years=np.asarray([2000, 2001, 2002], dtype=np.int64),
    )

    from marquee.config import settings
    from marquee.core.pipeline_config import pipeline_settings

    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_PATH", profile_path)
    monkeypatch.setattr(
        type(settings), "poster_cache_path", property(lambda self: tmp_path / "cache" / "posters")
    )

    result = load_map()
    assert len(result["points"]) == 3
    assert result["points"][0]["genres"] == ["Action"]


def test_failed_migration_leaves_original_untouched(tmp_path):
    path = tmp_path / "taste_profile.clip-vit-b-32.npz"
    embeddings = np.eye(1, 512, dtype=np.float32)
    centroid = embeddings[0]
    np.savez(
        path,
        embeddings=embeddings,
        poster_names=np.asarray(["a.jpg"], dtype=object),
        centroid_emb=centroid,
        model_name=np.asarray("clip-vit-b-32"),
        unsupported=np.asarray([{"bad": "payload"}], dtype=object),
    )
    original = path.read_bytes()
    with pytest.raises(ArtifactMigrationError):
        ensure_safe_artifact(path, "taste_profile")
    assert path.read_bytes() == original
