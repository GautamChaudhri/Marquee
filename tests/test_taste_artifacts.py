from __future__ import annotations

import json
from urllib.parse import quote

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from marquee.core.pipeline_config import pipeline_settings
from marquee.main import app
from marquee.ml import artifact_registry
from marquee.ml.artifact_codec import unicode_array, unicode_scalar
from marquee.models import ArtifactSnapshot, Job


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def managed_profile(tmp_path, monkeypatch):
    rng = np.random.default_rng(7)
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
    monkeypatch.setattr(pipeline_settings, "TRAINING_DATA_DIR", tmp_path / "training")
    monkeypatch.setattr(pipeline_settings, "LEARNED_HEAD_PATH", tmp_path / "learned_head.clip-vit-b-32.npz")

    from marquee.config import settings

    monkeypatch.setattr(
        type(settings),
        "poster_cache_path",
        property(lambda self: tmp_path / "cache" / "posters"),
    )
    return [str(x) for x in names.tolist()]


@pytest.fixture
def managed_head(tmp_path, monkeypatch):
    head_path = tmp_path / "learned_head.clip-vit-b-32.npz"
    np.savez(
        head_path,
        feature_names=unicode_array(["aesthetic", "knn_sim", "title_balance"]),
        weights=np.asarray([0.3, 0.8, -0.2], dtype=np.float64),
        bias=np.float64(0.1),
        model_name=unicode_scalar("clip-vit-b-32"),
        n_samples=np.int64(42),
        train_accuracy=np.float64(0.91),
        trained_at=unicode_scalar("2026-07-03T00:00:00+00:00"),
    )
    monkeypatch.setattr(pipeline_settings, "LEARNED_HEAD_PATH", head_path)
    # Isolate the feedback labels off the operator's data dir, and seed one
    # label referencing a movie absent from the (empty) test library. The head
    # backfill must link it durably (title snapshot, NULL movie_id) rather than
    # insert a dangling artifact_snapshot_movies FK.
    labels_path = tmp_path / "feedback" / "labels.jsonl"
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    labels_path.write_text(
        json.dumps({"movie_id": 4242, "title": "Retired Movie", "year": 2021, "label": "positive"})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline_settings, "FEEDBACK_LABELS_PATH", labels_path)


@pytest.mark.asyncio
async def test_profiles_endpoint_backfills_active_profile(client, db, managed_profile):
    resp = await client.get("/api/taste/profiles")
    assert resp.status_code == 200, resp.text
    profiles = resp.json()["profiles"]
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile["status"] == "active"
    assert profile["summary"]["exemplars"] == 12
    assert profile["summary"]["unique_movies"] == 12

    detail = await client.get(f"/api/taste/profiles/{profile['id']}")
    assert detail.status_code == 200
    assert len(detail.json()["movies"]) == 12


