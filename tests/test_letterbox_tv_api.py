"""API tests for Plan 10 phase 3: TV letterbox summary/list/detail + detect/apply actions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.main import app
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    Job,
    JobDispatch,
    LetterboxEvent,
    LetterboxState,
    MediaFile,
    Movie,
    Season,
    Series,
)


@pytest.mark.asyncio
async def test_tv_feature_payloads_do_not_aggregate_active_jobs(client: AsyncClient, tv_library):
    listing = (await client.get("/api/letterbox/tv")).json()
    assert all("active_job_ids" not in item for item in listing["items"])

    detail = (await client.get(f"/api/letterbox/tv/{tv_library['mixed_show'].id}")).json()
    assert "active_job_ids" not in detail


@pytest.mark.asyncio
async def test_tv_preview_submission_uses_canonical_job(
    client: AsyncClient,
    db: AsyncSession,
    installed_pgqueuer,
) -> None:
    series = Series(
        title="Preview show",
        year=2026,
        series_path="/tv/preview",
        sonarr_id=90_000_001,
    )
    db.add(series)
    await db.flush()
    db.add(Season(series_id=series.id, season_number=1, episode_file_count=1))
    await db.flush()
    episode = await _seed_episode_with_state(
        db,
        series=series,
        season_number=1,
        episode_number=1,
        path="/tv/preview/s01e01.mkv",
        state_status="candidate",
        recommended_crop=8,
        video_width=64,
        video_height=48,
    )
    await db.commit()
    episode_id = episode.id
    media_file_id = await db.scalar(
        select(EpisodeMediaFile.media_file_id).where(EpisodeMediaFile.episode_id == episode_id)
    )
    assert media_file_id is not None

    response = await client.post(
        f"/api/letterbox/tv/{series.id}/episodes/{episode_id}/preview"
        "?mode=after&minute=0&exact=true"
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]

    await db.rollback()
    db.expire_all()
    job = await db.get(Job, job_id)
    dispatch = await db.scalar(select(JobDispatch).where(JobDispatch.job_id == job_id))
    assert job is not None and dispatch is not None
    assert job.type == "letterbox_preview"
    assert (job.subject_kind, job.subject_reference) == ("media_file", str(media_file_id))
    assert job.request["episode_id"] == episode_id
    assert job.request["candidate_minutes"] == []
    assert dispatch.entrypoint == "media_read"


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def neutralize_media_roots(monkeypatch):
    from marquee.config import settings

    monkeypatch.setattr(settings, "RADARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "SONARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "RADARR_PATH_PREFIX", None)
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [])
    yield


async def _seed_movie_summary_rows(db: AsyncSession) -> None:
    movie_a = Movie(
        title="Movie Candidate",
        year=2020,
        folder_path="/movies/a",
        movie_file_path="a.mkv",
        tmdb_id=1,
    )
    movie_b = Movie(
        title="Movie Processed",
        year=2021,
        folder_path="/movies/b",
        movie_file_path="b.mkv",
        tmdb_id=2,
    )
    db.add_all([movie_a, movie_b])
    await db.flush()
    db.add_all(
        [
            LetterboxState(
                media_type="movie",
                movie_id=movie_a.id,
                status="candidate",
                confidence="high",
                recommended_crop_top=140,
                recommended_crop_bottom=140,
                aspect_label="2.40:1",
            ),
            LetterboxState(
                media_type="movie",
                movie_id=movie_b.id,
                status="tagged",
                confidence="high",
                recommended_crop_top=132,
                recommended_crop_bottom=132,
                applied_crop_top=132,
                applied_crop_bottom=132,
                reviewed=True,
                aspect_label="2.35:1",
            ),
        ]
    )
    await db.commit()


async def _seed_episode_with_state(
    db: AsyncSession,
    *,
    series: Series,
    season_number: int,
    episode_number: int,
    path: str,
    state_status: str | None,
    reviewed: bool = False,
    aspect_label: str | None = None,
    recommended_crop: int | None = None,
    applied_crop: int | None = None,
    last_detected_at: datetime | None = None,
    resolved_by: str | None = None,
    video_width: int = 1920,
    video_height: int = 1080,
) -> Episode:
    episode = Episode(
        series_id=series.id,
        season_number=season_number,
        episode_number=episode_number,
        title=f"S{season_number:02d}E{episode_number:02d}",
        episode_file_path=path,
        video_width=video_width,
        video_height=video_height,
    )
    db.add(episode)
    await db.flush()
    media_file = MediaFile(
        source="sonarr",
        source_key=f"sonarr:episode-file:{series.id}:{season_number}:{episode_number}",
        path=path,
        relative_path=Path(path).name,
        container="mkv",
        is_active=True,
    )
    db.add(media_file)
    await db.flush()
    db.add(EpisodeMediaFile(episode_id=episode.id, media_file_id=media_file.id))
    if state_status is not None:
        db.add(
            LetterboxState(
                media_type="episode",
                episode_id=episode.id,
                status=state_status,
                confidence="high" if state_status == "candidate" else "none",
                reviewed=reviewed,
                recommended_crop_top=recommended_crop,
                recommended_crop_bottom=recommended_crop,
                applied_crop_top=applied_crop,
                applied_crop_bottom=applied_crop,
                aspect_label=aspect_label,
                source_width=video_width,
                source_height=video_height,
                last_detected_at=last_detected_at,
                resolved_by=resolved_by,
            )
        )
    return episode


async def _seed_dimension_class_show(db: AsyncSession) -> tuple[Series, Episode, Episode]:
    series = Series(title="Wide Show", year=2024, series_path="/tv/wide", sonarr_id=20)
    db.add(series)
    await db.flush()
    db.add(Season(series_id=series.id, season_number=1, episode_file_count=2))
    await db.flush()
    open_matte = await _seed_episode_with_state(
        db,
        series=series,
        season_number=1,
        episode_number=1,
        path="/tv/wide/s01e01.mkv",
        state_status=None,
        video_width=1920,
        video_height=800,
    )
    truth = await _seed_episode_with_state(
        db,
        series=series,
        season_number=1,
        episode_number=2,
        path="/tv/wide/s01e02.mkv",
        state_status="candidate",
        aspect_label="2.40:1",
        recommended_crop=140,
        video_width=1920,
        video_height=800,
    )
    await db.commit()
    return series, open_matte, truth


async def _seed_rollup_show(
    db: AsyncSession,
    *,
    title: str,
    sonarr_id: int,
    episodes: list[tuple[int, str | None, str | None, int, int]],
) -> Series:
    series = Series(title=title, year=2024, series_path=f"/tv/{sonarr_id}", sonarr_id=sonarr_id)
    db.add(series)
    await db.flush()

    season_numbers = sorted({season_number for season_number, *_rest in episodes})
    db.add_all(
        [
            Season(
                series_id=series.id,
                season_number=season_number,
                episode_file_count=sum(1 for episode in episodes if episode[0] == season_number),
            )
            for season_number in season_numbers
        ]
    )
    await db.flush()

    episode_numbers: dict[int, int] = {}
    for season_number, status, aspect_label, width, height in episodes:
        episode_numbers[season_number] = episode_numbers.get(season_number, 0) + 1
        episode_number = episode_numbers[season_number]
        await _seed_episode_with_state(
            db,
            series=series,
            season_number=season_number,
            episode_number=episode_number,
            path=f"/tv/{sonarr_id}/s{season_number:02d}e{episode_number:02d}.mkv",
            state_status=status,
            aspect_label=aspect_label,
            recommended_crop=140 if status == "candidate" else None,
            applied_crop=140 if status in {"tagged", "reencoded"} else None,
            video_width=width,
            video_height=height,
        )
    return series


@pytest_asyncio.fixture
async def tv_library(db: AsyncSession):
    await _seed_movie_summary_rows(db)

    clean_show = Series(title="Clean Show", year=2020, series_path="/tv/clean", sonarr_id=1)
    mixed_show = Series(title="Mixed Show", year=2021, series_path="/tv/mixed", sonarr_id=2)
    db.add_all([clean_show, mixed_show])
    await db.flush()
    db.add_all(
        [
            Season(series_id=clean_show.id, season_number=1, episode_file_count=2),
            Season(series_id=mixed_show.id, season_number=0, episode_file_count=1),
            Season(series_id=mixed_show.id, season_number=1, episode_file_count=4),
        ]
    )
    await db.flush()

    clean_ep1 = await _seed_episode_with_state(
        db,
        series=clean_show,
        season_number=1,
        episode_number=1,
        path="/tv/clean/s01e01.mkv",
        state_status="not_letterboxed",
        last_detected_at=datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
    )
    clean_ep2 = await _seed_episode_with_state(
        db,
        series=clean_show,
        season_number=1,
        episode_number=2,
        path="/tv/clean/s01e02.mkv",
        state_status="sampled_clear",
        last_detected_at=datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
    )

    special = await _seed_episode_with_state(
        db,
        series=mixed_show,
        season_number=0,
        episode_number=1,
        path="/tv/mixed/s00e01.mkv",
        state_status="candidate",
        aspect_label="2.40:1",
        recommended_crop=140,
    )
    ep1 = await _seed_episode_with_state(
        db,
        series=mixed_show,
        season_number=1,
        episode_number=1,
        path="/tv/mixed/s01e01.mkv",
        state_status="candidate",
        aspect_label="2.40:1",
        recommended_crop=140,
    )
    ep2 = await _seed_episode_with_state(
        db,
        series=mixed_show,
        season_number=1,
        episode_number=2,
        path="/tv/mixed/s01e02.mkv",
        state_status="tagged",
        reviewed=False,
        aspect_label="2.40:1",
        recommended_crop=140,
        applied_crop=140,
    )
    ep3 = await _seed_episode_with_state(
        db,
        series=mixed_show,
        season_number=1,
        episode_number=3,
        path="/tv/mixed/s01e03.mkv",
        state_status="tagged",
        reviewed=True,
        aspect_label="2.40:1",
        recommended_crop=140,
        applied_crop=140,
        resolved_by="reencode",
    )
    ep4 = await _seed_episode_with_state(
        db,
        series=mixed_show,
        season_number=1,
        episode_number=4,
        path="/tv/mixed/s01e04.mkv",
        state_status="prefilter_candidate",
    )
    db.add(
        Job(
            id="tv-active-job",
            type="letterbox_detect_tv_scope",
            root_id="tv-active-job",
            request={"series_id": mixed_show.id},
            phase="running",
            subject_kind="series",
            subject_reference=str(mixed_show.id),
            subject_snapshot={"title": mixed_show.title},
        )
    )
    await db.commit()
    return {
        "clean_show": clean_show,
        "clean_ep1": clean_ep1,
        "clean_ep2": clean_ep2,
        "mixed_show": mixed_show,
        "special": special,
        "ep1": ep1,
        "ep2": ep2,
        "ep3": ep3,
        "ep4": ep4,
    }


@pytest_asyncio.fixture
async def tv_filter_library(db: AsyncSession, tv_library):
    await _seed_rollup_show(
        db,
        title="Treated Show",
        sonarr_id=30,
        episodes=[(1, "tagged", "2.39:1", 1920, 1080)],
    )
    await _seed_rollup_show(
        db,
        title="Unanalyzed Show",
        sonarr_id=31,
        episodes=[(1, "prefilter_candidate", None, 1920, 1080)],
    )
    await _seed_rollup_show(
        db,
        title="Open Matte Show",
        sonarr_id=32,
        episodes=[(1, None, None, 1920, 800)],
    )
    await _seed_rollup_show(
        db,
        title="Pillarbox Show",
        sonarr_id=33,
        episodes=[(1, None, None, 1800, 1080)],
    )
    await _seed_rollup_show(
        db,
        title="Clean Mix Show",
        sonarr_id=34,
        episodes=[
            (1, "not_letterboxed", None, 1920, 1080),
            (2, None, None, 1800, 1080),
        ],
    )
    await _seed_rollup_show(
        db,
        title="Dirty Mix Show",
        sonarr_id=35,
        episodes=[
            (1, "not_letterboxed", None, 1920, 1080),
            (1, "candidate", "2.39:1", 1920, 1080),
        ],
    )
    await db.commit()
    return tv_library


@pytest.mark.asyncio
class TestSummary:
    async def test_summary_includes_movies_and_tv_sections(self, client: AsyncClient, tv_library):
        body = (await client.get("/api/letterbox/summary")).json()

        assert body["movies"]["workflow_funnel"]["staging"] == 1
        assert body["movies"]["workflow_funnel"]["processed"] == 1
        assert body["tv"]["workflow_funnel"] == {
            "candidates": 1,
            "staging": 1,
            "preview": 1,
            "processed": 1,
        }
        assert body["tv"]["shows_total"] == 2
        assert body["tv"]["episodes_total"] == 6  # specials excluded
        assert body["tv"]["show_verdict_counts"]["ok"] == 1
        assert body["tv"]["show_verdict_counts"]["needs_action"] == 1
        assert body["tv"]["verdict_breakdown"] == {
            "widescreen": 1,
            "sampled_widescreen": 1,
            "letterboxed_untreated": 1,
            "tagged": 2,
            "reencoded": 0,
            "variable": 0,
            "open_matte": 0,
            "pillarbox": 0,
            "ineligible": 0,
            "error": 0,
            "unanalyzed": 1,
        }
        assert body["tv"]["aspect_distribution"] == {"2.40:1": 3}

    async def test_summary_excludes_open_matte_from_tv_aspect_distribution(
        self, client: AsyncClient, db
    ):
        await _seed_dimension_class_show(db)

        body = (await client.get("/api/letterbox/summary")).json()

        assert body["tv"]["verdict_breakdown"]["open_matte"] == 1
        assert body["tv"]["aspect_distribution"] == {"2.40:1": 1}


@pytest.mark.asyncio
class TestTvList:
    async def test_list_returns_shows_with_rollups(self, client: AsyncClient, tv_library):
        body = (await client.get("/api/letterbox/tv")).json()

        assert body["total"] == 2
        by_title = {item["title"]: item for item in body["items"]}
        assert by_title["Clean Show"]["rollup"]["verdict"] == "ok"
        assert by_title["Clean Show"]["rollup"]["content_types"] == [
            {"type": "widescreen", "count": 2}
        ]
        assert by_title["Mixed Show"]["rollup"]["verdict"] == "needs_action"
        assert "active_job_ids" not in by_title["Mixed Show"]

    async def test_list_filters_by_has_candidates(self, client: AsyncClient, tv_library):
        body = (await client.get("/api/letterbox/tv", params={"has_candidates": "true"})).json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Mixed Show"

    async def test_list_filters_by_query(self, client: AsyncClient, tv_library):
        body = (await client.get("/api/letterbox/tv", params={"q": "clean"})).json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Clean Show"

    async def test_list_filters_by_each_verdict(self, client: AsyncClient, tv_filter_library):
        expected_titles = {
            "needs_action": {"Dirty Mix Show", "Mixed Show"},
            "treated": {"Treated Show"},
            "ok": {"Clean Mix Show", "Clean Show", "Open Matte Show", "Pillarbox Show"},
            "unanalyzed": {"Unanalyzed Show"},
        }

        for verdict, titles in expected_titles.items():
            body = (await client.get("/api/letterbox/tv", params={"verdict": verdict})).json()
            assert {item["title"] for item in body["items"]} == titles

    async def test_list_filters_by_content_type_membership(
        self, client: AsyncClient, tv_filter_library
    ):
        expected_titles = {
            "widescreen": {"Clean Mix Show", "Clean Show", "Dirty Mix Show"},
            "open_matte": {"Open Matte Show"},
            "pillarbox": {"Clean Mix Show", "Pillarbox Show"},
        }

        for content_type, titles in expected_titles.items():
            body = (await client.get("/api/letterbox/tv", params={"verdict": content_type})).json()
            assert {item["title"] for item in body["items"]} == titles

    async def test_list_filters_by_each_uniformity(self, client: AsyncClient, tv_filter_library):
        expected_titles = {
            "uniform": {"Clean Show", "Mixed Show", "Open Matte Show", "Pillarbox Show", "Treated Show"},
            "clean_mixed": {"Clean Mix Show"},
            "dirty_mixed": {"Dirty Mix Show"},
        }

        for uniformity, titles in expected_titles.items():
            body = (await client.get("/api/letterbox/tv", params={"uniformity": uniformity})).json()
            assert {item["title"] for item in body["items"]} == titles


@pytest.mark.asyncio
class TestTvDetail:
    async def test_detail_payload_shape(self, client: AsyncClient, tv_library):
        mixed_show = tv_library["mixed_show"]
        body = (await client.get(f"/api/letterbox/tv/{mixed_show.id}")).json()

        assert body["series"]["id"] == mixed_show.id
        assert [season["season_number"] for season in body["seasons"]] == [0, 1]
        assert body["seasons"][0]["is_specials"] is True
        assert body["rollup"]["episodes_total"] == 4  # specials excluded from show rollup
        assert body["rollup"]["content_types"] == []
        ep4 = next(
            episode
            for episode in body["seasons"][1]["episodes"]
            if episode["episode_number"] == 4
        )
        ep3 = next(
            episode
            for episode in body["seasons"][1]["episodes"]
            if episode["episode_number"] == 3
        )
        assert ep4["bucket"] == "unanalyzed"
        assert ep4["media_file_id"] is not None
        assert ep3["resolved_by"] == "reencode"

    async def test_detail_computes_legacy_clear_aspect_label(self, client: AsyncClient, tv_library):
        clean_show = tv_library["clean_show"]
        clean_ep1 = tv_library["clean_ep1"]
        clean_ep2 = tv_library["clean_ep2"]

        series_body = (await client.get(f"/api/letterbox/tv/{clean_show.id}")).json()
        season_one = next(season for season in series_body["seasons"] if season["season_number"] == 1)
        detail_episode = next(
            episode for episode in season_one["episodes"] if episode["episode_id"] == clean_ep1.id
        )
        sampled_clear_episode = next(
            episode for episode in season_one["episodes"] if episode["episode_id"] == clean_ep2.id
        )
        assert detail_episode["aspect_label"] == "1.78:1"
        assert sampled_clear_episode["aspect_label"] is None

        episode_body = (await client.get(f"/api/letterbox/tv/{clean_show.id}/episodes/{clean_ep1.id}")).json()
        assert episode_body["aspect_label"] == "1.78:1"

        sampled_clear_body = (
            await client.get(f"/api/letterbox/tv/{clean_show.id}/episodes/{clean_ep2.id}")
        ).json()
        assert sampled_clear_body["aspect_label"] is None

    async def test_detail_404_for_unknown_series(self, client: AsyncClient):
        resp = await client.get("/api/letterbox/tv/999999")
        assert resp.status_code == 404

    async def test_detail_overrides_unscanned_open_matte_until_detector_truth(
        self, client: AsyncClient, db
    ):
        series, open_matte, truth = await _seed_dimension_class_show(db)

        body = (await client.get(f"/api/letterbox/tv/{series.id}")).json()
        episodes = {
            episode["episode_id"]: episode
            for season in body["seasons"]
            for episode in season["episodes"]
        }

        assert episodes[open_matte.id]["bucket"] == "open_matte"
        assert episodes[open_matte.id]["aspect_label"] == "2.40:1"
        assert episodes[truth.id]["bucket"] == "candidate"
        assert episodes[truth.id]["aspect_label"] == "2.40:1"











@pytest.mark.asyncio
async def test_tv_dev_reset_all_deletes_episode_rows_and_previews_only(
    client: AsyncClient, tv_library, db: AsyncSession, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    preview_root = settings.letterbox_preview_path
    preview_root.mkdir(parents=True, exist_ok=True)
    episode = tv_library["ep1"]
    movie = Movie(
        title="Movie Kept",
        year=2024,
        folder_path="/movies/kept",
        movie_file_path="/movies/kept/movie.mkv",
        tmdb_id=44001,
    )
    db.add(movie)
    await db.flush()
    db.add_all(
        [
            LetterboxState(
                media_type="movie",
                movie_id=movie.id,
                status="candidate",
                confidence="high",
            ),
            LetterboxEvent(
                media_type="movie",
                movie_id=movie.id,
                action="detect",
                source="api",
            ),
            LetterboxEvent(
                media_type="episode",
                episode_id=episode.id,
                action="detect",
                source="api",
            ),
        ]
    )
    await db.commit()

    episode_preview = preview_root / f"episode-{episode.id}_before_5_bright_v3.webp"
    movie_preview = preview_root / f"movie-{movie.id}_before_5_bright_v3.webp"
    episode_preview.write_bytes(b"episode")
    movie_preview.write_bytes(b"movie")

    episode_states = (
        await db.execute(select(LetterboxState).where(LetterboxState.media_type == "episode"))
    ).scalars().all()
    episode_events = (
        await db.execute(select(LetterboxEvent).where(LetterboxEvent.media_type == "episode"))
    ).scalars().all()

    resp = await client.post("/api/letterbox/tv/dev/reset-all")

    assert resp.status_code == 200
    assert resp.json() == {
        "states_deleted": len(episode_states),
        "events_deleted": len(episode_events),
        "previews_purged": 1,
    }
    assert (
        await db.execute(select(LetterboxState).where(LetterboxState.media_type == "episode"))
    ).scalars().all() == []
    assert (
        await db.execute(select(LetterboxEvent).where(LetterboxEvent.media_type == "episode"))
    ).scalars().all() == []
    assert (
        await db.execute(
            select(LetterboxState).where(
                LetterboxState.media_type == "movie",
                LetterboxState.movie_id == movie.id,
            )
        )
    ).scalar_one().movie_id == movie.id
    assert (
        await db.execute(select(LetterboxEvent).where(LetterboxEvent.media_type == "movie"))
    ).scalar_one().movie_id == movie.id
    assert not episode_preview.exists()
    assert movie_preview.exists()


@pytest.mark.asyncio
class TestRouteOrdering:
    async def test_summary_and_tv_not_captured_by_movie_routes(self, client: AsyncClient):
        assert (await client.get("/api/letterbox/summary")).status_code == 200
        assert (await client.get("/api/letterbox/tv")).status_code == 200

    async def test_tv_literal_routes_register_before_series_parameter(self):
        paths = [getattr(route, "path", "") for route in app.routes]
        series_index = paths.index("/api/letterbox/tv/{series_id}")

        assert paths.index("/api/letterbox/tv/dev/reset-all") < series_index
        assert paths.index("/api/letterbox/tv/detect") < series_index

    async def test_tv_detect_literal_route_not_captured_by_series_route(
        self,
    ):
        assert any(
            getattr(route, "path", None) == "/api/letterbox/tv/detect"
            and "POST" in (getattr(route, "methods", None) or set())
            for route in app.routes
        )
