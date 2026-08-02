from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.main import app
from marquee.models import Job, Season, Series
from tests.support.canonical_poster import seed_canonical_pipeline_run


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_series(
    db: AsyncSession,
    tmp_path: Path,
    *,
    title: str,
    tmdb_id: int | None,
    sonarr_id: int,
    show_poster: bool = False,
    genres: list[str] | None = None,
    show_text_profile_id: str | None = None,
    season_text_profile_id: str | None = None,
    seasons: list[dict] | None = None,
) -> tuple[Series, list[Season]]:
    root = tmp_path / title
    root.mkdir(parents=True, exist_ok=True)
    series = Series(
        title=title,
        year=2001,
        series_path=str(root),
        tmdb_id=tmdb_id,
        sonarr_id=sonarr_id,
        tvdb_id=sonarr_id + 1000,
        genres=genres,
        season_count=len(seasons or []),
        show_text_profile_id=show_text_profile_id,
        season_text_profile_id=season_text_profile_id,
    )
    if show_poster:
        show_file = root / "show.jpg"
        show_file.write_bytes(b"show")
        series.poster_path = str(show_file)
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
            poster_file = root / f"season{spec['number']:02d}.jpg"
            poster_file.write_bytes(b"season")
            season.poster_path = str(poster_file)
            season.poster_user_approved = True
        db.add(season)
        await db.flush()
        created.append(season)

    await db.commit()
    await db.refresh(series)
    for season in created:
        await db.refresh(season)
    return series, created


@pytest.mark.asyncio
async def test_list_series_filters_visibility_and_computes_rollups(
    db: AsyncSession, client: AsyncClient, tmp_path: Path
):
    partial, _ = await _seed_series(
        db,
        tmp_path,
        title="Partial Show",
        tmdb_id=1,
        sonarr_id=10,
        genres=["Drama", "Mystery"],
        seasons=[
            {"number": 1, "episode_file_count": 8, "poster": False},
            {"number": 0, "episode_file_count": 1, "poster": True},
        ],
    )
    await _seed_series(
        db,
        tmp_path,
        title="Complete Show",
        tmdb_id=2,
        sonarr_id=11,
        show_poster=True,
        seasons=[{"number": 1, "episode_file_count": 8, "poster": True}],
    )
    await _seed_series(
        db,
        tmp_path,
        title="Invisible Show",
        tmdb_id=3,
        sonarr_id=12,
        seasons=[{"number": 1, "episode_file_count": 0, "poster": False}],
    )

    resp = await client.get("/api/library/series")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["review_pending_total"] == 0
    items = {item["title"]: item for item in body["items"]}
    assert set(items) == {"Complete Show", "Partial Show"}
    assert items["Partial Show"]["downloaded_seasons"] == 2
    assert items["Partial Show"]["seasons_with_poster"] == 1
    assert items["Partial Show"]["season_poster_status"] == "partial"
    assert items["Partial Show"]["poster"]["has_poster"] is False
    assert items["Partial Show"]["genres"] == ["Drama", "Mystery"]
    assert [season["season_number"] for season in items["Partial Show"]["seasons"]] == [0, 1]
    assert items["Partial Show"]["seasons"][0]["poster"]["has_poster"] is True
    assert items["Partial Show"]["seasons"][1]["poster"]["has_poster"] is False
    assert items["Complete Show"]["season_poster_status"] == "complete"
    assert items["Complete Show"]["poster"]["has_poster"] is True
    assert items["Complete Show"]["genres"] is None
    assert items["Partial Show"]["id"] == partial.id
    assert items["Partial Show"]["review_pending"] is False


