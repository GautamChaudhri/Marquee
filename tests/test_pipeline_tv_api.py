from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import Queries
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.routes import feedback as feedback_route
from marquee.api.routes import pipeline_tv as pipeline_tv_route
from marquee.config import settings
from marquee.core.jobs.handlers_poster_mutations import execute_poster_reset
from marquee.database import _get_engine
from marquee.main import app
from marquee.models import Job, JobBatch, Movie, PipelineRun, Season, Series
from tests.support.canonical_poster import seed_canonical_pipeline_run
from tests.test_poster_mutations import _context as _mutation_context


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def installed_pgqueuer(db: AsyncSession) -> Queries:
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queries = Queries.from_asyncpg_connection(raw.driver_connection)
        await queries.install()
        try:
            yield queries
        finally:
            await db.rollback()
            await queries.uninstall()


async def _seed_series(
    db: AsyncSession,
    tmp_path: Path,
    *,
    title: str,
    year: int = 2000,
    tmdb_id: int | None,
    sonarr_id: int,
    show_poster: bool = False,
    seasons: list[dict] | None = None,
) -> tuple[Series, list[Season]]:
    root = tmp_path / title
    root.mkdir(parents=True, exist_ok=True)
    series = Series(
        title=title,
        year=year,
        series_path=str(root),
        sonarr_id=sonarr_id,
        tvdb_id=sonarr_id + 1000,
        tmdb_id=tmdb_id,
    )
    if show_poster:
        poster = root / "show.jpg"
        poster.write_bytes(b"show")
        series.poster_path = str(poster)
        series.poster_user_approved = True
    db.add(series)
    await db.flush()

    created: list[Season] = []
    for spec in seasons or []:
        season = Season(
            series_id=series.id,
            season_number=spec["number"],
            episode_count=spec.get("episode_count", spec.get("episode_file_count", 0)),
            episode_file_count=spec.get("episode_file_count", 0),
        )
        if spec.get("poster"):
            poster = root / f"season{spec['number']:02d}.jpg"
            poster.write_bytes(b"season")
            season.poster_path = str(poster)
            season.poster_user_approved = True
        db.add(season)
        await db.flush()
        created.append(season)

    await db.commit()
    await db.refresh(series)
    for season in created:
        await db.refresh(season)
    return series, created


def _archive_for(
    series: Series, *, season: Season | None = None, status: str = "completed"
) -> dict:
    title = (
        series.title if season is None else f"{series.title} - Season {season.season_number:02d}"
    )
    subject = {"series_id": series.id, "title": title}
    media_type = "series"
    if season is not None:
        media_type = "season"
        subject["season_id"] = season.id
        subject["season_number"] = season.season_number
    archive = {
        "media_type": media_type,
        "subject": subject,
        "title": title,
        "tmdb_id": series.tmdb_id,
        "candidates": [],
    }
    if status == "completed":
        archive["candidates"] = [
            {
                "orig_filename": "auto.jpg",
                "image_path": str(Path(series.series_path) / "auto.jpg"),
                "rank": 1,
                "final_score": 0.9,
                "normalized_features": {"knn_sim": 0.9},
                "raw_features": {"knn_sim": 0.8},
                "extended_features": {},
                "contributions": {},
                "stage_reached": "ranked",
                "rejection_reason": None,
                "stack_rank": 1,
                "stack_pos": 1,
                "stack_label": "1a",
                "stack_size": 1,
            }
        ]
    return archive


async def _seed_run(
    db: AsyncSession,
    tmp_path: Path,
    *,
    run_id: str,
    series: Series,
    season: Season | None = None,
    status: str = "completed",
    reviewed: bool = False,
) -> PipelineRun:
    run = await seed_canonical_pipeline_run(
        db,
        run_id=run_id,
        archive=_archive_for(series, season=season, status=status),
        media_type="season" if season is not None else "series",
        series_id=series.id,
        season_id=season.id if season is not None else None,
        status=status,
        scorer_name="weighted" if status == "completed" else None,
        auto_pick_filename="auto.jpg" if status == "completed" else None,
        feedback_event_id="done" if reviewed else None,
    )
    await db.commit()
    await db.refresh(run)
    return run


