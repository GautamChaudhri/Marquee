"""API tests for Plan 10 phase 3: TV letterbox summary/list/detail + detect/apply actions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.main import app
from marquee.media import binaries
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    Job,
    LetterboxState,
    MediaFile,
    Movie,
    Season,
    Series,
)


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
            payload={"series_id": mixed_show.id},
            status="running",
            subject_type="series",
            subject_id=str(mixed_show.id),
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
        assert body["tv"]["show_verdict_counts"]["clean"] == 1
        assert body["tv"]["show_verdict_counts"]["mixed"] == 1
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
        assert by_title["Clean Show"]["rollup"]["verdict"] == "clean"
        assert by_title["Mixed Show"]["rollup"]["verdict"] == "mixed"
        assert by_title["Mixed Show"]["active_job_ids"] == ["tv-active-job"]

    async def test_list_filters_by_has_candidates(self, client: AsyncClient, tv_library):
        body = (await client.get("/api/letterbox/tv", params={"has_candidates": "true"})).json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Mixed Show"

    async def test_list_filters_by_query(self, client: AsyncClient, tv_library):
        body = (await client.get("/api/letterbox/tv", params={"q": "clean"})).json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Clean Show"


@pytest.mark.asyncio
class TestTvDetail:
    async def test_detail_payload_shape(self, client: AsyncClient, tv_library):
        mixed_show = tv_library["mixed_show"]
        body = (await client.get(f"/api/letterbox/tv/{mixed_show.id}")).json()

        assert body["series"]["id"] == mixed_show.id
        assert [season["season_number"] for season in body["seasons"]] == [0, 1]
        assert body["seasons"][0]["is_specials"] is True
        assert body["rollup"]["episodes_total"] == 4  # specials excluded from show rollup
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
class TestTvDetect:
    async def test_series_detect_builds_one_child_per_season(self, client: AsyncClient, tv_library, db):
        mixed_show = tv_library["mixed_show"]
        resp = await client.post(f"/api/letterbox/tv/{mixed_show.id}/detect", json={})
        assert resp.status_code == 202
        body = resp.json()
        assert body["total"] == 2

        parent = await db.get(Job, body["job_id"])
        assert parent.type == "letterbox_detect_tv_batch"
        children = (await db.execute(select(Job).where(Job.parent_id == parent.id))).scalars().all()
        assert {child.type for child in children} == {"letterbox_detect_tv_scope"}
        assert len(children) == 2
        assert all(child.payload["include_open_matte"] is False for child in children)

    async def test_series_detect_scope_forwards_include_open_matte_only_to_scoped_children(
        self, client: AsyncClient, tv_library, db
    ):
        mixed_show = tv_library["mixed_show"]
        ep1 = tv_library["ep1"]

        show_resp = await client.post(
            f"/api/letterbox/tv/{mixed_show.id}/detect",
            json={"include_open_matte": True},
        )
        assert show_resp.status_code == 202
        show_parent = await db.get(Job, show_resp.json()["job_id"])
        show_children = (
            await db.execute(select(Job).where(Job.parent_id == show_parent.id))
        ).scalars().all()
        assert all(child.payload["include_open_matte"] is False for child in show_children)

        season_resp = await client.post(
            f"/api/letterbox/tv/{mixed_show.id}/detect",
            json={"season_number": 1, "include_open_matte": True},
        )
        assert season_resp.status_code == 202
        season_parent = await db.get(Job, season_resp.json()["job_id"])
        season_child = (
            await db.execute(select(Job).where(Job.parent_id == season_parent.id))
        ).scalar_one()
        assert season_child.payload["include_open_matte"] is True

        episode_resp = await client.post(
            f"/api/letterbox/tv/{mixed_show.id}/detect",
            json={"episode_id": ep1.id, "include_open_matte": True},
        )
        assert episode_resp.status_code == 202
        episode_parent = await db.get(Job, episode_resp.json()["job_id"])
        episode_child = (
            await db.execute(select(Job).where(Job.parent_id == episode_parent.id))
        ).scalar_one()
        assert episode_child.payload["include_open_matte"] is True

    async def test_library_detect_builds_one_child_per_show(self, client: AsyncClient, tv_library, db):
        resp = await client.post("/api/letterbox/tv/detect", json={})
        assert resp.status_code == 202
        body = resp.json()
        assert body["total"] == 2

        parent = await db.get(Job, body["job_id"])
        assert parent.type == "letterbox_detect_tv_batch"
        assert parent.payload["force"] is False
        children = (await db.execute(select(Job).where(Job.parent_id == parent.id))).scalars().all()
        assert len(children) == 2
        assert all(child.payload["force"] is False for child in children)
        assert all(child.payload["include_open_matte"] is False for child in children)

    async def test_library_detect_passes_force_to_child_payloads(
        self, client: AsyncClient, tv_library, db
    ):
        resp = await client.post("/api/letterbox/tv/detect", json={"force": True})
        assert resp.status_code == 202
        body = resp.json()
        assert body["total"] == 2

        parent = await db.get(Job, body["job_id"])
        assert parent.type == "letterbox_detect_tv_batch"
        assert parent.payload["force"] is True
        children = (await db.execute(select(Job).where(Job.parent_id == parent.id))).scalars().all()
        assert len(children) == 2
        assert all(child.payload["force"] is True for child in children)


@pytest.mark.asyncio
class TestTvActions:
    async def test_apply_and_remove_episode_scope(self, client: AsyncClient, tv_library, db, monkeypatch, tmp_path):
        mixed_show = tv_library["mixed_show"]
        target = tv_library["ep1"]
        media_path = tmp_path / "mixed-s01e01.mkv"
        media_path.write_bytes(b"video")

        media_file = (
            await db.execute(
                select(MediaFile)
                .join(EpisodeMediaFile, EpisodeMediaFile.media_file_id == MediaFile.id)
                .where(EpisodeMediaFile.episode_id == target.id)
            )
        ).scalar_one()
        media_file.path = str(media_path)
        target.episode_file_path = str(media_path)
        await db.commit()

        mkv_json = json.dumps(
            {"container": {"type": "Matroska"}, "tracks": [{"type": "video", "properties": {}}]}
        )

        def fake_run(name, args, timeout=120.0):
            if name == "mkvmerge":
                return binaries.CommandResult(0, mkv_json, "")
            return binaries.CommandResult(0, "", "")

        monkeypatch.setattr(binaries, "resolve", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(binaries, "run", fake_run)

        applied = await client.post(
            f"/api/letterbox/tv/{mixed_show.id}/apply",
            json={"episode_id": target.id},
        )
        assert applied.status_code == 200
        assert applied.json()["applied_episodes"] == 1

        removed = await client.post(
            f"/api/letterbox/tv/{mixed_show.id}/episodes/{target.id}/remove"
        )
        assert removed.status_code == 200
        assert removed.json()["removed"] is True

    async def test_apply_episode_scope_fans_out_shared_media_group(
        self,
        client: AsyncClient,
        tv_library,
        db,
        monkeypatch,
        tmp_path,
    ):
        mixed_show = tv_library["mixed_show"]
        first = tv_library["ep1"]
        second = tv_library["ep2"]
        media_path = tmp_path / "mixed-shared.mkv"
        media_path.write_bytes(b"video")

        first_media_file = (
            await db.execute(
                select(MediaFile)
                .join(EpisodeMediaFile, EpisodeMediaFile.media_file_id == MediaFile.id)
                .where(EpisodeMediaFile.episode_id == first.id)
            )
        ).scalar_one()
        second_media_file = (
            await db.execute(
                select(MediaFile)
                .join(EpisodeMediaFile, EpisodeMediaFile.media_file_id == MediaFile.id)
                .where(EpisodeMediaFile.episode_id == second.id)
            )
        ).scalar_one()
        first_media_file.path = str(media_path)
        second_media_file.is_active = False
        first.episode_file_path = str(media_path)
        second.episode_file_path = str(media_path)
        db.add(EpisodeMediaFile(episode_id=second.id, media_file_id=first_media_file.id))
        await db.commit()

        mkv_json = json.dumps(
            {"container": {"type": "Matroska"}, "tracks": [{"type": "video", "properties": {}}]}
        )

        def fake_run(name, args, timeout=120.0):
            if name == "mkvmerge":
                return binaries.CommandResult(0, mkv_json, "")
            return binaries.CommandResult(0, "", "")

        monkeypatch.setattr(binaries, "resolve", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(binaries, "run", fake_run)

        applied = await client.post(
            f"/api/letterbox/tv/{mixed_show.id}/apply",
            json={"episode_id": first.id},
        )
        assert applied.status_code == 200
        assert applied.json()["applied_episodes"] == 2
        assert applied.json()["items"][0]["episode_ids"] == [first.id, second.id]

    async def test_ignore_and_mark_not_letterboxed(self, client: AsyncClient, tv_library, db):
        mixed_show = tv_library["mixed_show"]
        ep4 = tv_library["ep4"]
        ep1 = tv_library["ep1"]

        ignored = await client.post(
            f"/api/letterbox/tv/{mixed_show.id}/episodes/{ep4.id}/ignore"
        )
        assert ignored.status_code == 200
        assert ignored.json()["status"] == "skipped"

        marked = await client.post(
            f"/api/letterbox/tv/{mixed_show.id}/episodes/{ep1.id}/mark-not-letterboxed"
        )
        assert marked.status_code == 200
        assert marked.json()["status"] == "not_letterboxed"
        assert marked.json()["confidence"] == "none"
        assert marked.json()["recommended_crop_top"] == 0
        assert marked.json()["recommended_crop_bottom"] == 0
        assert marked.json()["applied_crop_top"] == 0
        assert marked.json()["applied_crop_bottom"] == 0
        assert marked.json()["aspect_label"] == "1.78:1"


@pytest.mark.asyncio
class TestRouteOrdering:
    async def test_summary_and_tv_not_captured_by_movie_routes(self, client: AsyncClient):
        assert (await client.get("/api/letterbox/summary")).status_code == 200
        assert (await client.get("/api/letterbox/tv")).status_code == 200

    async def test_tv_detect_literal_route_not_captured_by_series_route(
        self, client: AsyncClient, tv_library
    ):
        resp = await client.post("/api/letterbox/tv/detect", json={})
        assert resp.status_code == 202