@pytest.mark.asyncio
async def test_series_review_override_counts_each_show_once_and_requires_terminal_work(
    db: AsyncSession, client: AsyncClient, tmp_path: Path
):
    pending, pending_seasons = await _seed_series(
        db,
        tmp_path,
        title="Pending Show",
        tmdb_id=20,
        sonarr_id=120,
        seasons=[
            {"number": 0, "episode_file_count": 1, "poster": False},
            {"number": 1, "episode_file_count": 8, "poster": False},
        ],
    )
    resolved, resolved_seasons = await _seed_series(
        db,
        tmp_path,
        title="Resolved Show",
        tmdb_id=21,
        sonarr_id=121,
        seasons=[{"number": 1, "episode_file_count": 8, "poster": False}],
    )

    await seed_canonical_pipeline_run(
        db,
        run_id="pending-show-run",
        series_id=pending.id,
        media_type="series",
        archive={"run_id": "pending-show-run", "series_id": pending.id, "candidates": []},
    )
    await seed_canonical_pipeline_run(
        db,
        run_id="pending-specials-run",
        series_id=pending.id,
        season_id=pending_seasons[0].id,
        media_type="season",
        archive={
            "run_id": "pending-specials-run",
            "series_id": pending.id,
            "season_id": pending_seasons[0].id,
            "candidates": [],
        },
    )
    await seed_canonical_pipeline_run(
        db,
        run_id="resolved-season-run",
        series_id=resolved.id,
        season_id=resolved_seasons[0].id,
        media_type="season",
        feedback_event_id="resolved-feedback",
        archive={
            "run_id": "resolved-season-run",
            "series_id": resolved.id,
            "season_id": resolved_seasons[0].id,
            "candidates": [],
        },
    )
    nonterminal = await seed_canonical_pipeline_run(
        db,
        run_id="nonterminal-show-run",
        series_id=resolved.id,
        media_type="series",
        archive={
            "run_id": "nonterminal-show-run",
            "series_id": resolved.id,
            "candidates": [],
        },
    )
    nonterminal_job = await db.get(Job, nonterminal.job_id)
    assert nonterminal_job is not None
    nonterminal_job.phase = "running"
    nonterminal_job.outcome = None
    nonterminal_job.terminal_at = None
    await db.commit()

    response = await client.get("/api/library/series")
    assert response.status_code == 200
    body = response.json()
    items = {item["title"]: item for item in body["items"]}
    assert body["review_pending_total"] == 1
    assert items["Pending Show"]["review_pending"] is True
    assert [season["season_number"] for season in items["Pending Show"]["seasons"]] == [0, 1]
    assert items["Resolved Show"]["review_pending"] is False

    detail = await client.get(f"/api/library/series/{pending.id}")
    assert detail.status_code == 200
    assert detail.json()["review_pending"] is True


@pytest.mark.asyncio
async def test_get_series_and_list_seasons_include_downloaded_specials_and_overrides(
    db: AsyncSession, client: AsyncClient, tmp_path: Path
):
    series, seasons = await _seed_series(
        db,
        tmp_path,
        title="Detailed Show",
        tmdb_id=4,
        sonarr_id=20,
        genres=["Science Fiction"],
        show_text_profile_id="show-textless",
        season_text_profile_id="season-title",
        seasons=[
            {"number": 0, "episode_file_count": 1, "poster": True},
            {"number": 1, "episode_file_count": 8, "poster": False},
            {"number": 2, "episode_file_count": 0, "poster": False},
        ],
    )

    detail = await client.get(f"/api/library/series/{series.id}")
    assert detail.status_code == 200
    data = detail.json()
    assert data["show_text_profile_id"] == "show-textless"
    assert data["season_text_profile_id"] == "season-title"
    assert data["genres"] == ["Science Fiction"]
    assert [season["season_number"] for season in data["seasons"]] == [0, 1]
    assert data["downloaded_seasons"] == 2
    assert data["seasons_with_poster"] == 1
    assert data["season_poster_status"] == "partial"

    downloaded = await client.get(f"/api/library/series/{series.id}/seasons")
    assert downloaded.status_code == 200
    assert [season["season_number"] for season in downloaded.json()["seasons"]] == [0, 1]

    all_seasons = await client.get(f"/api/library/series/{series.id}/seasons?downloaded_only=false")
    assert all_seasons.status_code == 200
    assert [season["season_number"] for season in all_seasons.json()["seasons"]] == [0, 1, 2]
    assert seasons[2].episode_file_count == 0