@pytest.mark.asyncio
async def test_tv_summary_run_queue_and_batch_scopes(
    db: AsyncSession, client: AsyncClient, tmp_path: Path, installed_pgqueuer: Queries
):
    _alpha, alpha_seasons = await _seed_series(
        db,
        tmp_path,
        title="Alpha Show",
        tmdb_id=100,
        sonarr_id=1,
        seasons=[
            {"number": 0, "episode_file_count": 1, "poster": True},
            {"number": 1, "episode_file_count": 8, "poster": False},
        ],
    )
    await _seed_series(
        db,
        tmp_path,
        title="Beta Show",
        tmdb_id=None,
        sonarr_id=2,
        seasons=[{"number": 1, "episode_file_count": 6, "poster": False}],
    )
    await _seed_series(
        db,
        tmp_path,
        title="Gamma Show",
        tmdb_id=300,
        sonarr_id=3,
        show_poster=True,
        seasons=[{"number": 1, "episode_file_count": 10, "poster": True}],
    )

    summary = await client.get("/api/pipeline/tv/summary")
    assert summary.status_code == 200
    assert summary.json() == {
        **summary.json(),
        "shows_total": 3,
        "shows_with_show_poster": 1,
        "shows_missing_show_poster": 2,
        "seasons_total": 4,
        "seasons_with_poster": 2,
        "seasons_missing_poster": 2,
        "shows_fully_covered": 1,
        "shows_in_review": 0,
    }

    run_queue = await client.get("/api/pipeline/tv/run-queue")
    assert run_queue.status_code == 200
    items = {item["series"]["title"]: item for item in run_queue.json()["items"]}
    assert set(items) == {"Alpha Show", "Beta Show"}
    assert items["Alpha Show"]["assets_to_run"] == [
        {"media_type": "series"},
        {"media_type": "season", "season_id": alpha_seasons[1].id, "number": 1},
    ]

    # The second request overlaps the first active batch, so it queues only the
    # three assets that are not already covered by that parent.
    for payload, expected_count in (({"scope": "missing"}, 2), ({"scope": "all"}, 3)):
        response = await client.post("/api/pipeline/tv/batch", json=payload)
        assert response.status_code == 202, response.text
        parent = await db.get(Job, response.json()["job_id"])
        assert parent is not None
        assert parent.type == "poster_pipeline_tv_batch"
        assert parent.plan["parent_only"] is True
        assert parent.plan["effect_safety"] == "read_only"
        projection = await db.get(JobBatch, parent.id)
        assert projection is not None
        assert projection.sealed_child_total == expected_count
        children = list(
            (await db.execute(select(Job).where(Job.parent_id == parent.id).order_by(Job.id)))
            .scalars()
            .all()
        )
        assert len(children) == expected_count
        assert {child.type for child in children} == {"poster_pipeline"}
        assert {child.subject_kind for child in children} <= {"series", "season"}


@pytest.mark.asyncio
async def test_series_run_all_missing_creates_canonical_season_child(
    db: AsyncSession, client: AsyncClient, tmp_path: Path, installed_pgqueuer: Queries
):
    series, _seasons = await _seed_series(
        db,
        tmp_path,
        title="Missing Only",
        tmdb_id=500,
        sonarr_id=11,
        show_poster=True,
        seasons=[
            {"number": 1, "episode_file_count": 8, "poster": False},
            {"number": 2, "episode_file_count": 8, "poster": True},
        ],
    )

    response = await client.post(f"/api/pipeline/tv/series/{series.id}/run", json={})
    assert response.status_code == 202, response.text
    parent = await db.get(Job, response.json()["job_id"])
    assert parent is not None
    child = await db.scalar(select(Job).where(Job.parent_id == parent.id))
    assert child is not None
    assert child.subject_kind == "season"
    assert child.request["title"] == "Missing Only · Season 1"


