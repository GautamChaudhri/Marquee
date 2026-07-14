"""API tests for plan 06 phase 3: TV HDR summary/list/detail + Sonarr preferences + analyze."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.main import app
from marquee.models import (
    DoviState,
    Episode,
    Movie,
    RadarrOverlayProfilePreference,
    Season,
    Series,
    SonarrCustomFormat,
    SonarrOverlayProfilePreference,
    SonarrProfileFormatItem,
    SonarrQualityProfile,
)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_sonarr_profile(db: AsyncSession, profile_id: int = 1) -> None:
    now = datetime.now(UTC)
    db.add(
        SonarrQualityProfile(
            id=profile_id, name="4K HDR", upgrade_allowed=True, synced_at=now
        )
    )
    db.add(
        SonarrCustomFormat(
            id=10, name="HDR10", include_when_renaming=False, specifications_json=None, synced_at=now
        )
    )
    db.add(
        SonarrCustomFormat(
            id=11,
            name="Dolby Vision",
            include_when_renaming=False,
            specifications_json=None,
            synced_at=now,
        )
    )
    await db.flush()
    db.add(SonarrProfileFormatItem(profile_id=profile_id, custom_format_id=10, score=10))
    db.add(SonarrProfileFormatItem(profile_id=profile_id, custom_format_id=11, score=20))
    db.add(
        SonarrOverlayProfilePreference(
            profile_id=profile_id,
            meet_target="hdr10",
            exceed_target="dovi_fallback",
            excluded_targets=None,
            updated_at=now,
        )
    )
    await db.commit()


async def _seed_show_a_uniform_meets(db: AsyncSession) -> Series:
    """One season, both episodes HDR10 — meets target, uniform."""
    series = Series(title="Uniform Show", year=2020, series_path="/tv/uniform", quality_profile_id=1)
    db.add(series)
    await db.flush()
    db.add(Season(series_id=series.id, season_number=1, episode_file_count=2))
    await db.flush()
    db.add_all(
        [
            Episode(
                series_id=series.id,
                season_number=1,
                episode_number=1,
                episode_file_path="/tv/uniform/s01e01.mkv",
                hdr_type_raw="HDR10",
                has_hdr=True,
                has_dv=False,
            ),
            Episode(
                series_id=series.id,
                season_number=1,
                episode_number=2,
                episode_file_path="/tv/uniform/s01e02.mkv",
                hdr_type_raw="HDR10",
                has_hdr=True,
                has_dv=False,
            ),
        ]
    )
    await db.commit()
    return series


async def _seed_show_b_gaps_with_specials(db: AsyncSession) -> Series:
    """Specials + a mixed season: gaps status, a dovi_no_fallback episode, an unknown episode."""
    series = Series(title="Gaps Show", year=2021, series_path="/tv/gaps", quality_profile_id=1)
    db.add(series)
    await db.flush()
    db.add(Season(series_id=series.id, season_number=0, episode_file_count=1))
    db.add(Season(series_id=series.id, season_number=1, episode_file_count=4))
    await db.flush()
    db.add(
        Episode(
            series_id=series.id,
            season_number=0,
            episode_number=1,
            episode_file_path="/tv/gaps/s00e01.mkv",
            hdr_type_raw="DV",  # would break rollups if wrongly included (H3)
            has_hdr=False,
            has_dv=True,
        )
    )
    db.add_all(
        [
            Episode(
                series_id=series.id,
                season_number=1,
                episode_number=1,
                episode_file_path="/tv/gaps/s01e01.mkv",
                hdr_type_raw="HDR10",
                has_hdr=True,
                has_dv=False,
            ),
            Episode(
                series_id=series.id,
                season_number=1,
                episode_number=2,
                episode_file_path="/tv/gaps/s01e02.mkv",
                hdr_type_raw="SDR",
                has_hdr=False,
                has_dv=False,
            ),
            Episode(
                series_id=series.id,
                season_number=1,
                episode_number=3,
                episode_file_path="/tv/gaps/s01e03.mkv",
                hdr_type_raw=None,  # unknown: file exists, mediaInfo not captured yet
                has_hdr=None,
                has_dv=None,
            ),
            Episode(
                series_id=series.id,
                season_number=1,
                episode_number=4,
                episode_file_path="/tv/gaps/s01e04.mkv",
                hdr_type_raw="DV",  # dovi_no_fallback
                has_hdr=False,
                has_dv=True,
            ),
        ]
    )
    await db.commit()
    return series


@pytest_asyncio.fixture
async def tv_library(db: AsyncSession):
    await _seed_sonarr_profile(db)
    show_a = await _seed_show_a_uniform_meets(db)
    show_b = await _seed_show_b_gaps_with_specials(db)
    return show_a, show_b


@pytest.mark.asyncio
class TestSummary:
    async def test_movies_and_tv_numbers(self, client: AsyncClient, tv_library):
        show_a, show_b = tv_library
        body = (await client.get("/api/hdr/summary")).json()

        assert body["tv"]["shows_total"] == 2
        # Non-special episodes only: show_a=2, show_b season1=4 (season0 excluded) -> 6
        assert body["tv"]["episodes_total"] == 6
        assert body["tv"]["episodes_unknown"] == 1
        assert body["tv"]["show_status_counts"]["meets_target"] == 1
        assert body["tv"]["show_status_counts"]["gaps"] == 1
        assert body["tv"]["uniformity_counts"]["uniform"] == 1  # show_a: single uniform season
        assert body["tv"]["uniformity_counts"]["mixed"] == 1  # show_b: season1 mixes SDR/HDR10/DV

        assert body["insights"]["dovi_no_fallback"]["shows_affected"] == 1

        assert "radarr" in body["profile_preferences"]
        assert "sonarr" in body["profile_preferences"]
        assert any(p["profile_id"] == 1 for p in body["profile_preferences"]["sonarr"])

    async def test_unanalyzed_dovi_counts_episodes_without_analyzed_state(
        self, client: AsyncClient, tv_library
    ):
        body = (await client.get("/api/hdr/summary")).json()
        # show_b has two DV episodes (specials s00e01 + s01e04), neither analyzed.
        # Insight/dovi-analysis counts are library-wide and intentionally include
        # specials (unlike the rollup-derived episodes_total/distribution).
        assert body["tv"]["dovi_analysis"]["total_dovi"] == 2
        assert body["tv"]["dovi_analysis"]["analyzed"] == 0
        assert body["insights"]["unanalyzed_dovi"]["episodes"] == 2
        assert body["insights"]["dovi_no_fallback"]["episodes"] == 2

    async def test_worst_offenders_ordering(self, client: AsyncClient, tv_library, db: AsyncSession):
        show_a, show_b = tv_library
        body = (await client.get("/api/hdr/summary")).json()
        offenders = body["worst_offenders"]
        assert len(offenders) == 1
        assert offenders[0]["series_id"] == show_b.id
        assert offenders[0]["status"] == "gaps"
        assert offenders[0]["below_count"] == 1

    async def test_four_k_sdr_insight(self, client: AsyncClient, db: AsyncSession):
        series = Series(title="4K SDR Show", year=2022, series_path="/tv/uhd", quality_profile_id=1)
        db.add(series)
        await db.flush()
        db.add(Season(series_id=series.id, season_number=1, episode_file_count=1))
        await db.flush()
        db.add(
            Episode(
                series_id=series.id,
                season_number=1,
                episode_number=1,
                episode_file_path="/tv/uhd/s01e01.mkv",
                hdr_type_raw="SDR",
                video_width=3840,
                video_height=2160,
            )
        )
        await db.commit()
        await _seed_sonarr_profile(db)

        body = (await client.get("/api/hdr/summary")).json()
        assert body["insights"]["four_k_sdr"]["episodes"] == 1
        assert body["insights"]["four_k_sdr"]["shows_affected"] == 1


@pytest.mark.asyncio
class TestTvList:
    async def test_list_returns_both_shows_with_rollups(self, client: AsyncClient, tv_library):
        show_a, show_b = tv_library
        body = (await client.get("/api/hdr/tv")).json()
        assert body["total"] == 2
        titles = {item["title"] for item in body["items"]}
        assert titles == {"Uniform Show", "Gaps Show"}

        by_title = {item["title"]: item for item in body["items"]}
        assert by_title["Uniform Show"]["rollup"]["status"] == "meets_target"
        assert by_title["Gaps Show"]["rollup"]["status"] == "gaps"
        # Specials excluded from the show's own episode totals.
        assert by_title["Gaps Show"]["episodes_total"] == 5  # includes specials at item level
        assert by_title["Gaps Show"]["rollup"]["episodes_total"] == 4  # rollup excludes specials

    async def test_filter_by_preference_status(self, client: AsyncClient, tv_library):
        body = (await client.get("/api/hdr/tv", params={"preference_status": "gaps"})).json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Gaps Show"

    async def test_filter_by_dovi_no_fallback(self, client: AsyncClient, tv_library):
        body = (await client.get("/api/hdr/tv", params={"dovi_no_fallback": "true"})).json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Gaps Show"

    async def test_sort_by_status_ascending_worst_first(self, client: AsyncClient, tv_library):
        body = (
            await client.get("/api/hdr/tv", params={"sort_by": "status", "sort_dir": "asc"})
        ).json()
        assert [item["title"] for item in body["items"]] == ["Gaps Show", "Uniform Show"]

    async def test_sort_by_coverage(self, client: AsyncClient, tv_library):
        body = (
            await client.get("/api/hdr/tv", params={"sort_by": "coverage", "sort_dir": "asc"})
        ).json()
        assert body["items"][0]["title"] == "Gaps Show"


@pytest.mark.asyncio
class TestTvDetail:
    async def test_detail_payload_shape(self, client: AsyncClient, tv_library):
        _show_a, show_b = tv_library
        resp = await client.get(f"/api/hdr/tv/{show_b.id}")
        assert resp.status_code == 200
        body = resp.json()

        assert body["series"]["id"] == show_b.id
        season_numbers = [s["season_number"] for s in body["seasons"]]
        assert season_numbers == [0, 1]

        specials = body["seasons"][0]
        assert specials["is_specials"] is True
        assert len(specials["episodes"]) == 1

        # Specials excluded from the show-level rollup.
        assert body["rollup"]["episodes_total"] == 4
        assert body["rollup"]["status"] == "gaps"

        season1 = body["seasons"][1]
        episodes_by_number = {e["episode_number"]: e for e in season1["episodes"]}
        assert episodes_by_number[3]["hdr_type_raw"] is None
        assert episodes_by_number[3]["preference_status"] == "unknown"
        assert episodes_by_number[4]["hdr_tags"] == ["dovi", "dovi_no_fallback"]
        assert body["binaries"]["ffprobe"] is True

    async def test_detail_includes_episode_dovi_state(self, client: AsyncClient, tv_library, db: AsyncSession):
        _show_a, show_b = tv_library
        episode = (
            await db.execute(
                select(Episode).where(Episode.series_id == show_b.id, Episode.episode_number == 4)
            )
        ).scalar_one()
        db.add(
            DoviState(
                media_type="episode",
                episode_id=episode.id,
                status="analyzed",
                dovi_profile=7,
                el_type="MEL",
            )
        )
        await db.commit()

        body = (await client.get(f"/api/hdr/tv/{show_b.id}")).json()
        season1 = body["seasons"][1]
        ep4 = next(e for e in season1["episodes"] if e["episode_number"] == 4)
        assert ep4["dovi"]["status"] == "analyzed"
        assert ep4["dovi"]["profile"] == 7
        assert ep4["dovi"]["el_type"] == "MEL"

    async def test_detail_404_for_invisible_series(self, client: AsyncClient, db: AsyncSession):
        series = Series(title="No Files", year=2023, series_path="/tv/none")
        db.add(series)
        await db.commit()
        resp = await client.get(f"/api/hdr/tv/{series.id}")
        assert resp.status_code == 404

    async def test_detail_404_for_unknown_series(self, client: AsyncClient):
        resp = await client.get("/api/hdr/tv/999999")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestSonarrPreferences:
    async def test_put_preferences_valid(self, client: AsyncClient, tv_library, db: AsyncSession):
        resp = await client.put(
            "/api/hdr/tv/preferences",
            json={"profiles": [{"profile_id": 1, "meet_target": "hdr10", "exceed_target": "dovi_fallback"}]},
        )
        assert resp.status_code == 200
        assert resp.json()["applied_profile_ids"] == [1]

        row = (
            await db.execute(
                select(SonarrOverlayProfilePreference).where(
                    SonarrOverlayProfilePreference.profile_id == 1
                )
            )
        ).scalar_one()
        assert row.meet_target == "hdr10"
        assert row.exceed_target == "dovi_fallback"

    async def test_put_preferences_rejects_unknown_profile(self, client: AsyncClient, tv_library):
        resp = await client.put(
            "/api/hdr/tv/preferences",
            json={"profiles": [{"profile_id": 999, "meet_target": "hdr10"}]},
        )
        assert resp.status_code == 400

    async def test_put_preferences_rejects_invalid_target(self, client: AsyncClient, tv_library):
        resp = await client.put(
            "/api/hdr/tv/preferences",
            json={"profiles": [{"profile_id": 1, "meet_target": "not_a_real_target"}]},
        )
        assert resp.status_code == 400

    async def test_put_preferences_rejects_exceed_not_stricter_than_meet(
        self, client: AsyncClient, tv_library
    ):
        resp = await client.put(
            "/api/hdr/tv/preferences",
            json={"profiles": [{"profile_id": 1, "meet_target": "dovi_fallback", "exceed_target": "hdr10"}]},
        )
        assert resp.status_code == 400

    async def test_put_preferences_rejects_empty_payload(self, client: AsyncClient):
        resp = await client.put("/api/hdr/tv/preferences", json={"profiles": []})
        assert resp.status_code == 400

    async def test_put_preferences_does_not_touch_radarr_table(
        self, client: AsyncClient, tv_library, db: AsyncSession
    ):
        await client.put(
            "/api/hdr/tv/preferences",
            json={"profiles": [{"profile_id": 1, "meet_target": "hdr10", "exceed_target": "dovi_fallback"}]},
        )
        radarr_rows = (await db.execute(select(RadarrOverlayProfilePreference))).scalars().all()
        assert radarr_rows == []


@pytest.mark.asyncio
class TestTvAnalyzeBatch:
    async def test_show_batch_builds_one_child_per_dv_episode(
        self, client: AsyncClient, tv_library
    ):
        _show_a, show_b = tv_library
        response = await client.post(f"/api/hdr/tv/{show_b.id}/analyze", json={})
        assert response.status_code == 400

    async def test_show_batch_season_filter(self, client: AsyncClient, tv_library):
        _show_a, show_b = tv_library
        response = await client.post(
            f"/api/hdr/tv/{show_b.id}/analyze",
            json={"season_number": 1},
        )
        assert response.status_code == 400

    async def test_show_batch_400_when_no_eligible_episodes(self, client: AsyncClient, tv_library):
        show_a, _show_b = tv_library
        response = await client.post(f"/api/hdr/tv/{show_a.id}/analyze", json={})
        assert response.status_code == 400

    async def test_library_batch_covers_all_visible_shows(self, client: AsyncClient, tv_library):
        response = await client.post("/api/hdr/tv/analyze", json={})
        assert response.status_code == 400

    async def test_library_batch_series_ids_subset(self, client: AsyncClient, tv_library):
        show_a, _show_b = tv_library
        response = await client.post("/api/hdr/tv/analyze", json={"series_ids": [show_a.id]})
        assert response.status_code == 400  # show_a has no DV episodes


@pytest.mark.asyncio
class TestRouteOrdering:
    async def test_summary_and_tv_not_captured_by_movie_id_route(self, client: AsyncClient):
        assert (await client.get("/api/hdr/summary")).status_code == 200
        assert (await client.get("/api/hdr/tv")).status_code == 200

    async def test_movie_detail_404_still_works_for_real_ints(self, client: AsyncClient):
        resp = await client.get("/api/hdr/999999")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestMovieRegression:
    async def test_movie_list_envelope_unchanged(self, client: AsyncClient, db: AsyncSession):
        db.add(
            Movie(
                title="Regression",
                year=2020,
                folder_path="/m/r",
                movie_file_path="r.mkv",
                hdr_type_raw="HDR10",
                has_hdr=True,
                has_dv=False,
            )
        )
        await db.commit()

        body = (await client.get("/api/hdr")).json()
        assert set(body.keys()) == {
            "distribution",
            "distribution_order",
            "total",
            "page",
            "page_size",
            "items",
            "profiles",
            "profile_preferences",
            "applied_filters",
        }
        assert "distribution_keys" not in body["items"][0]

    async def test_radarr_put_preferences_untouched(self, client: AsyncClient, db: AsyncSession):
        now = datetime.now(UTC)
        from marquee.models import RadarrCustomFormat, RadarrQualityProfile

        db.add(
            RadarrQualityProfile(id=1, name="UHD", upgrade_allowed=True, synced_at=now)
        )
        db.add(
            RadarrCustomFormat(
                id=10, name="HDR10", include_when_renaming=False, specifications_json=None, synced_at=now
            )
        )
        await db.flush()
        from marquee.models import RadarrProfileFormatItem

        db.add(RadarrProfileFormatItem(profile_id=1, custom_format_id=10, score=10))
        await db.commit()

        resp = await client.put(
            "/api/hdr/preferences",
            json={"profiles": [{"profile_id": 1, "meet_target": "hdr10"}]},
        )
        assert resp.status_code == 200
        assert resp.json() == {"applied_profile_ids": [1]}
