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
    ArtworkEvent,
    DoviState,
    MediaJob,
    MediaJobEvent,
    Movie,
    PipelineRun,
    RadarrCustomFormat,
    RadarrProfileFormatItem,
    RadarrQualityProfile,
)


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
    db.add_all(
        [
            PipelineRun(
                run_id="old-alpha",
                movie_id=alpha.id,
                status="completed",
                started_at=now - timedelta(hours=2),
            ),
            PipelineRun(
                run_id="new-alpha-reviewed",
                movie_id=alpha.id,
                status="completed",
                started_at=now - timedelta(hours=1),
                feedback_event_id="event1",
            ),
            PipelineRun(
                run_id="bravo-running",
                movie_id=bravo.id,
                status="running",
                started_at=now,
            ),
        ]
    )
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
async def test_activity_feed_unifies_sources(db: AsyncSession, client: AsyncClient):
    movie = Movie(title="Heat", year=1995, folder_path="/m/heat", tmdb_id=949)
    db.add(movie)
    await db.flush()
    db.add_all(
        [
            ArtworkEvent(
                movie_id=movie.id,
                action="deploy",
                source="feedback",
                detail=json.dumps({"path": "/m/heat/poster.jpg"}),
            ),
            PipelineRun(
                run_id="run-1",
                movie_id=movie.id,
                status="failed",
                error="boom",
            ),
            MediaJob(
                job_id="job-1",
                operation="subtitle_scan",
                status="running",
                trigger="manual",
            ),
        ]
    )
    await db.flush()
    db.add(
        MediaJobEvent(
            job_id="job-1",
            stage="scan",
            state="running",
            message="Scanning subtitles",
            progress_json={"done": 1, "total": 2},
        )
    )
    await db.commit()

    body = (await client.get("/api/activity")).json()
    by_id = {event["id"]: event for event in body["events"]}
    assert by_id["pipeline-run:run-1"]["level"] == "warn"
    assert by_id["pipeline-run:run-1"]["movie_id"] == movie.id
    assert any(
        event["id"].startswith("media-job-event:") and event["message"] == "Scanning subtitles"
        for event in body["events"]
    )
    assert any(
        event["id"].startswith("artwork:") and event["level"] == "ok" for event in body["events"]
    )


@pytest.mark.asyncio
async def test_settings_redacts_secrets(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "RADARR_URL", "http://radarr.local")
    monkeypatch.setattr(settings, "RADARR_API_KEY", "radarr-secret")
    monkeypatch.setattr(subtitle_settings, "SUBGEN_URL", "http://subgen.local")
    monkeypatch.setattr(subtitle_settings, "SUBGEN_CALLBACK_TOKEN", "subgen-secret")

    body = (await client.get("/api/settings")).json()
    assert body["integrations"]["radarr"]["configured"] is True
    assert body["integrations"]["radarr"]["api_key_configured"] is True
    assert body["integrations"]["subgen"]["callback_token_configured"] is True
    serialized = json.dumps(body)
    assert "radarr-secret" not in serialized
    assert "subgen-secret" not in serialized
    assert "http://radarr.local" not in serialized
    assert body["writable"] is True