@pytest.mark.asyncio
async def test_concurrent_series_runs_create_one_show_and_season_batch(
    db: AsyncSession, client: AsyncClient, tmp_path: Path, installed_pgqueuer: Queries
):
    series, seasons = await _seed_series(
        db,
        tmp_path,
        title="One Click Show",
        tmdb_id=501,
        sonarr_id=12,
        show_poster=False,
        seasons=[{"number": 1, "episode_file_count": 8, "poster": False}],
    )

    first, second = await asyncio.gather(
        client.post(f"/api/pipeline/tv/series/{series.id}/run", json={}),
        client.post(f"/api/pipeline/tv/series/{series.id}/run", json={}),
    )

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    responses = [first.json(), second.json()]
    assert {item["disposition"] for item in responses} == {"created", "reused"}
    assert len({item["job_id"] for item in responses}) == 1

    parent_id = responses[0]["job_id"]
    children = list(
        (await db.execute(select(Job).where(Job.parent_id == parent_id).order_by(Job.id)))
        .scalars()
        .all()
    )
    assert {(child.subject_kind, child.subject_reference) for child in children} == {
        ("series", str(series.id)),
        ("season", str(seasons[0].id)),
    }

    run_queue = await client.get("/api/pipeline/tv/run-queue")
    assert run_queue.status_code == 200
    assert run_queue.json()["items"] == []


@pytest.mark.asyncio
async def test_grouped_concurrent_series_runs_reuse_the_same_active_parent(
    db: AsyncSession,
    client: AsyncClient,
    tmp_path: Path,
    installed_pgqueuer: Queries,
    monkeypatch: pytest.MonkeyPatch,
):
    effective_configuration = pipeline_tv_route.configuration_provider.effective
    monkeypatch.setattr(
        pipeline_tv_route.configuration_provider,
        "effective",
        lambda owner: {
            **effective_configuration(owner),
            **(
                {"POSTER_GROUP_ENABLED": True, "POSTER_GROUP_CHUNK_SIZE": 8}
                if owner == "pipeline"
                else {}
            ),
        },
    )
    series, seasons = await _seed_series(
        db,
        tmp_path,
        title="Grouped One Click",
        tmdb_id=601,
        sonarr_id=22,
        show_poster=False,
        seasons=[{"number": 1, "episode_file_count": 8, "poster": False}],
    )

    first, second = await asyncio.gather(
        client.post(f"/api/pipeline/tv/series/{series.id}/run", json={}),
        client.post(f"/api/pipeline/tv/series/{series.id}/run", json={}),
    )

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    responses = [first.json(), second.json()]
    assert {item["disposition"] for item in responses} == {"created", "reused"}
    assert len({item["job_id"] for item in responses}) == 1

    child = await db.scalar(select(Job).where(Job.parent_id == responses[0]["job_id"]))
    assert child is not None
    assert child.type == "poster_pipeline_group"
    assert child.subject_kind == "poster_subject_group"
    assert {
        member["subject_key"] for member in child.subject_snapshot["members"]
    } == {f"series:{series.id}", f"season:{seasons[0].id}"}


@pytest.mark.asyncio
async def test_grouped_active_tv_assets_from_different_parents_return_409(
    db: AsyncSession,
    client: AsyncClient,
    tmp_path: Path,
    installed_pgqueuer: Queries,
    monkeypatch: pytest.MonkeyPatch,
):
    effective_configuration = pipeline_tv_route.configuration_provider.effective
    monkeypatch.setattr(
        pipeline_tv_route.configuration_provider,
        "effective",
        lambda owner: {
            **effective_configuration(owner),
            **(
                {"POSTER_GROUP_ENABLED": True, "POSTER_GROUP_CHUNK_SIZE": 8}
                if owner == "pipeline"
                else {}
            ),
        },
    )
    first_series, _ = await _seed_series(
        db,
        tmp_path,
        title="Grouped Active A",
        tmdb_id=611,
        sonarr_id=31,
        seasons=[{"number": 1, "episode_file_count": 8}],
    )
    second_series, _ = await _seed_series(
        db,
        tmp_path,
        title="Grouped Active B",
        tmdb_id=612,
        sonarr_id=32,
        seasons=[{"number": 1, "episode_file_count": 8}],
    )
    for series in (first_series, second_series):
        response = await client.post(f"/api/pipeline/tv/series/{series.id}/run", json={})
        assert response.status_code == 202, response.text

    duplicate = await client.post(
        "/api/pipeline/tv/batch",
        json={"scope": "selected", "series_ids": [first_series.id, second_series.id]},
    )

    assert duplicate.status_code == 409
    assert "already active" in duplicate.json()["detail"]


