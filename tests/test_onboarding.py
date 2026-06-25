"""Cold-start onboarding tests (design 20) — no ML extras required.

Exercises the onboarding service (state, stratified sampler, progress/completion
gate, taste-test ranking → v3 events + image staging, starter-profile copy) and
the routes (status, taste-test rank, completion gate). Job creation in the
complete endpoint is monkeypatched.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

import marquee.api.routes.onboarding as onb
from marquee.core.pipeline_config import pipeline_settings
from marquee.main import app
from marquee.ml import feedback_store
from marquee.onboarding import service


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """A tmp taste-test bundle + redirected onboarding/feedback paths."""
    tt = tmp_path / "taste_test"
    (tt / "images").mkdir(parents=True)
    movies = []
    for mid in ("a", "b", "c"):
        posters = []
        for i in (1, 2, 3):
            file = f"{mid}{i}.jpg"
            (tt / "images" / file).write_bytes(b"fakeimg")
            posters.append(
                {"file": file, "normalized_features": {"knn_sim": 0.4 + 0.1 * i, "aesthetic": 0.6}}
            )
        movies.append(
            {
                "id": f"tt_{mid}",
                "title": mid.upper(),
                "year": 2000,
                "genres": ["Drama"],
                "posters": posters,
            }
        )
    (tt / "manifest.json").write_text(json.dumps({"model_name": "clip-vit-b-32", "movies": movies}))

    monkeypatch.setattr(pipeline_settings, "ONBOARDING_TASTE_TEST_DIR", tt)
    monkeypatch.setattr(pipeline_settings, "ONBOARDING_STATE_PATH", tmp_path / "onb" / "state.json")
    monkeypatch.setattr(pipeline_settings, "FEEDBACK_LABELS_PATH", tmp_path / "labels.jsonl")
    monkeypatch.setattr(pipeline_settings, "TRAINING_DATA_DIR", tmp_path / "pos")
    monkeypatch.setattr(pipeline_settings, "NEGATIVE_DATA_DIR", tmp_path / "neg")
    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_PATH", tmp_path / "profile.npz")
    monkeypatch.setattr(pipeline_settings, "ONBOARDING_SEED_PROFILE_PATH", tmp_path / "seed.npz")
    monkeypatch.setattr(pipeline_settings, "ONBOARDING_RANK_TEST_MIN", 2)
    monkeypatch.setattr(pipeline_settings, "ONBOARDING_RANK_TEST_GOAL", 3)
    monkeypatch.setattr(pipeline_settings, "ONBOARDING_RANK_TEST_MAX", 4)
    return tt


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Service units
# ---------------------------------------------------------------------------


def test_stratified_sample_spreads_genres():
    movies = [(1, ["Action"]), (2, ["Action"]), (3, ["Drama"]), (4, ["Drama"]), (5, ["Comedy"])]
    # First three picks should hit three different genres, not 1/2 Action.
    assert service.stratified_sample(movies, 3) == [1, 3, 5]
    assert sorted(service.stratified_sample(movies, 99)) == [1, 2, 3, 4, 5]


def test_taste_test_rank_writes_event_and_stages(bundle):
    service.start(service.PATH_TASTE_TEST)
    result = service.taste_test_rank("tt_a", [["a1.jpg"]], ["a3.jpg"])

    rows = feedback_store.read_all()
    assert len(rows) == 1
    row = rows[0]
    assert row["v"] == 3 and row["type"] == "ranking" and row["source"] == "taste_test"
    assert row["movie_id"] == "tt_a"
    buckets = {c["orig_filename"]: c["bucket"] for c in row["candidates"]}
    assert buckets == {"a1.jpg": "fav", "a2.jpg": "indiff", "a3.jpg": "hate"}
    # Favorite staged to positives, hated to negatives.
    assert result["favorites_exemplars"]
    assert (pipeline_settings.TRAINING_DATA_DIR / result["favorites_exemplars"][0]).is_file()
    assert (pipeline_settings.NEGATIVE_DATA_DIR / result["negatives_added"][0]).is_file()


def test_rerank_replaces_prior_event(bundle):
    service.start(service.PATH_TASTE_TEST)
    service.taste_test_rank("tt_a", [["a1.jpg"]], [])
    service.taste_test_rank("tt_a", [["a2.jpg"]], [])  # re-rank same movie
    rows = [r for r in feedback_store.read_all() if r.get("movie_id") == "tt_a"]
    assert len(rows) == 1  # prior replaced
    assert rows[0]["favorites"] == [["a2.jpg"]]


def test_progress_and_completion_gate(bundle):
    service.start(service.PATH_TASTE_TEST)
    assert service.progress()["can_complete"] is False
    service.taste_test_rank("tt_a", [["a1.jpg"]], [])
    service.taste_test_rank("tt_b", [["b1.jpg"]], [])
    prog = service.progress()
    assert prog["ranked"] == 2 and prog["min"] == 2
    assert prog["can_complete"] is True


def test_ensure_starter_profile_copies_seed(bundle):
    pipeline_settings.ONBOARDING_SEED_PROFILE_PATH.write_bytes(b"seed-bytes")
    assert not service.profile_present()
    assert service.ensure_starter_profile() is True
    assert service.profile_present()
    # No-op when a profile already exists.
    assert service.ensure_starter_profile() is False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_endpoint_shape(bundle, client):
    resp = await client.get("/api/onboarding/status")
    assert resp.status_code == 200
    data = resp.json()
    for key in (
        "ranked",
        "min",
        "goal",
        "max",
        "complete",
        "needs_onboarding",
        "taste_test_available",
    ):
        assert key in data
    assert data["taste_test_available"] is True


@pytest.mark.asyncio
async def test_taste_test_rank_endpoint(bundle, client):
    service.start(service.PATH_TASTE_TEST)
    resp = await client.post(
        "/api/onboarding/taste-test/rank",
        json={"movie_id": "tt_a", "favorites": [["a1.jpg"]], "hated": ["a3.jpg"]},
    )
    assert resp.status_code == 200
    assert resp.json()["status"]["ranked"] == 1


@pytest.mark.asyncio
async def test_poster_404_for_unknown(bundle, client):
    resp = await client.get("/api/onboarding/taste-test/posters/nope.jpg")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_complete_requires_minimum(bundle, client):
    service.start(service.PATH_TASTE_TEST)
    resp = await client.post("/api/onboarding/complete")
    assert resp.status_code == 400  # 0 ranked < min


@pytest.mark.asyncio
async def test_complete_succeeds_after_minimum(bundle, client, monkeypatch):
    service.start(service.PATH_TASTE_TEST)
    service.taste_test_rank("tt_a", [["a1.jpg"]], [])
    service.taste_test_rank("tt_b", [["b1.jpg"]], [])

    async def _fake_create(*args, **kwargs):
        return object()

    monkeypatch.setattr(onb.job_manager, "create", _fake_create)
    monkeypatch.setattr(onb, "job_summary", lambda job: {"id": "fake"})

    resp = await client.post("/api/onboarding/complete")
    assert resp.status_code == 200
    assert resp.json()["status"]["complete"] is True
