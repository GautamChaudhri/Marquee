"""Tests for backend endpoints added to close frontend API gaps after G2."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.subtitles.config import subtitle_settings
from marquee.main import app
from marquee.models import (
    DoviState,
    Movie,
    RadarrCustomFormat,
    RadarrProfileFormatItem,
    RadarrQualityProfile,
)
from tests.support.canonical_poster import seed_canonical_pipeline_run


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_review_queue_latest_unreviewed_run_per_movie(
    db: AsyncSession,
    client: AsyncClient,
):
    now = datetime.now(UTC)
    alpha = Movie(
        title="Alpha",
        year=2020,
        folder_path="/m/a",
        movie_file_path="alpha.mkv",
        tmdb_id=1,
        poster_path="/m/a/poster.jpg",
        poster_ai_selected=True,
        video_width=3840,
        video_height=1600,
        has_hdr=True,
        has_dv=True,
    )
    bravo = Movie(
        title="Bravo", year=2021, folder_path="/m/b", movie_file_path="bravo.mkv", tmdb_id=2
    )
    db.add_all([alpha, bravo])
    await db.flush()
    old = await seed_canonical_pipeline_run(
        db,
        run_id="old-alpha",
        movie_id=alpha.id,
        archive={"run_id": "old-alpha", "movie_id": alpha.id, "candidates": []},
    )
    old.started_at = now - timedelta(hours=2)
    reviewed = await seed_canonical_pipeline_run(
        db,
        run_id="new-alpha-reviewed",
        movie_id=alpha.id,
        archive={"run_id": "new-alpha-reviewed", "movie_id": alpha.id, "candidates": []},
        feedback_event_id="event1",
    )
    reviewed.started_at = now - timedelta(hours=1)
    await db.commit()

    resp = await client.get("/api/pipeline/review-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["run"]["run_id"] == "old-alpha"
    assert body["items"][0]["run"]["reviewed"] is False
    assert body["items"][0]["movie"]["hdr"] == "dovi"
    assert body["items"][0]["results_url"] == "/api/pipeline/runs/old-alpha"


@pytest.mark.asyncio
async def test_library_missing_filter_can_exclude_review_queue_movies(
    db: AsyncSession,
    client: AsyncClient,
):
    now = datetime.now(UTC)
    review = Movie(
        title="Needs Review",
        year=2020,
        folder_path="/m/review",
        movie_file_path="review.mkv",
        tmdb_id=10,
    )
    missing = Movie(
        title="Still Missing",
        year=2021,
        folder_path="/m/missing",
        movie_file_path="missing.mkv",
        tmdb_id=11,
    )
    db.add_all([review, missing])
    await db.flush()
    run = await seed_canonical_pipeline_run(
        db,
        run_id="review-run",
        movie_id=review.id,
        archive={"run_id": "review-run", "movie_id": review.id, "candidates": []},
        auto_pick_filename="auto.jpg",
    )
    run.started_at = now
    await db.commit()

    resp = await client.get("/api/library/movies?poster_status=missing&exclude_in_review=true")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert [item["title"] for item in body["items"]] == ["Still Missing"]


@pytest.mark.asyncio
async def test_hdr_distribution_and_filter(db: AsyncSession, client: AsyncClient):
    now = datetime.now(UTC)
    db.add_all(
        [
            RadarrCustomFormat(
                id=15,
                name="Dolby Vision",
                include_when_renaming=False,
                specifications_json=[],
                synced_at=now,
            ),
            RadarrCustomFormat(
                id=20,
                name="HDR10+",
                include_when_renaming=False,
                specifications_json=[],
                synced_at=now,
            ),
            RadarrQualityProfile(
                id=3,
                name="UHD",
                upgrade_allowed=True,
                cutoff_format_score=100,
                min_format_score=0,
                synced_at=now,
            ),
        ]
    )
    await db.flush()
    db.add_all(
        [
            RadarrProfileFormatItem(profile_id=3, custom_format_id=15, score=15),
            RadarrProfileFormatItem(profile_id=3, custom_format_id=20, score=10),
        ]
    )
    await db.flush()
    db.add_all(
        [
            Movie(
                title="Dolby",
                year=2020,
                folder_path="/m/d",
                movie_file_path="dolby.mkv",
                quality_profile_id=3,
                quality_cutoff_met=True,
                current_cf_score=25,
                hdr_type_raw="DV HDR10",
                has_hdr=True,
                has_dv=True,
            ),
            Movie(
                title="Hdr",
                year=2021,
                folder_path="/m/h",
                movie_file_path="hdr.mkv",
                quality_profile_id=3,
                quality_cutoff_met=False,
                current_cf_score=10,
                hdr_type_raw="HDR10Plus",
                has_hdr=True,
                has_dv=False,
            ),
            Movie(
                title="Sdr",
                year=2022,
                folder_path="/m/s",
                movie_file_path="sdr.mkv",
                hdr_type_raw="SDR",
                has_hdr=False,
                has_dv=False,
            ),
            Movie(title="Unknown", year=2023, folder_path="/m/u"),
        ]
    )
    await db.commit()

    hdr_movie = (await db.execute(select(Movie).where(Movie.title == "Hdr"))).scalar_one()
    db.add(
        DoviState(
            movie_id=hdr_movie.id,
            status="analyzed",
            dovi_profile=8,
            bl_signal_compatibility_id=1,
        )
    )
    await db.commit()

    body = (await client.get("/api/hdr?hdr_tags=hdr10p")).json()
    assert body["distribution"] == {
        "sdr": 1,
        "hdr": 0,
        "hdr10": 1,
        "hdr10p": 1,
        "dovi": 1,
        "dovi_no_fallback": 0,
    }
    assert body["total"] == 1
    assert [item["title"] for item in body["items"]] == ["Hdr"]
    assert body["items"][0]["hdr"] == "hdr10p"
    assert body["items"][0]["hdr_tags"] == ["hdr10p"]
    assert body["items"][0]["dovi_status"] == "analyzed"
    assert body["items"][0]["dovi_profile"] == 8
    assert body["items"][0]["dovi_bl_signal_compatibility_id"] == 1
    assert body["items"][0]["profile_name"] == "UHD"
    assert body["items"][0]["cf_score"] == 10
    assert body["items"][0]["profile_targets"] == ["hdr10p", "dovi"]
    assert body["items"][0]["available_preference_targets"] == [
        "sdr",
        "hdr10p",
        "dovi_no_fallback",
        "dovi_fallback",
    ]
    assert body["items"][0]["meet_target"] == "sdr"
    assert body["items"][0]["exceed_target"] == "dovi_fallback"
    assert body["items"][0]["preference_status"] == "meets_target"
    assert body["profile_preferences"] == [
        {
            "profile_id": 3,
            "profile_name": "UHD",
            "profile_targets": ["hdr10p", "dovi"],
            "available_preference_targets": [
                "sdr",
                "hdr10p",
                "dovi_no_fallback",
                "dovi_fallback",
            ],
            "meet_target": "sdr",
            "exceed_target": "dovi_fallback",
            "excluded_targets": [],
        }
    ]


@pytest.mark.asyncio
async def test_settings_redacts_secrets(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "RADARR_URL", "http://radarr.local")
    monkeypatch.setattr(settings, "RADARR_API_KEY", "radarr-secret")
    monkeypatch.setattr(subtitle_settings, "SUBGEN_URL", "http://subgen.local")
    monkeypatch.setattr(subtitle_settings, "SUBGEN_CALLBACK_TOKEN", "subgen-secret")

    body = (await client.get("/api/settings")).json()
    assert body["app"]["debug"] is True
    assert body["integrations"]["radarr"]["configured"] is True
    assert body["integrations"]["radarr"]["api_key_configured"] is True
    assert body["integrations"]["subgen"]["callback_token_configured"] is True
    assert body["posters"]["restore_method"] == settings.POSTER_RESTORE_METHOD
    assert body["posters"]["backup_dir"] == settings.POSTER_BACKUP_DIR
    serialized = json.dumps(body)
    assert "radarr-secret" not in serialized
    assert "subgen-secret" not in serialized
    assert "http://radarr.local" not in serialized
    assert body["writable"] is True
    assert body["configuration_meta"]["SUBGEN_URL"] == {
        "owner": "database",
        "apply_mode": "next_job",
        "sensitivity": "public",
        "scope": "execution",
    }
    assert body["configuration_meta"]["SUBGEN_CALLBACK_TOKEN"] == {
        "owner": "environment",
        "apply_mode": "restart",
        "sensitivity": "secret",
        "scope": "execution",
    }
    assert body["configuration_meta"]["SUBGEN_WHISPER_MODEL"]["owner"] == "environment"
    assert body["configuration_meta"]["SUBGEN_WHISPER_MODEL"]["apply_mode"] == "restart"


@pytest.mark.asyncio
async def test_put_settings_success(db: AsyncSession, client: AsyncClient):
    original_enabled = subtitle_settings.SUBTITLE_ENABLED
    payload = {
        "expected_version": 1,
        "subtitles": {
            "enabled": False,
            "scan_concurrency": 5,
            "preferred_languages": ["en", "es", "fr"],
            "preferred_audio_languages": ["en"],
            "preferred_subtitle_languages": ["en", "fr"],
        },
        "subgen": {
            "url": "http://whisper.service:9000",
            "mode": "translate",
        },
    }

    resp = await client.put("/api/settings", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["configuration_version"] == 2
    assert "SUBTITLE_ENABLED" in data["applied"]
    assert "SUBTITLE_SCAN_CONCURRENCY" in data["applied"]
    assert "SUBTITLE_PREFERRED_LANGUAGES" in data["applied"]
    assert "SUBTITLE_PREFERRED_AUDIO_LANGUAGES" in data["applied"]
    assert "SUBTITLE_PREFERRED_SUBTITLE_LANGUAGES" in data["applied"]
    assert "SUBGEN_URL" in data["applied"]
    assert "SUBGEN_MODE" in data["applied"]
    assert data["settings"]["subtitles"]["enabled"] is False
    assert data["settings"]["integrations"]["subgen"]["mode"] == "translate"

    # The environment singleton remains immutable.
    assert original_enabled == subtitle_settings.SUBTITLE_ENABLED


@pytest.mark.asyncio
async def test_put_settings_stale_version_returns_current_metadata(
    db: AsyncSession, client: AsyncClient
):
    winner = await client.put(
        "/api/settings",
        json={"expected_version": 1, "subtitles": {"preferred_languages": ["en", "fr"]}},
    )
    assert winner.status_code == 200

    stale = await client.put(
        "/api/settings",
        json={"expected_version": 1, "subtitles": {"preferred_languages": ["de"]}},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "configuration_version_conflict",
        "current_version": 2,
        "etag": winner.json()["etag"],
    }
    current = (await client.get("/api/settings")).json()
    assert current["configuration_version"] == 2
    assert current["subtitles"]["preferred_languages"] == ["en", "fr"]


@pytest.mark.asyncio
async def test_audio_subs_preferences_uses_version_contract(db: AsyncSession, client: AsyncClient):
    winner = await client.put(
        "/api/audio-subs/preferences",
        json={"expected_version": 1, "preferred_languages": ["en", "es"]},
    )
    assert winner.status_code == 200
    assert winner.json()["configuration_version"] == 2

    stale = await client.put(
        "/api/audio-subs/preferences",
        json={"expected_version": 1, "preferred_languages": ["de"]},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_version"] == 2


@pytest.mark.asyncio
async def test_subgen_settings_uses_version_contract(db: AsyncSession, client: AsyncClient):
    winner = await client.put(
        "/api/subtitle-generators/subgen/settings",
        json={"expected_version": 1, "url": "http://subgen.internal:9000"},
    )
    assert winner.status_code == 200
    assert winner.json()["configuration_version"] == 2

    stale = await client.put(
        "/api/subtitle-generators/subgen/settings",
        json={"expected_version": 1, "mode": "translate"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_version"] == 2


@pytest.mark.asyncio
async def test_put_settings_rejects_secret_mutation(db: AsyncSession, client: AsyncClient):
    resp = await client.put(
        "/api/settings",
        json={
            "expected_version": 1,
            "subgen": {"callback_token": "must-not-persist"},
        },
    )
    assert resp.status_code == 400
    assert "secret" in resp.json()["detail"].lower()
    assert (await client.get("/api/settings")).json()["configuration_version"] == 1


@pytest.mark.asyncio
async def test_put_settings_persists_poster_and_heal_overrides(
    db: AsyncSession, client: AsyncClient
):
    resp = await client.put(
        "/api/settings",
        json={
            "expected_version": 1,
            "posters": {
                "movie_poster_format": "{movie_basename}-poster",
                "restore_method": "local",
            },
            "heal": {"enabled": False, "interval_minutes": 15},
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["configuration_version"] == 2
    assert "MOVIE_POSTER_FORMAT" in data["applied"]
    assert "POSTER_RESTORE_METHOD" in data["applied"]
    assert "HEAL_ENABLED" in data["applied"]
    assert "HEAL_INTERVAL_MINUTES" in data["applied"]
    assert data["settings"]["poster_formats"]["movie"] == "{movie_basename}-poster"
    assert data["settings"]["posters"]["restore_method"] == "local"
    assert data["settings"]["sync"]["heal_enabled"] is False
    assert data["settings"]["sync"]["heal_interval_minutes"] == 15


@pytest.mark.asyncio
async def test_put_settings_rejects_invalid_poster_format(db: AsyncSession, client: AsyncClient):
    resp = await client.put(
        "/api/settings",
        json={
            "expected_version": 1,
            "posters": {"movie_poster_format": "../poster"},
        },
    )

    assert resp.status_code == 400
    current = (await client.get("/api/settings")).json()
    assert current["configuration_version"] == 1


@pytest.mark.asyncio
async def test_put_settings_validation_failure(db: AsyncSession, client: AsyncClient):
    payload = {
        "expected_version": 1,
        "subtitles": {"scan_concurrency": "not-an-int"},
    }
    resp = await client.put("/api/settings", json=payload)
    assert resp.status_code == 422

    payload = {
        "expected_version": 1,
        "subtitles": {"preferred_languages": "not-a-list"},
    }
    resp = await client.put("/api/settings", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_extract_subtitle_track_endpoint(client: AsyncClient):
    response = await client.post("/api/media-files/1/subtitles/track-1/extract")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["header", "Idempotency-Key"]


@pytest.mark.asyncio
async def test_scan_library_subtitles_endpoint(db: AsyncSession, client: AsyncClient):
    # With no media files the fixed batch seals empty and terminalizes no_change (202).
    response = await client.post("/api/subtitles/scan-library", json={})
    assert response.status_code == 202
    body = response.json()
    assert body["disposition"] == "created"
    assert body["phase"] == "terminal"
    assert body["snapshot_url"] == f"/api/jobs/{body['job_id']}/snapshot"