@pytest.mark.asyncio
async def test_review_waits_for_producing_child_and_parent_to_terminalize(
    db: AsyncSession, client: AsyncClient, tmp_path: Path, installed_pgqueuer: Queries
):
    series, _seasons = await _seed_series(
        db,
        tmp_path,
        title="Terminal Batch",
        tmdb_id=502,
        sonarr_id=13,
        show_poster=False,
        seasons=[{"number": 1, "episode_file_count": 8, "poster": False}],
    )
    submission = await client.post(f"/api/pipeline/tv/series/{series.id}/run", json={})
    assert submission.status_code == 202, submission.text
    parent = await db.get(Job, submission.json()["job_id"])
    assert parent is not None

    run = await _seed_run(db, tmp_path, run_id="terminal-show", series=series)
    producing_child = await db.get(Job, run.job_id)
    assert producing_child is not None
    producing_child.parent_id = parent.id
    producing_child.root_id = parent.id
    await db.commit()

    while_parent_active = await client.get("/api/pipeline/tv/review-queue")
    assert while_parent_active.status_code == 200
    assert while_parent_active.json()["total_series"] == 0

    parent.phase = "terminal"
    parent.outcome = "succeeded"
    parent.terminal_at = datetime.now(UTC)
    await db.commit()

    after_parent = await client.get("/api/pipeline/tv/review-queue")
    assert after_parent.status_code == 200
    assert after_parent.json()["total_series"] == 1
    assert after_parent.json()["items"][0]["show_run"]["run_id"] == "terminal-show"


@pytest.mark.asyncio
async def test_tv_review_queue_grouping_and_movie_guard(
    db: AsyncSession, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    movie = Movie(
        title="Movie Only",
        year=2024,
        folder_path=str(tmp_path / "movie"),
        movie_file_path="movie.mkv",
        tmdb_id=42,
    )
    db.add(movie)
    await db.flush()
    movie_run = await seed_canonical_pipeline_run(
        db,
        run_id="movie-review",
        movie_id=movie.id,
        archive={
            "run_id": "movie-review",
            "movie_id": movie.id,
            "title": movie.title,
            "candidates": [{"orig_filename": "auto.jpg", "rank": 1}],
        },
        auto_pick_filename="auto.jpg",
    )
    movie_run.started_at = datetime.now(UTC)
    await db.commit()

    alpha, alpha_seasons = await _seed_series(
        db,
        tmp_path,
        title="Review Alpha",
        tmdb_id=800,
        sonarr_id=21,
        seasons=[{"number": 1, "episode_file_count": 8, "poster": False}],
    )
    beta, _ = await _seed_series(
        db,
        tmp_path,
        title="Review Beta",
        tmdb_id=801,
        sonarr_id=22,
        seasons=[{"number": 1, "episode_file_count": 8, "poster": False}],
    )
    await _seed_run(db, tmp_path, run_id="alpha-show", series=alpha, status="completed")
    await _seed_run(
        db,
        tmp_path,
        run_id="alpha-season",
        series=alpha,
        season=alpha_seasons[0],
        status="flagged_manual",
    )
    await _seed_run(db, tmp_path, run_id="beta-show", series=beta, status="completed")

    queue = await client.get("/api/pipeline/tv/review-queue")
    assert queue.status_code == 200
    body = queue.json()
    assert body["total_series"] == 2
    items = {item["series"]["title"]: item for item in body["items"]}
    assert items["Review Alpha"]["show_run"]["run_id"] == "alpha-show"
    assert items["Review Alpha"]["season_runs"][0]["flagged_no_candidates"] is True
    assert items["Review Beta"]["seasons_only"] is False

    approved: list[str] = []

    async def fake_apply_feedback_request(body, _request, _db):
        approved.append(body.run_id)
        return {"ok": True}

    monkeypatch.setattr(feedback_route, "apply_feedback_request", fake_apply_feedback_request)

    approve = await client.post(
        "/api/pipeline/tv/review-queue/approve-auto",
        json={"deploy": True, "series_id": alpha.id},
        headers={"Idempotency-Key": "poster_deploy:tv-review-alpha"},
    )
    assert approve.status_code == 202
    assert approve.json()["total"] == 2
    assert approve.json()["approved"] == 1
    assert approve.json()["skipped_no_auto"] == 1
    assert approved == ["alpha-show"]

    movie_queue = await client.get("/api/pipeline/review-queue")
    assert movie_queue.status_code == 200
    assert movie_queue.json()["total"] == 1
    assert movie_queue.json()["items"][0]["movie"]["title"] == "Movie Only"


@pytest.mark.asyncio
async def test_use_show_poster_for_season_and_shared_run_results(
    db: AsyncSession,
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed_pgqueuer: Queries,
):
    series, seasons = await _seed_series(
        db,
        tmp_path,
        title="Fallback Show",
        tmdb_id=900,
        sonarr_id=31,
        show_poster=True,
        seasons=[{"number": 1, "episode_file_count": 8, "poster": False}],
    )
    run = await _seed_run(
        db,
        tmp_path,
        run_id="fallback-season",
        series=series,
        season=seasons[0],
        status="flagged_manual",
    )
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])

    resp = await client.post(
        f"/api/pipeline/tv/seasons/{seasons[0].id}/use-show-poster",
        headers={"Idempotency-Key": "poster_deploy:season-use-show-1"},
    )
    assert resp.status_code == 202
    job = await db.get(Job, resp.json()["job_id"])
    assert job.type == "poster_deploy"
    assert job.subject_kind == "season"
    assert job.request["candidate"]["source"] == "subject_artwork"
    assert not (Path(series.series_path) / "season01.jpg").exists()

    await db.refresh(run)
    assert run.feedback_event_id is None

    run_results = await client.get(f"/api/pipeline/runs/{run.run_id}")
    assert run_results.status_code == 200
    payload = run_results.json()
    assert payload["media_type"] == "season"
    assert payload["subject"]["season_id"] == seasons[0].id


