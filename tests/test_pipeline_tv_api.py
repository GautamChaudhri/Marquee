from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import Queries
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.routes import feedback as feedback_route
from marquee.config import settings
from marquee.database import _get_engine
from marquee.main import app
from marquee.models import Job, JobBatch, Movie, PipelineRun, Season, Series


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


def _archive_for(series: Series, *, season: Season | None = None, status: str = "completed") -> dict:
    title = series.title if season is None else f"{series.title} - Season {season.season_number:02d}"
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
        archive["official_pick"] = {"enabled": True, "applied": "primary_stack"}
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
    archive_path = tmp_path / f"{run_id}.json"
    archive_path.write_text(json.dumps(_archive_for(series, season=season, status=status)))
    run = PipelineRun(
        run_id=run_id,
        media_type="season" if season is not None else "series",
        series_id=series.id,
        season_id=season.id if season is not None else None,
        status=status,
        scorer_name="weighted" if status == "completed" else None,
        archive_path=str(archive_path),
        output_dir=str(tmp_path / run_id),
        auto_pick_filename="auto.jpg" if status == "completed" else None,
        feedback_event_id="done" if reviewed else None,
    )
    db.add(run)
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

    for payload, expected_count in (({"scope": "missing"}, 2), ({"scope": "all"}, 5)):
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
    db.add(
        PipelineRun(
            run_id="movie-review",
            movie_id=movie.id,
            media_type="movie",
            status="completed",
            scorer_name="weighted",
            auto_pick_filename="auto.jpg",
            started_at=datetime.now(UTC),
        )
    )
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
    assert items["Review Alpha"]["season_runs"][0]["official_pick"] is None
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
