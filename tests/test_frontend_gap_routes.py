"""Tests for backend endpoints added to close frontend API gaps after G2."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.subtitles.config import subtitle_settings
from marquee.main import app
from marquee.models import (
    ArtworkEvent,
    MediaJob,
    MediaJobEvent,
    Movie,
    PipelineRun,
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
        tmdb_id=1,
        poster_path="/m/a/poster.jpg",
        poster_ai_selected=True,
        video_width=3840,
        video_height=1600,
        has_hdr=True,
        has_dv=True,
    )
    bravo = Movie(title="Bravo", year=2021, folder_path="/m/b", tmdb_id=2)
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
    db.add_all(
        [
            Movie(
                title="Dolby",
                year=2020,
                folder_path="/m/d",
                has_hdr=True,
                has_dv=True,
            ),
            Movie(
                title="Hdr",
                year=2021,
                folder_path="/m/h",
                has_hdr=True,
                has_dv=False,
            ),
            Movie(
                title="Sdr",
                year=2022,
                folder_path="/m/s",
                has_hdr=False,
                has_dv=False,
            ),
            Movie(title="Unknown", year=2023, folder_path="/m/u"),
        ]
    )
    await db.commit()

    body = (await client.get("/api/hdr?hdr=hdr10")).json()
    assert body["distribution"] == {
        "dovi": 1,
        "hdr10p": 0,
        "hdr10": 1,
        "sdr": 1,
        "unknown": 1,
    }
    assert body["total"] == 1
    assert [item["title"] for item in body["items"]] == ["Hdr"]
    assert body["items"][0]["hdr"] == "hdr10"


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
        event["id"].startswith("media-job-event:")
        and event["message"] == "Scanning subtitles"
        for event in body["events"]
    )
    assert any(
        event["id"].startswith("artwork:") and event["level"] == "ok"
        for event in body["events"]
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
    assert body["writable"] is False
