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
from marquee.ml.artifact_codec import unicode_array, unicode_scalar


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
    names = unicode_array([f"Movie {i} (200{i % 10}).jpg" for i in range(n)])
    centroid = embeddings.mean(0)
    centroid /= np.linalg.norm(centroid)

    profile_path = tmp_path / "taste_profile.clip-vit-b-32.npz"
    np.savez(
        profile_path,
        embeddings=embeddings,
        poster_names=names,
        centroid_emb=centroid.astype(np.float32),
        model_name=unicode_scalar("clip-vit-b-32"),
    )

    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_PATH", profile_path)
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
    assert result["summary"]["exemplars"] == 12
    assert result["summary"]["unique_movies"] == 12
    # < 50 exemplars → no clustering, with a guidance note.
    assert result["clustering"] is None
    assert result["note"] is not None
    # Each point has 3D + 2D coords.
    p = result["points"][0]
    assert {"x", "y", "z", "x2", "y2", "self_knn", "movie_title", "is_noise"} <= set(p)


@pytest.fixture
def synthetic_tv_profile(tmp_path, monkeypatch):
    """A TV profile carrying both show artwork and season artwork."""
    rng = np.random.default_rng(1)
    names = [f"Series {i // 3}-{'show' if i % 3 == 0 else f'season0{i % 3}'}.jpg" for i in range(12)]
    embeddings = rng.standard_normal((len(names), 512)).astype(np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    centroid = embeddings.mean(0)
    centroid /= np.linalg.norm(centroid)

    profile_path = tmp_path / "taste_profile.tv.clip-vit-b-32.npz"
    np.savez(
        profile_path,
        embeddings=embeddings,
        poster_names=unicode_array(names),
        asset_kinds=unicode_array(
            ["show" if "-show" in name else "season" for name in names],
        ),
        centroid_emb=centroid.astype(np.float32),
        model_name=unicode_scalar("clip-vit-b-32"),
    )

    from marquee.config import settings

    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_TV_PATH", profile_path)
    monkeypatch.setattr(
        type(settings),
        "poster_cache_path",
        property(lambda self: tmp_path / "cache" / "posters"),
    )

    from marquee.ml import namespaces, taste_map

    # Namespaces are rebuilt from live pipeline_settings, so the patch above is enough.
    namespace = namespaces.get_namespace("tv")

    def reduce_with_pca(matrix: np.ndarray, n: int) -> tuple[np.ndarray, str]:
        return taste_map._pca(matrix, n), "pca"

    monkeypatch.setattr(taste_map, "_reduce", reduce_with_pca)
    return namespace, names


def test_tv_map_keeps_show_and_season_artwork(synthetic_tv_profile):
    """Seasons are a view of the TV map, so they must survive the build."""
    from marquee.ml.taste_map import build_map

    namespace, names = synthetic_tv_profile
    result = build_map(namespace=namespace, generate_thumbnails=False)

    kinds = [point["asset_kind"] for point in result["points"]]
    assert len(result["points"]) == len(names)
    assert kinds.count("show") == 4
    assert kinds.count("season") == 8
    assert result["summary"]["by_kind"] == {"show": 4, "season": 8}


def test_tv_map_points_carry_series_identity(synthetic_tv_profile):
    """A season point knows which season it is, so the detail panel can name it."""
    from marquee.ml.taste_map import build_map

    namespace, _names = synthetic_tv_profile
    points = build_map(namespace=namespace, generate_thumbnails=False)["points"]

    seasons = [point for point in points if point["asset_kind"] == "season"]
    assert {point["season_number"] for point in seasons} == {1, 2}
    assert all(point["season_number"] is None for point in points if point["asset_kind"] == "show")
    # Nothing resolved against the library here, so no artwork is claimed for them.
    assert all(point["poster_url"] is None for point in points)


def test_point_poster_urls_match_real_serving_routes():
    """A poster URL the map invents is worthless unless something serves it.

    These are built by string concatenation far from the routers, so this pins them
    to the mounted paths rather than to a remembered prefix.
    """
    import re

    from marquee.main import app
    from marquee.ml.taste_map import _poster_url

    mounted = {
        getattr(route, "path", "") for route in app.routes if "GET" in getattr(route, "methods", ())
    }
    patterns = [re.compile(re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in mounted]

    urls = [
        _poster_url("movie", movie_id=7, series_id=None, season_id=None),
        _poster_url("show", movie_id=None, series_id=7, season_id=None),
        _poster_url("season", movie_id=None, series_id=7, season_id=9),
    ]
    for url in urls:
        assert url is not None
        assert any(pattern.match(url) for pattern in patterns), f"{url} is not a served route"

    # Unresolved subjects get nothing rather than a URL that cannot resolve.
    assert _poster_url("movie", movie_id=None, series_id=None, season_id=None) is None
    assert _poster_url("season", movie_id=None, series_id=7, season_id=None) is None


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
async def test_map_endpoint_consumes_active_publication(client, synthetic_profile, monkeypatch):
    from types import SimpleNamespace

    from marquee.ml.taste_map import _map_path, build_map

    build_map()

    async def active(_db, *, family: str):
        assert family == "taste_map:movies"
        return SimpleNamespace(path=_map_path())

    monkeypatch.setattr("marquee.api.routes.taste.resolve_active_publication", active)
    resp = await client.get("/api/taste/map")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["points"]) == 12
    assert data["projection"]["method"] == "pca"


@pytest.mark.asyncio
async def test_exemplar_neighbors_endpoint(client, synthetic_profile):
    _embeddings, names = synthetic_profile
    resp = await client.get(f"/api/taste/exemplars/{names[0]}/neighbors")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_exemplar_image_rejects_traversal(client, synthetic_profile):
    resp = await client.get("/api/taste/exemplars/..%2f..%2fetc%2fpasswd/image")
    # Either rejected as invalid name (400) or not found (404) — never served.
    assert resp.status_code in (400, 404)