@pytest.mark.asyncio
async def test_put_settings_success(client: AsyncClient, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "marquee.core.subtitles.config._overrides_path",
        lambda: tmp_path / "subtitle_overrides.json",
    )

    payload = {
        "subtitles": {
            "enabled": False,
            "scan_concurrency": 5,
            "preferred_languages": ["en", "es", "fr"],
        },
        "subgen": {
            "url": "http://whisper.service:9000",
            "mode": "translate",
            "callback_token": "supersecrettoken",
        },
    }

    resp = await client.put("/api/settings", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert "applied" in data
    assert "SUBTITLE_ENABLED" in data["applied"]
    assert "SUBTITLE_SCAN_CONCURRENCY" in data["applied"]
    assert "SUBTITLE_PREFERRED_LANGUAGES" in data["applied"]
    assert "SUBGEN_URL" in data["applied"]
    assert "SUBGEN_MODE" in data["applied"]
    assert "SUBGEN_CALLBACK_TOKEN" in data["applied"]

    # Verify memory mutation
    assert subtitle_settings.SUBTITLE_ENABLED is False
    assert subtitle_settings.SUBTITLE_SCAN_CONCURRENCY == 5
    assert subtitle_settings.SUBTITLE_PREFERRED_LANGUAGES == ["en", "es", "fr"]
    assert subtitle_settings.SUBGEN_URL == "http://whisper.service:9000"
    assert subtitle_settings.SUBGEN_MODE == "translate"
    assert subtitle_settings.SUBGEN_CALLBACK_TOKEN == "supersecrettoken"

    # Verify file persistence
    overrides_file = tmp_path / "subtitle_overrides.json"
    assert overrides_file.exists()
    content = json.loads(overrides_file.read_text())
    assert content["SUBTITLE_ENABLED"] is False
    assert content["SUBTITLE_SCAN_CONCURRENCY"] == 5
    assert content["SUBTITLE_PREFERRED_LANGUAGES"] == ["en", "es", "fr"]
    assert content["SUBGEN_URL"] == "http://whisper.service:9000"
    assert content["SUBGEN_MODE"] == "translate"
    assert content["SUBGEN_CALLBACK_TOKEN"] == "supersecrettoken"


@pytest.mark.asyncio
async def test_put_settings_validation_failure(client: AsyncClient, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "marquee.core.subtitles.config._overrides_path",
        lambda: tmp_path / "subtitle_overrides.json",
    )

    # pass invalid scan_concurrency (type error)
    payload = {"subtitles": {"scan_concurrency": "not-an-int"}}
    resp = await client.put("/api/settings", json=payload)
    assert resp.status_code == 422

    # pass invalid preferred_languages (type error)
    payload = {"subtitles": {"preferred_languages": "not-a-list"}}
    resp = await client.put("/api/settings", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_extract_subtitle_track_endpoint(
    db: AsyncSession,
    client: AsyncClient,
    tmp_path,
    monkeypatch,
):
    from marquee.models import MediaFile, SubtitleInventory, SubtitleTrack

    # Setup configurations
    monkeypatch.setattr(type(settings), "effective_media_roots", property(lambda self: [tmp_path]))
    monkeypatch.setattr(subtitle_settings, "SUBTITLE_ENABLED", True)

    # Create dummy movie video file on disk
    video_file = tmp_path / "test_movie.mkv"
    video_file.write_bytes(b"mock video data")

    # Create records in database
    movie = Movie(
        title="Test Movie",
        year=2024,
        folder_path=str(tmp_path),
        movie_file_path="test_movie.mkv",
        tmdb_id=123,
    )
    db.add(movie)
    await db.flush()

    media_file = MediaFile(
        source="radarr",
        source_key="radarr:movie-file:123",
        movie_id=movie.id,
        path=str(video_file),
        size_bytes=len(video_file.read_bytes()),
        container="mkv",
        is_active=True,
    )
    db.add(media_file)
    await db.flush()

    inventory = SubtitleInventory(
        media_file_id=media_file.id,
        file_signature="sig",
        container="mkv",
    )
    db.add(inventory)
    await db.flush()

    embedded_track = SubtitleTrack(
        id="embedded-track-id",
        inventory_id=inventory.id,
        source="embedded",
        codec="subrip",
        kind="text",
        language_tag="en",
        stream_index=2,
    )
    external_track = SubtitleTrack(
        id="external-track-id",
        inventory_id=inventory.id,
        source="external",
        codec="subrip",
        kind="text",
        language_tag="fr",
        external_path=str(tmp_path / "test_movie.fr.srt"),
    )
    db.add_all([embedded_track, external_track])
    await db.commit()

    # 1. Success case: extracting embedded track
    resp = await client.post(
        f"/api/media-files/{media_file.id}/subtitles/{embedded_track.id}/extract"
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "queued"

    # Verify that MediaJob was created in DB
    job_id = data["job_id"]
    from sqlalchemy import select

    job = (await db.execute(select(MediaJob).where(MediaJob.job_id == job_id))).scalar_one_or_none()
    assert job is not None
    assert job.operation == "subtitle_extract"
    assert job.status == "confirmed"
    assert job.media_file_id == media_file.id

    # 2. Error case: track does not exist
    resp = await client.post(
        f"/api/media-files/{media_file.id}/subtitles/nonexistent-track/extract"
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Embedded track not found"

    # 3. Error case: track is external
    resp = await client.post(
        f"/api/media-files/{media_file.id}/subtitles/{external_track.id}/extract"
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Embedded track not found"


@pytest.mark.asyncio
async def test_scan_library_subtitles_endpoint(
    db: AsyncSession,
    client: AsyncClient,
):
    from sqlalchemy import select
    from marquee.models import Job

    # Trigger scan library
    resp = await client.post("/api/subtitles/scan-library")
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "queued"

    # Verify that Job was created in DB
    job_id = data["job_id"]
    job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    assert job is not None
    assert job.type == "subtitle_scan_all"
    assert job.status == "queued"
    assert job.payload == {"force": False}


@pytest.mark.asyncio
async def test_subtitle_scan_all_handler(db: AsyncSession):
    from sqlalchemy import select
    from marquee.models import Job, MediaFile, SubtitleInventory, MediaJob, Movie
    from marquee.core.jobs.builtin_handlers import subtitle_scan_all

    # Setup configurations
    movie1 = Movie(
        title="Movie 1", year=2024, folder_path="/tmp/m1", movie_file_path="m1.mkv", tmdb_id=101
    )
    movie2 = Movie(
        title="Movie 2", year=2024, folder_path="/tmp/m2", movie_file_path="m2.mkv", tmdb_id=102
    )
    db.add_all([movie1, movie2])
    await db.flush()

    mf1 = MediaFile(
        source="radarr", source_key="k1", movie_id=movie1.id, path="m1.mkv", is_active=True
    )
    mf2 = MediaFile(
        source="radarr", source_key="k2", movie_id=movie2.id, path="m2.mkv", is_active=True
    )
    db.add_all([mf1, mf2])
    await db.flush()

    # mf1 has a subtitle inventory, mf2 does not
    inv1 = SubtitleInventory(media_file_id=mf1.id, container="mkv")
    db.add(inv1)
    await db.commit()

    # 1. Run without force: should only queue mf2
    job = Job(id="job-id-1", type="subtitle_scan_all", payload={"force": False})
    res = await subtitle_scan_all(job)
    assert res == {"queued_scans": 1}

    # Verify that a MediaJob was created for mf2
    media_jobs = (
        (await db.execute(select(MediaJob).where(MediaJob.media_file_id == mf2.id))).scalars().all()
    )
    assert len(media_jobs) == 1
    assert media_jobs[0].operation == "subtitle_scan"
    assert media_jobs[0].status == "queued"

    # 2. Run with force: should queue both mf1 and mf2
    job2 = Job(id="job-id-2", type="subtitle_scan_all", payload={"force": True})
    res2 = await subtitle_scan_all(job2)
    assert res2 == {"queued_scans": 2}


@pytest.mark.asyncio
async def test_build_argv_heals_null_tool_track_id(db: AsyncSession):
    from marquee.core.subtitles.mutation import _build_argv
    from marquee.models import SubtitleInventory, SubtitleTrack, Movie, MediaFile
    from marquee.core.subtitles.probe import ProbeResult, EmbeddedSub
    from marquee.core.subtitles.adapters.matroska import MatroskaAdapter

    # Create dummy movie & media file
    movie = Movie(
        title="Dummy Movie",
        year=2024,
        folder_path="/tmp/dummy",
        movie_file_path="dummy.mkv",
        tmdb_id=999,
    )
    db.add(movie)
    await db.flush()

    media_file = MediaFile(
        source="radarr", source_key="dummy-key", movie_id=movie.id, path="dummy.mkv", is_active=True
    )
    db.add(media_file)
    await db.flush()

    # Create dummy inventory & tracks
    inv = SubtitleInventory(media_file_id=media_file.id, container="mkv")
    db.add(inv)
    await db.flush()

    track = SubtitleTrack(
        id="embedded-track-id",
        inventory_id=inv.id,
        source="embedded",
        stream_index=2,
        tool_track_id=None,
    )
    db.add(track)
    await db.commit()

    # Create mock source probe with aligned tool_track_id
    source_probe = ProbeResult(
        container="matroska",
        duration_seconds=120.0,
        subtitles=[
            EmbeddedSub(
                stream_index=2,
                codec="subrip",
                kind="text",
                language_raw="eng",
                language_tag="en",
                language_source="metadata",
                title=None,
                is_default=False,
                is_forced=False,
                is_sdh=False,
                is_commentary=False,
                tool_track_id=3,  # Present in probe!
            )
        ],
    )

    from pathlib import Path
    from marquee.core.media_files import ResolvedMediaFile

    resolved = ResolvedMediaFile(
        media_file_id=media_file.id,
        source="radarr",
        path=Path("/tmp/dummy.mkv"),
        size_bytes=100,
        mtime_ns=0,
        st_nlink=1,
        signature="sig",
        container="mkv",
        movie_id=movie.id,
    )
    request = {"inventory_id": inv.id, "track_ids": [track.id]}
    adapter = MatroskaAdapter()

    # Call _build_argv - it should align on the fly and not raise UnsupportedContainerError
    argv, expected_delta, ext_rem = await _build_argv(
        db,
        None,
        "subtitle_remove",
        request,
        source_probe,
        adapter,
        Path("/tmp/dummy_out.mkv"),
        resolved,
    )

    # Check track.tool_track_id is updated
    assert track.tool_track_id == 3
