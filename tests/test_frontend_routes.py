"""Tests for backend endpoints added to close frontend API gaps after G2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.main import app
from marquee.models import (
    Job,
    JobArtifact,
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
    producing_job = await db.get(Job, old.job_id)
    assert producing_job is not None
    producing_job.phase = "running"
    producing_job.outcome = None
    producing_job.terminal_at = None
    await db.commit()

    active = await client.get("/api/pipeline/review-queue")
    assert active.status_code == 200
    assert active.json()["total"] == 0

    producing_job.phase = "terminal"
    producing_job.outcome = "succeeded"
    producing_job.terminal_at = now
    await db.commit()

    resp = await client.get("/api/pipeline/review-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["run"]["run_id"] == "old-alpha"
    assert body["items"][0]["run"]["reviewed"] is False
    assert body["items"][0]["results_url"] == "/api/pipeline/runs/old-alpha"


@pytest.mark.asyncio
async def test_review_preview_and_results_require_a_persisted_auto_pick(
    db: AsyncSession,
    client: AsyncClient,
):
    collecting = Movie(
        title="Collecting",
        year=2020,
        folder_path="/m/collecting",
        movie_file_path="collecting.mkv",
        tmdb_id=31,
    )
    personalized = Movie(
        title="Personalized",
        year=2021,
        folder_path="/m/personalized",
        movie_file_path="personalized.mkv",
        tmdb_id=32,
    )
    db.add_all([collecting, personalized])
    await db.flush()

    await seed_canonical_pipeline_run(
        db,
        run_id="collecting-run",
        movie_id=collecting.id,
        scorer_name="weighted",
        archive={
            "run_id": "collecting-run",
            "movie_id": collecting.id,
            "title": collecting.title,
            "personalization_mode": "collecting",
            "candidates": [{"orig_filename": "neutral.jpg", "rank": 1}],
        },
        auto_pick_filename=None,
    )
    await seed_canonical_pipeline_run(
        db,
        run_id="personalized-run",
        movie_id=personalized.id,
        scorer_name="weighted",
        archive={
            "run_id": "personalized-run",
            "movie_id": personalized.id,
            "title": personalized.title,
            "personalization_mode": "personalized",
            "candidates": [
                {"orig_filename": "rank-one.jpg", "rank": 1},
                {"orig_filename": "persisted-auto.jpg", "rank": 2},
            ],
        },
        auto_pick_filename="persisted-auto.jpg",
    )
    await db.commit()

    queue = await client.get("/api/pipeline/review-queue")
    assert queue.status_code == 200
    previews = {item["movie"]["title"]: item["auto_pick_poster_url"] for item in queue.json()["items"]}
    assert previews["Collecting"] is None
    assert previews["Personalized"].endswith("/persisted-auto.jpg")

    results = await client.get("/api/pipeline/runs/collecting-run")
    assert results.status_code == 200
    body = results.json()
    assert body["review_mode"] == "collecting"
    assert body["auto_pick"] is None
    assert body["scorer"] is None

    personalized_results = await client.get("/api/pipeline/runs/personalized-run")
    assert personalized_results.status_code == 200
    assert personalized_results.json()["review_mode"] == "personalized"
    assert personalized_results.json()["auto_pick"]["orig_filename"] == "persisted-auto.jpg"


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
async def test_movie_run_queue_withholds_active_and_review_work(
    db: AsyncSession,
    client: AsyncClient,
):
    now = datetime.now(UTC)

    def queued_job(
        *,
        job_type: str,
        movie_id: int | None = None,
        parent_id: str | None = None,
        request: dict | None = None,
        phase: str = "queued",
        outcome: str | None = None,
    ) -> Job:
        job_id = uuid4().hex
        return Job(
            id=job_id,
            type=job_type,
            payload_version=1,
            request=request or {},
            phase=phase,
            outcome=outcome,
            desired_state="run",
            fence_token=1,
            root_id=parent_id or job_id,
            parent_id=parent_id,
            trigger_kind="manual",
            feature_area="ai_posters",
            presentation_family="poster_pipeline",
            subject_kind="movie" if movie_id is not None else "poster_subject_group",
            subject_reference=str(movie_id) if movie_id is not None else job_id,
            subject_snapshot={"version": 1, "kind": "movie"},
            terminal_at=now if phase == "terminal" else None,
        )

    movies = [
        Movie(
            title=title,
            year=2020,
            folder_path=f"/m/{index}",
            movie_file_path=f"/m/{index}/{title}.mkv",
            tmdb_id=tmdb_id,
        )
        for index, (title, tmdb_id) in enumerate(
            (
                ("Runnable", 1),
                ("Active Single", 2),
                ("Active Group", 3),
                ("Active Batch", 4),
                ("Needs Review", 5),
                ("Failed Run", 6),
                ("Cancelled Run", 7),
                ("No TMDB", None),
            ),
            start=1,
        )
    ]
    db.add_all(movies)
    await db.flush()
    by_title = {movie.title: movie for movie in movies}

    active_single = queued_job(movie_id=by_title["Active Single"].id, job_type="poster_pipeline")
    active_group = queued_job(
        job_type="poster_pipeline_group",
        request={
            "library": "movies",
            "members": [{"movie_id": by_title["Active Group"].id}],
        },
    )
    active_parent = queued_job(job_type="poster_pipeline_batch")
    completed_child = queued_job(
        job_type="poster_pipeline",
        movie_id=by_title["Active Batch"].id,
        parent_id=active_parent.id,
        phase="terminal",
        outcome="succeeded",
    )
    failed = queued_job(
        job_type="poster_pipeline",
        movie_id=by_title["Failed Run"].id,
        phase="terminal",
        outcome="failed",
    )
    cancelled = queued_job(
        job_type="poster_pipeline",
        movie_id=by_title["Cancelled Run"].id,
        phase="terminal",
        outcome="cancelled",
    )
    db.add_all([active_single, active_group, active_parent, completed_child, failed, cancelled])
    await seed_canonical_pipeline_run(
        db,
        run_id="movie-run-queue-review",
        movie_id=by_title["Needs Review"].id,
        archive={
            "run_id": "movie-run-queue-review",
            "movie_id": by_title["Needs Review"].id,
            "candidates": [],
        },
    )
    await db.commit()

    response = await client.get("/api/pipeline/run-queue")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert [item["title"] for item in body["items"]] == [
        "Cancelled Run",
        "Failed Run",
        "No TMDB",
        "Runnable",
    ]
    assert next(item for item in body["items"] if item["title"] == "No TMDB")["tmdb_id"] is None

    summary = await client.get("/api/pipeline/summary")
    assert summary.status_code == 200
    assert summary.json()["movies_awaiting_run"] == body["total"]


@pytest.mark.asyncio
async def test_review_queue_reset_requeues_movies_and_expires_review_evidence(
    db: AsyncSession,
    client: AsyncClient,
    installed_pgqueuer,
):
    """The movie reset must use the same scoped, durable lifecycle as TV."""
    awaiting = Movie(
        title="Awaiting Reset",
        year=2020,
        folder_path="/m/awaiting-reset",
        movie_file_path="awaiting-reset.mkv",
        tmdb_id=101,
        is_present=True,
    )
    decided = Movie(
        title="Already Decided",
        year=2021,
        folder_path="/m/already-decided",
        movie_file_path="already-decided.mkv",
        tmdb_id=102,
        is_present=True,
        poster_path="/m/already-decided/poster.jpg",
    )
    db.add_all([awaiting, decided])
    await db.flush()
    awaiting_run = await seed_canonical_pipeline_run(
        db,
        run_id="movie-awaiting-reset",
        movie_id=awaiting.id,
        archive={"run_id": "movie-awaiting-reset", "movie_id": awaiting.id, "candidates": []},
    )
    decided_run = await seed_canonical_pipeline_run(
        db,
        run_id="movie-already-decided",
        movie_id=decided.id,
        archive={"run_id": "movie-already-decided", "movie_id": decided.id, "candidates": []},
        feedback_event_id="decision",
    )
    await db.commit()

    response = await client.post(
        "/api/pipeline/review-queue/reset",
        headers={"Idempotency-Key": "poster_deploy_reset:movie-review-queue"},
    )

    assert response.status_code == 202, response.text
    children = (
        (
            await db.execute(
                select(Job).where(
                    Job.parent_id == response.json()["job_id"], Job.type == "poster_reset"
                )
            )
        )
        .scalars()
        .all()
    )
    assert {(job.subject_kind, job.subject_reference) for job in children} == {
        ("movie", str(awaiting.id))
    }

    cleared = await client.get("/api/pipeline/review-queue")
    assert cleared.json()["total"] == 0
    requeued = await client.get("/api/library/movies?poster_status=missing&exclude_in_review=true")
    assert [item["id"] for item in requeued.json()["items"]] == [awaiting.id]

    artifacts = (await db.execute(select(JobArtifact))).scalars().all()
    by_run = {
        artifact.artifact_metadata.get("run_id"): artifact
        for artifact in artifacts
        if isinstance(artifact.artifact_metadata, dict)
    }
    assert by_run[awaiting_run.run_id].expires_at is not None
    assert by_run[awaiting_run.run_id].expires_at <= datetime.now(UTC)
    untouched = by_run[decided_run.run_id].expires_at
    assert untouched is None or untouched > datetime.now(UTC)


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