@pytest.mark.asyncio
async def test_use_show_poster_for_season_requires_deployed_show_poster(
    db: AsyncSession, client: AsyncClient, tmp_path: Path
):
    series, seasons = await _seed_series(
        db,
        tmp_path,
        title="No Show Poster",
        tmdb_id=901,
        sonarr_id=32,
        show_poster=False,
        seasons=[{"number": 1, "episode_file_count": 8, "poster": False}],
    )

    resp = await client.post(
        f"/api/pipeline/tv/seasons/{seasons[0].id}/use-show-poster",
        headers={"Idempotency-Key": "poster_deploy:missing-show-poster"},
    )
    assert resp.status_code == 409
    assert series.poster_path is None


@pytest.mark.asyncio
async def test_run_queue_withholds_assets_already_waiting_on_review(
    client, db, tmp_path, installed_pgqueuer
) -> None:
    """An analysed asset waits on a decision, not on another run.

    Listing it in both queues invites re-running finished work, which is what the
    poster_path check alone does: the path stays null until the pick is deployed.
    """
    series, seasons = await _seed_series(
        db,
        tmp_path,
        title="Queue Overlap",
        tmdb_id=900,
        sonarr_id=31,
        show_poster=False,
        seasons=[
            {"number": 1, "episode_file_count": 6, "poster": False},
            {"number": 2, "episode_file_count": 6, "poster": False},
        ],
    )

    before = await client.get("/api/pipeline/tv/run-queue")
    assert before.status_code == 200
    assert before.json()["items"][0]["assets_to_run"] == [
        {"media_type": "series"},
        {"media_type": "season", "season_id": seasons[0].id, "number": 1},
        {"media_type": "season", "season_id": seasons[1].id, "number": 2},
    ]

    await _seed_run(db, tmp_path, run_id="overlap-show", series=series, status="completed")
    await _seed_run(
        db, tmp_path, run_id="overlap-s1", series=series, season=seasons[0], status="completed"
    )

    after = await client.get("/api/pipeline/tv/run-queue")
    assert after.status_code == 200
    items = after.json()["items"]
    assert len(items) == 1
    assert items[0]["show_poster_missing"] is False
    assert items[0]["assets_to_run"] == [
        {"media_type": "season", "season_id": seasons[1].id, "number": 2}
    ]

    review_only = await client.post(
        f"/api/pipeline/tv/series/{series.id}/run", json={"include": "show"}
    )
    assert review_only.status_code == 409
    assert "awaiting review" in review_only.json()["detail"]

    partial = await client.post(f"/api/pipeline/tv/series/{series.id}/run", json={})
    assert partial.status_code == 202, partial.text
    partial_children = list(
        (
            await db.execute(
                select(Job).where(Job.parent_id == partial.json()["job_id"]).order_by(Job.id)
            )
        )
        .scalars()
        .all()
    )
    assert [(child.subject_kind, child.subject_reference) for child in partial_children] == [
        ("season", str(seasons[1].id))
    ]

    # Once the review is resolved the asset is eligible for a run again.
    run = await db.scalar(select(PipelineRun).where(PipelineRun.run_id == "overlap-show"))
    run.feedback_event_id = "resolved"
    await db.commit()
    resolved = await client.get("/api/pipeline/tv/run-queue")
    assert resolved.json()["items"][0]["show_poster_missing"] is True


