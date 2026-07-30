"""Tests for backend endpoints added to close frontend API gaps after G2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.main import app
from marquee.models import (
    Movie,
    Series,
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
async def test_library_missing_filter_ignores_tv_runs_in_review(
    db: AsyncSession,
    client: AsyncClient,
):
    """A show awaiting review must not hide every movie missing a poster.

    ``PipelineRun.movie_id`` is NULL for series/season runs, so the exclusion
    used to be an ``id NOT IN (… NULL …)`` that was never true for any row —
    one undecided TV run emptied the movie run queue entirely.
    """
    movie = Movie(
        title="Still Missing",
        year=2021,
        folder_path="/m/missing",
        movie_file_path="missing.mkv",
        tmdb_id=11,
    )
    series = Series(title="Show In Review", series_path="/tv/show", tvdb_id=77, tmdb_id=78)
    db.add_all([movie, series])
    await db.flush()
    run = await seed_canonical_pipeline_run(
        db,
        run_id="tv-review-run",
        series_id=series.id,
        media_type="series",
        archive={"run_id": "tv-review-run", "candidates": []},
    )
    run.started_at = datetime.now(UTC)
    await db.commit()

    resp = await client.get("/api/library/movies?poster_status=missing&exclude_in_review=true")

    assert resp.status_code == 200
    assert [item["title"] for item in resp.json()["items"]] == ["Still Missing"]


@pytest.mark.asyncio
async def test_summary_awaiting_run_matches_the_run_tab_list(
    db: AsyncSession,
    client: AsyncClient,
):
    """The Missing card and the workspace Run tab must never disagree.

    They read the same predicate now, so the count and the list stay in step
    whether nothing is in review, a movie is, or only a show is.
    """
    awaiting = Movie(
        title="Awaiting Run",
        year=2021,
        folder_path="/m/awaiting",
        movie_file_path="awaiting.mkv",
        tmdb_id=21,
    )
    in_review = Movie(
        title="In Review",
        year=2020,
        folder_path="/m/in-review",
        movie_file_path="in-review.mkv",
        tmdb_id=22,
    )
    series = Series(title="Show In Review", series_path="/tv/show", tvdb_id=79, tmdb_id=80)
    db.add_all([awaiting, in_review, series])
    await db.flush()

    async def counts() -> tuple[int, list[str]]:
        summary = await client.get("/api/pipeline/summary")
        listing = await client.get(
            "/api/library/movies?poster_status=missing&exclude_in_review=true&sort=title"
        )
        assert summary.status_code == 200
        assert listing.status_code == 200
        body = listing.json()
        assert summary.json()["movies_awaiting_run"] == body["total"]
        return body["total"], [item["title"] for item in body["items"]]

    await db.commit()
    assert await counts() == (2, ["Awaiting Run", "In Review"])

    movie_run = await seed_canonical_pipeline_run(
        db,
        run_id="movie-review-run",
        movie_id=in_review.id,
        archive={"run_id": "movie-review-run", "movie_id": in_review.id, "candidates": []},
        auto_pick_filename="auto.jpg",
    )
    movie_run.started_at = datetime.now(UTC)
    await db.commit()
    assert await counts() == (1, ["Awaiting Run"])

    tv_run = await seed_canonical_pipeline_run(
        db,
        run_id="tv-review-run",
        series_id=series.id,
        media_type="series",
        archive={"run_id": "tv-review-run", "candidates": []},
    )
    tv_run.started_at = datetime.now(UTC)
    await db.commit()
    assert await counts() == (1, ["Awaiting Run"])


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
        "heal": {"interval_minutes": "not-an-int"},
    }
    resp = await client.put("/api/settings", json=payload)
    assert resp.status_code == 422

    payload = {
        "expected_version": 1,
        "heal": {"interval_minutes": 1},
    }
    resp = await client.put("/api/settings", json=payload)
    assert resp.status_code == 422
