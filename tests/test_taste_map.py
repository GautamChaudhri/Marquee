"""Taste-map tests using a synthetic profile (PCA path, no ML models).

Exercises the projection, barycentric candidate placement, neighbor lookup,
staleness/determinism, and the endpoints — all on a fabricated .npz so no
UMAP/sklearn/real models are required.
"""

from __future__ import annotations

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from marquee.core.pipeline_config import pipeline_settings
from marquee.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def synthetic_profile(tmp_path, monkeypatch):
    """Write a small synthetic taste profile and point config at it."""
    rng = np.random.default_rng(0)
    n = 12
    embeddings = rng.standard_normal((n, 512)).astype(np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    names = np.asarray([f"Movie {i} (200{i % 10}).jpg" for i in range(n)], dtype=object)
    centroid = embeddings.mean(0)
    centroid /= np.linalg.norm(centroid)

    profile_path = tmp_path / "taste_profile.clip-vit-b-32.npz"
    np.savez(
        profile_path,
        embeddings=embeddings,
        poster_names=names,
        centroid_emb=centroid.astype(np.float32),
        model_name=np.asarray("clip-vit-b-32"),
    )

    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_PATH", profile_path)
    monkeypatch.setattr(pipeline_settings, "TRAINING_DATA_DIR", tmp_path / "training")

    from marquee.config import settings

    monkeypatch.setattr(
        type(settings),
        "poster_cache_path",
        property(lambda self: tmp_path / "cache" / "posters"),
    )

    from marquee.ml import taste_map

    def reduce_with_pca(matrix: np.ndarray, n: int) -> tuple[np.ndarray, str]:
        return taste_map._pca(matrix, n), "pca"

    monkeypatch.setattr(taste_map, "_reduce", reduce_with_pca)
    return embeddings, [str(x) for x in names.tolist()]


def test_build_and_load_map_pca(synthetic_profile):
    from marquee.ml.taste_map import load_map

    result = load_map(recompute=True)
    assert result["projection"]["method"] == "pca"  # umap not installed
    assert len(result["points"]) == 12
    # < 50 exemplars → no clustering, with a guidance note.
    assert result["clustering"] is None
    assert result["note"] is not None
    # Each point has 3D + 2D coords.
    p = result["points"][0]
    assert {"x", "y", "z", "x2", "y2", "self_knn"} <= set(p)


def test_map_is_deterministic(synthetic_profile):
    from marquee.ml.taste_map import build_map

    first = build_map()
    second = build_map()
    xs1 = [p["x"] for p in first["points"]]
    xs2 = [p["x"] for p in second["points"]]
    assert np.allclose(xs1, xs2)


def test_project_candidate_lands_near_itself(synthetic_profile):
    from marquee.ml.taste_map import build_map, project

    built = build_map()
    embeddings, _names = synthetic_profile
    # Project exemplar 0 as if it were a candidate.
    result = project(embeddings[0:1])[0]
    assert result["knn_sim"] > 0.9  # nearest neighbor is itself
    # Its projected position is close to exemplar 0's map coordinate.
    p0 = built["points"][0]
    dist = abs(result["x"] - p0["x"]) + abs(result["y"] - p0["y"]) + abs(result["z"] - p0["z"])
    assert dist < 2.0


def test_neighbors_of(synthetic_profile):
    from marquee.ml.taste_map import build_map, neighbors_of

    build_map()
    _embeddings, names = synthetic_profile
    neighbors = neighbors_of(names[0])
    assert neighbors is not None
    assert names[0] not in [n["name"] for n in neighbors]  # excludes itself
    assert neighbors_of("does not exist.jpg") is None


def test_staleness_triggers_rebuild(synthetic_profile, tmp_path):
    import os
    import time

    from marquee.ml.taste_map import _is_stale, build_map

    build_map()
    assert _is_stale() is False
    # Touch the profile so its mtime is newer than the map.
    time.sleep(0.01)
    os.utime(pipeline_settings.TASTE_PROFILE_PATH, None)
    assert _is_stale() is True


@pytest.mark.asyncio
async def test_map_endpoint(client, synthetic_profile):
    resp = await client.get("/api/taste/map?recompute=true")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["points"]) == 12
    assert data["projection"]["method"] == "pca"


@pytest.mark.asyncio
async def test_exemplar_neighbors_endpoint(client, synthetic_profile):
    from marquee.ml.taste_map import build_map

    build_map()
    _embeddings, names = synthetic_profile
    resp = await client.get(f"/api/taste/exemplars/{names[0]}/neighbors")
    assert resp.status_code == 200
    assert len(resp.json()["neighbors"]) > 0


@pytest.mark.asyncio
async def test_exemplar_image_rejects_traversal(client, synthetic_profile):
    resp = await client.get("/api/taste/exemplars/..%2f..%2fetc%2fpasswd/image")
    # Either rejected as invalid name (400) or not found (404) — never served.
    assert resp.status_code in (400, 404)