@pytest.mark.asyncio
async def test_heads_endpoint_backfills_active_head(client, db, managed_profile, managed_head):
    resp = await client.get("/api/taste/heads")
    assert resp.status_code == 200
    heads = resp.json()["heads"]
    assert len(heads) == 1
    head = heads[0]
    assert head["status"] == "active"
    assert head["summary"]["sample_count"] == 42

    detail = await client.get(f"/api/taste/heads/{head['id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["summary"]["top_features"][0]["name"] == "knn_sim"
    # The seeded label references a movie absent from the library: the backfill
    # links it durably by title snapshot with a NULL movie_id (no dangling FK).
    retired = next(m for m in detail_body["movies"] if m["title"] == "Retired Movie")
    assert retired["movie_id"] is None
    assert retired["year"] == 2021


@pytest.mark.asyncio
async def test_delete_active_profile_conflict(client, db, managed_profile):
    profiles = (await client.get("/api/taste/profiles")).json()["profiles"]
    resp = await client.delete(f"/api/taste/profiles/{profiles[0]['id']}")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_delete_profile_exemplar_schedules_map_successor(
    client, db, managed_profile, installed_pgqueuer
):
    profiles = (await client.get("/api/taste/profiles")).json()["profiles"]
    profile_id = profiles[0]["id"]
    exemplars = (await client.get(f"/api/taste/profiles/{profile_id}/exemplars")).json()["exemplars"]
    target = exemplars[0]["name"]

    resp = await client.delete(
        f"/api/taste/profiles/{profile_id}/exemplars/{quote(target, safe='')}"
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    successor = payload["successor_job"]
    assert successor["disposition"] == "created"

    job = await db.get(Job, successor["job_id"])
    assert job is not None
    assert job.subject_kind == "model_profile_training"
    assert job.subject_reference == "taste_map:movies"
    assert job.request["profile_revision"] == profile_id
    assert job.request["trigger_reference"] == f"exemplar_deleted:{target}"

    detail = await client.get(f"/api/taste/profiles/{profile_id}")
    assert detail.status_code == 200
    assert detail.json()["summary"]["exemplars"] == 11



@pytest.mark.asyncio
async def test_taste_status_falls_back_when_registry_schema_missing(
    client, db, managed_profile, monkeypatch
):
    async def unavailable(_db):
        return {"available": False, "reason": artifact_registry.REGISTRY_REASON_MISSING_SCHEMA}

    monkeypatch.setattr(artifact_registry, "registry_status", unavailable)
    resp = await client.get("/api/taste/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["artifact_registry"] == {
        "available": False,
        "reason": artifact_registry.REGISTRY_REASON_MISSING_SCHEMA,
    }
    assert payload["active_profile"] is None
    assert payload["active_head"] is None
    assert "labels" in payload
    assert "exemplars" in payload


@pytest.mark.asyncio
async def test_artifact_lists_return_empty_when_registry_schema_missing(
    client, db, managed_profile, managed_head, monkeypatch
):
    async def unavailable(_db):
        return {"available": False, "reason": artifact_registry.REGISTRY_REASON_MISSING_SCHEMA}

    monkeypatch.setattr(artifact_registry, "registry_status", unavailable)

    profiles = await client.get("/api/taste/profiles")
    assert profiles.status_code == 200
    assert profiles.json() == {
        "library": "movies",
        "profiles": [],
        "artifact_registry": {
            "available": False,
            "reason": artifact_registry.REGISTRY_REASON_MISSING_SCHEMA,
        },
    }

    heads = await client.get("/api/taste/heads")
    assert heads.status_code == 200
    assert heads.json() == {
        "heads": [],
        "artifact_registry": {
            "available": False,
            "reason": artifact_registry.REGISTRY_REASON_MISSING_SCHEMA,
        },
    }


@pytest.mark.asyncio
async def test_manual_artifact_activation_is_owned_by_the_publication_job(
    client, db, managed_profile, monkeypatch
):
    async def unavailable(_db):
        return {"available": False, "reason": artifact_registry.REGISTRY_REASON_MISSING_SCHEMA}

    monkeypatch.setattr(artifact_registry, "registry_status", unavailable)
    resp = await client.post("/api/taste/profiles/missing/activate")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "activation_is_job_owned"


@pytest.mark.asyncio
async def test_taste_status_normalizes_duplicate_active_profiles(client, db, managed_profile):
    profiles = (await client.get("/api/taste/profiles")).json()["profiles"]
    assert len(profiles) == 1
    existing = profiles[0]

    duplicate = ArtifactSnapshot(
        id="duplicate-active-profile",
        kind=artifact_registry.KIND_TASTE_PROFILE,
        status=artifact_registry.STATUS_ACTIVE,
        label="Duplicate active taste profile",
        # Distinct storage_path: (kind, storage_path) is uniquely constrained, so
        # the second active row models the anomaly with its own storage key.
        storage_path=existing["storage_path"] + ".duplicate",
        active_path=str(pipeline_settings.TASTE_PROFILE_PATH),
        model_name=existing["model_name"],
        sha256="duplicate-hash",
        source_mode="training_dir",
        imported_from_active=False,
        summary={"exemplars": 12, "unique_movies": 12},
    )
    db.add(duplicate)
    await db.commit()

    resp = await client.get("/api/taste/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["active_profile"] is not None

    # Read the persisted status directly. A column query bypasses this session's
    # identity map (expire_on_commit=False), so we observe the normalization the
    # /status request committed from its own session rather than a stale cache.
    statuses = (
        await db.execute(
            select(ArtifactSnapshot.status).where(
                ArtifactSnapshot.kind == artifact_registry.KIND_TASTE_PROFILE
            )
        )
    ).scalars().all()
    active_count = sum(1 for status in statuses if status == artifact_registry.STATUS_ACTIVE)
    assert active_count == 1