@pytest.mark.asyncio
async def test_poster_reset_returns_a_fully_covered_subject_to_the_run_queue(
    db: AsyncSession,
    client: AsyncClient,
    tmp_path: Path,
    installed_pgqueuer: Queries,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting a deployed poster has to make that asset runnable again.

    The run that chose the deleted poster can still be sitting undecided — the
    "use show poster" fallback and any run without an auto-pick both leave one
    behind. That undecided run withholds the asset from the run queue, so unless the
    reset retires it the season is deleted into a dead end: no poster, no way to run.
    """
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])
    series, seasons = await _seed_series(
        db,
        tmp_path,
        title="Reset Requeue",
        tmdb_id=902,
        sonarr_id=33,
        show_poster=True,
        seasons=[{"number": 1, "episode_file_count": 6, "poster": True}],
    )
    season = seasons[0]
    deployed = Path(series.series_path) / "season01.jpg"
    run = await _seed_run(
        db, tmp_path, run_id="requeue-s1", series=series, season=season, status="flagged_manual"
    )

    covered = await client.get("/api/pipeline/tv/run-queue")
    assert covered.status_code == 200
    assert covered.json()["items"] == []

    context = await _mutation_context(
        db, job_type="poster_reset", request={"target_kind": "season", "target_id": season.id}
    )
    result = await execute_poster_reset(context)
    assert result["outcome"] == "succeeded", result
    assert not deployed.exists()

    await db.refresh(run)
    assert run.feedback_event_id is not None

    requeued = await client.get("/api/pipeline/tv/run-queue")
    assert requeued.status_code == 200
    items = requeued.json()["items"]
    assert len(items) == 1
    # Only the deleted asset comes back — the show poster is untouched.
    assert items[0]["show_poster_missing"] is False
    assert items[0]["assets_to_run"] == [
        {"media_type": "season", "season_id": season.id, "number": 1}
    ]


@pytest.mark.asyncio
async def test_review_queue_groups_season_runs_under_their_series(
    client, db, tmp_path, installed_pgqueuer
) -> None:
    """Season runs must reach the review queue even when only the season ran."""
    series, seasons = await _seed_series(
        db,
        tmp_path,
        title="Seasons Only",
        tmdb_id=901,
        sonarr_id=32,
        show_poster=True,
        seasons=[{"number": 3, "episode_file_count": 6, "poster": False}],
    )
    await _seed_run(
        db, tmp_path, run_id="seasons-only-s3", series=series, season=seasons[0], status="completed"
    )

    queue = await client.get("/api/pipeline/tv/review-queue")

    assert queue.status_code == 200
    body = queue.json()
    assert body["total_series"] == 1
    item = body["items"][0]
    assert item["seasons_only"] is True
    assert [entry["season_number"] for entry in item["season_runs"]] == [3]
    assert item["season_runs"][0]["run"]["run_id"] == "seasons-only-s3"