@pytest.mark.asyncio
async def test_series_and_season_poster_file_and_delete_endpoints(
    db: AsyncSession,
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed_pgqueuer,
):
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])
    series, seasons = await _seed_series(
        db,
        tmp_path,
        title="Poster Show",
        tmdb_id=5,
        sonarr_id=30,
        show_poster=True,
        seasons=[{"number": 1, "episode_file_count": 8, "poster": True}],
    )

    show_file = await client.get(f"/api/library/series/{series.id}/poster")
    assert show_file.status_code == 200
    season_file = await client.get(f"/api/library/seasons/{seasons[0].id}/poster")
    assert season_file.status_code == 200

    delete_show = await client.delete(
        f"/api/library/series/{series.id}/poster",
        headers={"Idempotency-Key": "poster_reset:series-reset-1"},
    )
    assert delete_show.status_code == 202
    await db.refresh(series)
    assert series.poster_path is not None
    show_job = await db.get(Job, delete_show.json()["job_id"])
    assert show_job.type == "poster_reset"
    assert show_job.subject_kind == "series"

    delete_season = await client.delete(
        f"/api/library/seasons/{seasons[0].id}/poster",
        headers={"Idempotency-Key": "poster_reset:season-reset-1"},
    )
    assert delete_season.status_code == 202
    await db.refresh(seasons[0])
    assert seasons[0].poster_path is not None
    season_job = await db.get(Job, delete_season.json()["job_id"])
    assert season_job.type == "poster_reset"
    assert season_job.subject_kind == "season"


@pytest.mark.asyncio
async def test_series_poster_endpoints_translate_sonarr_paths_and_reject_outside_files(
    db: AsyncSession,
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    local_tv = tmp_path / "local-tv"
    series, seasons = await _seed_series(
        db,
        local_tv / "1080p",
        title="Translated Poster Show",
        tmdb_id=7,
        sonarr_id=32,
        show_poster=True,
        seasons=[{"number": 1, "episode_file_count": 8, "poster": True}],
    )
    series.series_path = "/plunder/tv/1080p/Translated Poster Show"
    await db.commit()

    monkeypatch.setattr(settings, "SONARR_PATH_PREFIX", "/plunder/tv")
    monkeypatch.setattr(settings, "SONARR_MEDIA_PATH", str(local_tv))
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [])

    show_file = await client.get(f"/api/library/series/{series.id}/poster")
    season_file = await client.get(f"/api/library/seasons/{seasons[0].id}/poster")
    assert show_file.status_code == 200
    assert show_file.content == b"show"
    assert season_file.status_code == 200
    assert season_file.content == b"season"

    outside = tmp_path / "outside-season.jpg"
    outside.write_bytes(b"outside")
    seasons[0].poster_path = str(outside)
    await db.commit()

    rejected = await client.get(f"/api/library/seasons/{seasons[0].id}/poster")
    assert rejected.status_code == 404
    assert outside.read_bytes() == b"outside"


@pytest.mark.asyncio
async def test_series_poster_delete_rejects_poisoned_stored_path(
    db: AsyncSession,
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed_pgqueuer,
):
    media_root = tmp_path / "media"
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"keep")
    series, _ = await _seed_series(
        db,
        media_root,
        title="Poisoned Poster Show",
        tmdb_id=6,
        sonarr_id=31,
        show_poster=True,
        seasons=[{"number": 1, "episode_file_count": 1}],
    )
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(media_root)])
    series.poster_path = str(outside)
    await db.commit()

    response = await client.delete(
        f"/api/library/series/{series.id}/poster",
        headers={"Idempotency-Key": "poster_reset:poisoned-series-reset"},
    )

    assert response.status_code == 202
    assert outside.read_bytes() == b"keep"
    await db.refresh(series)
    assert series.poster_path == str(outside)
