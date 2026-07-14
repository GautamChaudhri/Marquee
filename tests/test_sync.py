"""Tests for SyncService — movies, series, seasons, episodes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.poster_sources.tmdb import MovieDetails
from marquee.core.sync_service import (
    SyncService,
    _resolve_poster_path,
    _upsert_episode_media_files,
    _upsert_movie_media_file,
)
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    LetterboxEvent,
    LetterboxReencodeArtifact,
    LetterboxState,
    MediaFile,
    Movie,
    MovieCustomFormatScore,
    RadarrCustomFormat,
    RadarrProfileFormatItem,
    RadarrQualityProfile,
    Season,
    Series,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_path_validation():
    """All sync tests use pass-through path validation.

    Real path validation requires MEDIA_ROOTS to include test paths
    like /movies and /tv.  Tests shouldn't depend on the host filesystem.
    """
    with patch(
        "marquee.core.sync_service.safe_translate_and_validate",
        side_effect=lambda p, **_: Path(p),
    ):
        yield


# ---------------------------------------------------------------------------
# Helpers — build mock *arr API responses
# ---------------------------------------------------------------------------


def _radarr_movie(**overrides) -> dict:
    """Minimal Radarr movie dict."""
    return {
        "id": 1,
        "title": "Dune",
        "year": 2021,
        "tmdbId": 438631,
        "imdbId": "tt1160419",
        "path": "/movies/Dune (2021)",
        "qualityProfileId": 3,
        "movieFile": {"relativePath": "Dune (2021).mkv"},
        **overrides,
    }


def _sonarr_series(**overrides) -> dict:
    """Minimal Sonarr series dict."""
    return {
        "id": 100,
        "title": "Breaking Bad",
        "year": 2008,
        "tvdbId": 81189,
        "imdbId": "tt0903747",
        "tmdbId": 1396,
        "path": "/tv/Breaking Bad",
        "qualityProfileId": 2,
        "seasons": [
            {"seasonNumber": 1, "monitored": True},
            {"seasonNumber": 2, "monitored": True},
        ],
        **overrides,
    }


def _sonarr_episode(**overrides) -> dict:
    return {
        "id": 1001,
        "seriesId": 100,
        "seasonNumber": 1,
        "episodeNumber": 1,
        "title": "Pilot",
        "episodeFileId": 5001,
        "hasFile": True,
        **overrides,
    }


def _sonarr_episode_file(**overrides) -> dict:
    return {
        "id": 5001,
        "relativePath": "Season 1/Breaking Bad - S01E01.mkv",
        "path": "/tv/Breaking Bad/Season 1/Breaking Bad - S01E01.mkv",
        **overrides,
    }


# ---------------------------------------------------------------------------
# Movies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_movies_creates_new(db: AsyncSession):
    """A new movie from Radarr should be inserted."""
    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]

    svc = SyncService(db, radarr=radarr)
    report = await svc.sync_all()

    assert report.movies.created == 1
    assert report.movies.updated == 0
    assert report.movies.errors == 0

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    assert movie.title == "Dune"
    assert movie.tmdb_id == 438631
    assert movie.folder_path == "/movies/Dune (2021)"


@pytest.mark.asyncio
async def test_sync_movies_updates_existing(db: AsyncSession):
    """An existing movie should be updated, not duplicated."""
    movie = Movie(radarr_id=1, title="Old Title", year=2000, folder_path="/old")
    db.add(movie)
    await db.flush()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]

    svc = SyncService(db, radarr=radarr)
    report = await svc.sync_all()

    assert report.movies.updated == 1
    assert report.movies.created == 0

    await db.refresh(movie)
    assert movie.title == "Dune"
    assert movie.year == 2021


@pytest.mark.asyncio
async def test_full_movie_sync_retires_and_reactivates_stable_identity(db: AsyncSession):
    movie = Movie(radarr_id=99, title="Retire Me", year=2020, folder_path="/movies/retire")
    db.add(movie)
    await db.flush()
    media_file = MediaFile(
        source="radarr",
        source_key="radarr:movie-file:99",
        source_file_id=99,
        movie_id=movie.id,
        path="/movies/retire/movie.mkv",
    )
    db.add(media_file)
    await db.commit()

    radarr = AsyncMock()
    radarr.get_movies.return_value = []
    radarr.get_movie_files.return_value = []
    radarr.get_custom_formats.return_value = []
    radarr.get_quality_profiles.return_value = []
    await SyncService(db, radarr=radarr).sync_all()

    await db.refresh(movie)
    await db.refresh(media_file)
    assert (movie.is_present, media_file.is_present, media_file.is_active) == (False, False, False)
    assert movie.retired_at is not None and media_file.retired_at is not None

    radarr.get_movies.return_value = [_radarr_movie(id=99)]
    await SyncService(db, radarr=radarr).sync_all()

    await db.refresh(movie)
    reactivated_file = await db.scalar(
        select(MediaFile).where(MediaFile.movie_id == movie.id, MediaFile.is_present.is_(True))
    )
    assert movie.is_present is True and movie.retired_at is None
    assert reactivated_file is not None and reactivated_file.is_active is True


@pytest.mark.asyncio
async def test_partial_movie_sync_never_retires_absent_rows(db: AsyncSession):
    movie = Movie(radarr_id=99, title="Keep Me", year=2020, folder_path="/movies/keep")
    db.add(movie)
    await db.commit()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [{"id": 1, "title": None}]
    radarr.get_movie_files.return_value = []
    radarr.get_custom_formats.return_value = []
    radarr.get_quality_profiles.return_value = []
    report = await SyncService(db, radarr=radarr).sync_all()

    await db.refresh(movie)
    assert report.movies.errors == 1
    assert movie.is_present is True and movie.retired_at is None


@pytest.mark.asyncio
async def test_sync_movies_enriches_tmdb_metadata(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]
    tmdb = AsyncMock()
    tmdb.get_movie_details.return_value = MovieDetails(
        director="Denis Villeneuve",
        production_companies=["Legendary Pictures", "Warner Bros."],
        tagline="It begins.",
    )

    svc = SyncService(db, radarr=radarr, tmdb=tmdb)
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    assert movie.director == "Denis Villeneuve"
    assert movie.production_companies_json == ["Legendary Pictures", "Warner Bros."]
    assert movie.tagline == "It begins."
    tmdb.get_movie_details.assert_awaited_once_with(438631)


@pytest.mark.asyncio
async def test_sync_movies_skips_tmdb_fetch_for_enriched_rows(db: AsyncSession):
    movie = Movie(
        radarr_id=1,
        title="Old Title",
        year=2000,
        folder_path="/old",
        tmdb_id=438631,
        director="Already Set",
        production_companies_json=["Studio"],
        tagline=None,
    )
    db.add(movie)
    await db.flush()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]
    tmdb = AsyncMock()

    svc = SyncService(db, radarr=radarr, tmdb=tmdb)
    await svc.sync_all()

    tmdb.get_movie_details.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_movies_refreshes_tmdb_metadata_when_tmdb_id_changes(db: AsyncSession):
    movie = Movie(
        radarr_id=1,
        title="Old Title",
        year=2000,
        folder_path="/old",
        tmdb_id=99,
        director="Wrong Director",
        production_companies_json=["Old Studio"],
        tagline="Old tagline",
    )
    db.add(movie)
    await db.flush()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie(tmdbId=438631)]
    tmdb = AsyncMock()
    tmdb.get_movie_details.return_value = MovieDetails(
        director="Denis Villeneuve",
        production_companies=["Legendary Pictures"],
        tagline="It begins.",
    )

    svc = SyncService(db, radarr=radarr, tmdb=tmdb)
    await svc.sync_all()

    await db.refresh(movie)
    assert movie.tmdb_id == 438631
    assert movie.director == "Denis Villeneuve"
    assert movie.production_companies_json == ["Legendary Pictures"]
    assert movie.tagline == "It begins."
    tmdb.get_movie_details.assert_awaited_once_with(438631)


@pytest.mark.asyncio
async def test_sync_movies_tmdb_enrichment_failure_tolerated(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]
    tmdb = AsyncMock()
    tmdb.get_movie_details.side_effect = RuntimeError("boom")

    svc = SyncService(db, radarr=radarr, tmdb=tmdb)
    report = await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    assert report.movies.errors == 0
    assert movie.director is None
    assert movie.production_companies_json is None
    assert movie.tagline is None
    tmdb.get_movie_details.assert_awaited_once_with(438631)


@pytest.mark.asyncio
async def test_sync_movies_never_overwrites_poster(db: AsyncSession, tmp_path: Path):
    """Poster columns set by the pipeline must survive a sync.

    The pipeline-deployed poster exists on disk at a path that differs from
    the sync-derived expected location — sync must leave it alone.
    """
    deployed = tmp_path / "poster.jpg"
    deployed.write_bytes(b"poster")
    movie = Movie(
        radarr_id=1,
        title="Dune",
        year=2021,
        folder_path="/movies/Dune",
        poster_path=str(deployed),
        poster_ai_selected=True,
        poster_source="tmdb",
    )
    db.add(movie)
    await db.flush()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    await db.refresh(movie)
    assert movie.poster_path == str(deployed)
    assert movie.poster_ai_selected is True
    assert movie.poster_source == "tmdb"


@pytest.mark.asyncio
async def test_sync_movies_populates_hdr_dv(db: AsyncSession):
    """videoDynamicRangeType from Radarr mediaInfo sets has_hdr / has_dv."""
    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(
            movieFile={
                "relativePath": "Dune (2021).mkv",
                "mediaInfo": {
                    "width": 3840,
                    "height": 1600,
                    "videoDynamicRangeType": "DV HDR10",
                },
            }
        )
    ]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    assert movie.hdr_type_raw == "DV HDR10"
    assert movie.has_dv is True
    assert movie.has_hdr is True
    assert movie.video_width == 3840


@pytest.mark.asyncio
async def test_sync_movies_prefilters_letterbox_candidate(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(
            movieFile={
                "relativePath": "Dune (2021).mkv",
                "mediaInfo": {"width": 3840, "height": 2160},
            }
        )
    ]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    state = (
        await db.execute(select(LetterboxState).where(LetterboxState.movie_id == movie.id))
    ).scalar_one()
    assert state.status == "prefilter_candidate"
    assert state.prefilter_reason == "sixteen_nine_container"


@pytest.mark.asyncio
async def test_sync_movies_prefilters_full_frame_skipped(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(
            movieFile={
                "relativePath": "Dune (2021).mkv",
                "mediaInfo": {"width": 3840, "height": 1600},
            }
        )
    ]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    state = (
        await db.execute(select(LetterboxState).where(LetterboxState.movie_id == movie.id))
    ).scalar_one()
    assert state.status == "prefilter_skipped"
    assert state.prefilter_reason == "native_wide"


@pytest.mark.asyncio
async def test_sync_movies_without_file_stays_out_of_letterbox_workflow(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie(movieFile=None)]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    assert movie.movie_file_path is None
    state = (
        await db.execute(select(LetterboxState).where(LetterboxState.movie_id == movie.id))
    ).scalar_one_or_none()
    assert state is None


@pytest.mark.asyncio
async def test_sync_movies_later_file_creates_prefilter_row(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie(movieFile=None)]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    radarr.get_movies.return_value = [
        _radarr_movie(
            movieFile={
                "relativePath": "Dune (2021).mkv",
                "mediaInfo": {"width": 1920, "height": 1080},
            }
        )
    ]
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    state = (
        await db.execute(select(LetterboxState).where(LetterboxState.movie_id == movie.id))
    ).scalar_one()
    assert state.status == "prefilter_candidate"


@pytest.mark.asyncio
async def test_sync_movies_prefilter_does_not_overwrite_detector_truth(db: AsyncSession):
    movie = Movie(
        radarr_id=1,
        title="Dune",
        year=2021,
        folder_path="/movies/Dune (2021)",
        movie_file_path="Dune (2021).mkv",
    )
    db.add(movie)
    await db.flush()
    db.add(
        LetterboxState(
            movie_id=movie.id,
            status="not_letterboxed",
            confidence="none",
            reviewed=True,
            last_detected_at=None,
        )
    )
    await db.commit()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(
            movieFile={
                "relativePath": "Dune (2021).mkv",
                "mediaInfo": {"width": 3840, "height": 2160},
            }
        )
    ]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    state = (
        await db.execute(select(LetterboxState).where(LetterboxState.movie_id == movie.id))
    ).scalar_one()
    assert state.status == "not_letterboxed"
    assert state.reviewed is True


@pytest.mark.asyncio
async def test_movie_media_replacement_keeps_reencode_provenance_on_signature_match(
    db: AsyncSession, monkeypatch
):
    movie = Movie(
        radarr_id=1,
        title="Dune",
        year=2021,
        folder_path="/movies/Dune (2021)",
        movie_file_path="old.mkv",
    )
    db.add(movie)
    await db.flush()
    old_media_file = MediaFile(
        movie_id=movie.id,
        source="radarr",
        source_key="radarr:movie-file:2001",
        path="/movies/Dune (2021)/old.mkv",
        relative_path="old.mkv",
        container="mkv",
        is_active=True,
    )
    db.add(old_media_file)
    db.add(
        LetterboxState(
            media_type="movie",
            movie_id=movie.id,
            status="reencoded",
            resolved_by="reencode",
            resolved_at=datetime.now(UTC),
            original_crop_top=140,
            original_crop_bottom=140,
            original_aspect_label="2.40:1",
        )
    )
    artifact = LetterboxReencodeArtifact(
        media_type="movie",
        movie_id=movie.id,
        media_file_id=old_media_file.id,
        original_path="/movies/Dune (2021)/old.mkv",
        candidate_path="/movies/Dune (2021)/new.mkv",
        original_size_bytes=10,
        candidate_size_bytes=9,
        original_signature="old",
        candidate_signature="sig-match",
        encoder="hevc_nvenc",
        encoder_family="nvidia",
        codec="hevc",
        crop_top=140,
        crop_bottom=140,
        status="replaced",
    )
    db.add(artifact)
    await db.commit()

    monkeypatch.setattr("marquee.core.sync_service.compute_signature", lambda _path: "sig-match")

    await _upsert_movie_media_file(
        db,
        movie,
        {
            "id": 2002,
            "path": "/movies/Dune (2021)/new.mkv",
            "relativePath": "new.mkv",
            "size": 12,
        },
    )
    await db.commit()

    state = (
        await db.execute(
            select(LetterboxState).where(
                LetterboxState.media_type == "movie",
                LetterboxState.movie_id == movie.id,
            )
        )
    ).scalar_one()
    reset_events = (
        await db.execute(
            select(LetterboxEvent).where(
                LetterboxEvent.media_type == "movie",
                LetterboxEvent.movie_id == movie.id,
                LetterboxEvent.action == "reset",
            )
        )
    ).scalars().all()
    current_media = (
        await db.execute(
            select(MediaFile).where(MediaFile.source_key == "radarr:movie-file:2002")
        )
    ).scalar_one()

    assert state.status == "reencoded"
    assert state.resolved_by == "reencode"
    assert old_media_file.is_active is False
    assert artifact.media_file_id == current_media.id
    assert reset_events == []


@pytest.mark.asyncio
async def test_movie_media_replacement_resets_letterbox_state_on_signature_mismatch(
    db: AsyncSession, monkeypatch
):
    movie = Movie(
        radarr_id=1,
        title="Dune",
        year=2021,
        folder_path="/movies/Dune (2021)",
        movie_file_path="old.mkv",
    )
    db.add(movie)
    await db.flush()
    old_media_file = MediaFile(
        movie_id=movie.id,
        source="radarr",
        source_key="radarr:movie-file:2001",
        path="/movies/Dune (2021)/old.mkv",
        relative_path="old.mkv",
        container="mkv",
        is_active=True,
    )
    db.add(old_media_file)
    db.add(
        LetterboxState(
            media_type="movie",
            movie_id=movie.id,
            status="reencoded",
            confidence="high",
            recommended_crop_top=140,
            recommended_crop_bottom=140,
            applied_crop_top=140,
            applied_crop_bottom=140,
            aspect_label="2.40:1",
            detect_method="cropdetect",
            samples_json="[]",
            reviewed=True,
            last_detected_at=datetime.now(UTC),
            resolved_by="reencode",
            resolved_at=datetime.now(UTC),
            original_crop_top=140,
            original_crop_bottom=140,
            original_aspect_label="2.40:1",
        )
    )
    db.add(
        LetterboxReencodeArtifact(
            media_type="movie",
            movie_id=movie.id,
            media_file_id=old_media_file.id,
            original_path="/movies/Dune (2021)/old.mkv",
            candidate_path="/movies/Dune (2021)/new.mkv",
            original_size_bytes=10,
            candidate_size_bytes=9,
            original_signature="old",
            candidate_signature="sig-match",
            encoder="hevc_nvenc",
            encoder_family="nvidia",
            codec="hevc",
            crop_top=140,
            crop_bottom=140,
            status="replaced",
        )
    )
    await db.commit()

    monkeypatch.setattr("marquee.core.sync_service.compute_signature", lambda _path: "sig-new")

    await _upsert_movie_media_file(
        db,
        movie,
        {
            "id": 2002,
            "path": "/movies/Dune (2021)/new.mkv",
            "relativePath": "new.mkv",
            "size": 12,
        },
    )
    await db.commit()

    state = (
        await db.execute(
            select(LetterboxState).where(
                LetterboxState.media_type == "movie",
                LetterboxState.movie_id == movie.id,
            )
        )
    ).scalar_one()
    reset_event = (
        await db.execute(
            select(LetterboxEvent).where(
                LetterboxEvent.media_type == "movie",
                LetterboxEvent.movie_id == movie.id,
                LetterboxEvent.action == "reset",
            )
        )
    ).scalar_one()

    assert state.status == "prefilter_candidate"
    assert state.recommended_crop_top is None
    assert state.applied_crop_top is None
    assert state.resolved_by is None
    assert "media_file_replaced" in reset_event.detail


@pytest.mark.asyncio
async def test_sync_movies_hdr_sdr_vs_unknown(db: AsyncSession):
    """Explicit SDR → False/False; absent dynamic-range info → NULL (unchecked)."""
    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(
            title="SDR Film",
            movieFile={"relativePath": "a.mkv", "mediaInfo": {"videoDynamicRange": "SDR"}},
        ),
        _radarr_movie(
            id=2,
            title="Unknown Film",
            tmdbId=2,
            movieFile={"relativePath": "b.mkv"},  # no mediaInfo
        ),
    ]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    sdr = (await db.execute(select(Movie).where(Movie.title == "SDR Film"))).scalar_one()
    assert sdr.hdr_type_raw == "SDR"
    assert sdr.has_hdr is False
    assert sdr.has_dv is False

    unknown = (await db.execute(select(Movie).where(Movie.title == "Unknown Film"))).scalar_one()
    assert unknown.hdr_type_raw is None
    assert unknown.has_hdr is None
    assert unknown.has_dv is None


@pytest.mark.asyncio
async def test_episode_media_replacement_resets_stale_letterbox_state_and_links(
    db: AsyncSession, monkeypatch
):
    series = Series(title="Breaking Bad", year=2008, sonarr_id=100, series_path="/tv/Breaking Bad")
    db.add(series)
    await db.flush()
    episode = Episode(
        series_id=series.id,
        sonarr_episode_id=1001,
        season_number=1,
        episode_number=1,
        title="Pilot",
        episode_file_path="/tv/Breaking Bad/Season 1/old.mkv",
    )
    db.add(episode)
    await db.flush()
    old_media_file = MediaFile(
        source="sonarr",
        source_key="sonarr:episode-file:5001",
        path="/tv/Breaking Bad/Season 1/old.mkv",
        relative_path="Season 1/old.mkv",
        container="mkv",
        is_active=True,
    )
    db.add(old_media_file)
    await db.flush()
    db.add(EpisodeMediaFile(episode_id=episode.id, media_file_id=old_media_file.id))
    db.add(
        LetterboxState(
            media_type="episode",
            episode_id=episode.id,
            status="candidate",
            confidence="high",
            recommended_crop_top=120,
            recommended_crop_bottom=120,
            reviewed=True,
            last_detected_at=datetime.now(UTC),
        )
    )
    await db.commit()

    monkeypatch.setattr("marquee.core.sync_service.compute_signature", lambda _path: "sig-new")

    await _upsert_episode_media_files(
        db,
        [_sonarr_episode(episodeFileId=5002)],
        {
            5002: _sonarr_episode_file(
                id=5002,
                relativePath="Season 1/new.mkv",
                path="/tv/Breaking Bad/Season 1/new.mkv",
            )
        },
    )
    await db.commit()

    state = (
        await db.execute(
            select(LetterboxState).where(
                LetterboxState.media_type == "episode",
                LetterboxState.episode_id == episode.id,
            )
        )
    ).scalar_one()
    links = (
        await db.execute(
            select(EpisodeMediaFile).where(EpisodeMediaFile.episode_id == episode.id)
        )
    ).scalars().all()
    new_media = (
        await db.execute(
            select(MediaFile).where(MediaFile.source_key == "sonarr:episode-file:5002")
        )
    ).scalar_one()
    reset_event = (
        await db.execute(
            select(LetterboxEvent).where(
                LetterboxEvent.media_type == "episode",
                LetterboxEvent.episode_id == episode.id,
                LetterboxEvent.action == "reset",
            )
        )
    ).scalar_one()

    assert state.status == "prefilter_candidate"
    assert state.recommended_crop_top is None
    assert old_media_file.is_active is False
    assert [link.media_file_id for link in links] == [new_media.id]
    assert "media_file_replaced" in reset_event.detail


@pytest.mark.asyncio
async def test_sync_movies_populates_hdr_variants_from_raw(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(
            title="Plus",
            movieFile={
                "relativePath": "plus.mkv",
                "mediaInfo": {"videoDynamicRangeType": "HDR10Plus"},
            },
        ),
        _radarr_movie(
            id=2,
            title="Generic",
            tmdbId=2,
            movieFile={
                "relativePath": "generic.mkv",
                "mediaInfo": {"videoDynamicRangeType": "HLG"},
            },
        ),
        _radarr_movie(
            id=3,
            title="DoVi Only",
            tmdbId=3,
            movieFile={"relativePath": "dovi.mkv", "mediaInfo": {"videoDynamicRangeType": "DV"}},
        ),
    ]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    plus = (await db.execute(select(Movie).where(Movie.title == "Plus"))).scalar_one()
    generic = (await db.execute(select(Movie).where(Movie.title == "Generic"))).scalar_one()
    dovi_only = (await db.execute(select(Movie).where(Movie.title == "DoVi Only"))).scalar_one()

    assert plus.hdr_type_raw == "HDR10Plus"
    assert plus.has_hdr is True
    assert plus.has_dv is False
    assert generic.hdr_type_raw == "HLG"
    assert generic.has_hdr is True
    assert generic.has_dv is False
    assert dovi_only.hdr_type_raw == "DV"
    assert dovi_only.has_hdr is False
    assert dovi_only.has_dv is True


@pytest.mark.asyncio
async def test_sync_movies_syncs_overlay_profile_and_cf_scores(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(
            movieFile={
                "relativePath": "Dune (2021).mkv",
                "mediaInfo": {"videoDynamicRangeType": "DV HDR10"},
            }
        )
    ]
    radarr.get_movie_files.return_value = [
        {
            "id": 2001,
            "movieId": 1,
            "relativePath": "Dune (2021).mkv",
            "qualityCutoffNotMet": False,
            "customFormatScore": 25,
            "customFormats": [
                {"id": 15, "name": "Dolby Vision"},
                {"id": 20, "name": "HDR10+"},
            ],
            "mediaInfo": {"videoDynamicRangeType": "DV HDR10"},
        }
    ]
    radarr.get_custom_formats.return_value = [
        {
            "id": 15,
            "name": "Dolby Vision",
            "includeCustomFormatWhenRenaming": True,
            "specifications": [
                {"fields": [{"name": "value", "value": r"\b(DV|DOLBY[ .]?VISION)\b"}]}
            ],
        },
        {
            "id": 20,
            "name": "HDR10+",
            "includeCustomFormatWhenRenaming": False,
            "specifications": [
                {"fields": [{"name": "value", "value": r"\b(HDR10PLUS|HDR10\+)\b"}]}
            ],
        },
    ]
    radarr.get_quality_profiles.return_value = [
        {
            "id": 3,
            "name": "UHD",
            "upgradeAllowed": True,
            "cutoffFormatScore": 100,
            "minFormatScore": 0,
            "formatItems": [
                {"format": 15, "name": "Dolby Vision", "score": 15},
                {"format": 20, "name": "HDR10+", "score": 10},
            ],
        }
    ]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    custom_formats = (await db.execute(select(RadarrCustomFormat))).scalars().all()
    profiles = (await db.execute(select(RadarrQualityProfile))).scalars().all()
    profile_items = (await db.execute(select(RadarrProfileFormatItem))).scalars().all()
    movie_scores = (await db.execute(select(MovieCustomFormatScore))).scalars().all()

    assert movie.quality_cutoff_met is True
    assert movie.current_cf_score == 25
    assert {row.name for row in custom_formats} == {"Dolby Vision", "HDR10+"}
    assert [row.name for row in profiles] == ["UHD"]
    assert {(row.profile_id, row.custom_format_id, row.score) for row in profile_items} == {
        (3, 15, 15),
        (3, 20, 10),
    }
    assert {(row.movie_id, row.custom_format_id, row.score) for row in movie_scores} == {
        (movie.id, 15, 15),
        (movie.id, 20, 10),
    }
    radarr.get_movie_files.assert_awaited_once_with([1])


@pytest.mark.asyncio
async def test_sync_movies_replaces_stale_movie_cf_scores(db: AsyncSession):
    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(
            movieFile={
                "relativePath": "first.mkv",
                "mediaInfo": {"videoDynamicRangeType": "DV HDR10"},
            }
        )
    ]
    radarr.get_movie_files.return_value = [
        {
            "id": 2001,
            "movieId": 1,
            "relativePath": "first.mkv",
            "customFormatScore": 15,
            "customFormats": [{"id": 15, "name": "Dolby Vision"}],
            "mediaInfo": {"videoDynamicRangeType": "DV HDR10"},
        }
    ]
    radarr.get_custom_formats.return_value = [
        {
            "id": 15,
            "name": "Dolby Vision",
            "includeCustomFormatWhenRenaming": False,
            "specifications": [],
        },
        {
            "id": 20,
            "name": "HDR10+",
            "includeCustomFormatWhenRenaming": False,
            "specifications": [],
        },
    ]
    radarr.get_quality_profiles.return_value = [
        {
            "id": 3,
            "name": "UHD",
            "formatItems": [
                {"format": 15, "name": "Dolby Vision", "score": 15},
                {"format": 20, "name": "HDR10+", "score": 10},
            ],
        }
    ]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    radarr.get_movies.return_value = [
        _radarr_movie(
            movieFile={
                "relativePath": "second.mkv",
                "mediaInfo": {"videoDynamicRangeType": "HDR10Plus"},
            }
        )
    ]
    radarr.get_movie_files.return_value = [
        {
            "id": 2002,
            "movieId": 1,
            "relativePath": "second.mkv",
            "customFormatScore": 10,
            "customFormats": [{"id": 20, "name": "HDR10+"}],
            "mediaInfo": {"videoDynamicRangeType": "HDR10Plus"},
        }
    ]
    await svc.sync_all()

    movie = (await db.execute(select(Movie).where(Movie.radarr_id == 1))).scalar_one()
    rows = (await db.execute(select(MovieCustomFormatScore))).scalars().all()
    assert movie.hdr_type_raw == "HDR10Plus"
    assert movie.current_cf_score == 10
    assert {(row.movie_id, row.custom_format_id, row.score) for row in rows} == {(movie.id, 20, 10)}


@pytest.mark.asyncio
async def test_sync_movies_clears_stale_poster_path(db: AsyncSession):
    """A recorded poster whose file no longer exists must be NULLed.

    Radarr upgrades delete and recreate the movie folder; if sync keeps the
    stale path, the item stays "complete" forever and is never queued for
    re-selection (NULL poster_path = needs poster).
    """
    movie = Movie(
        radarr_id=1,
        title="Dune",
        year=2021,
        folder_path="/movies/Dune",
        poster_path="/movies/Dune (2021)/poster-that-was-deleted.jpg",
        poster_ai_selected=True,
    )
    db.add(movie)
    await db.flush()

    radarr = AsyncMock()
    radarr.get_movies.return_value = [_radarr_movie()]

    svc = SyncService(db, radarr=radarr)
    await svc.sync_all()

    await db.refresh(movie)
    assert movie.poster_path is None
    assert movie.needs_poster


@pytest.mark.asyncio
async def test_sync_series_missing_title_does_not_poison_commit(db: AsyncSession):
    """A title-less Sonarr entry must be skipped, not abort the whole sync."""
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [
        {"id": 200, "title": None, "path": "/tv/Broken"},
        _sonarr_series(),
    ]
    sonarr.get_episodes.return_value = [_sonarr_episode()]
    sonarr.get_episode_files.return_value = [_sonarr_episode_file()]

    svc = SyncService(db, sonarr=sonarr)
    report = await svc.sync_all()

    assert report.series.errors == 1
    assert report.series.created == 1  # the valid one still lands
    series = (await db.execute(select(Series).where(Series.sonarr_id == 100))).scalar_one()
    assert series.title == "Breaking Bad"


@pytest.mark.asyncio
async def test_sync_movies_counts_errors(db: AsyncSession):
    """A malformed movie entry should increment errors, not crash."""
    radarr = AsyncMock()
    radarr.get_movies.return_value = [
        _radarr_movie(),
        {"id": 2, "title": None},  # missing required fields
    ]

    svc = SyncService(db, radarr=radarr)
    report = await svc.sync_all()

    assert report.movies.created == 1
    assert report.movies.errors >= 1  # the bad entry


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_series_creates_new(db: AsyncSession):
    """A new series from Sonarr should be inserted with seasons and episodes."""
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series()]
    sonarr.get_episodes.return_value = [_sonarr_episode()]
    sonarr.get_episode_files.return_value = [_sonarr_episode_file()]

    svc = SyncService(db, sonarr=sonarr)
    report = await svc.sync_all()

    assert report.series.created == 1

    series = (await db.execute(select(Series).where(Series.sonarr_id == 100))).scalar_one()
    assert series.title == "Breaking Bad"
    assert series.tvdb_id == 81189
    assert series.season_count == 2

    # Seasons
    seasons = (
        (
            await db.execute(
                select(Season).where(Season.series_id == series.id).order_by(Season.season_number)
            )
        )
        .scalars()
        .all()
    )
    assert len(seasons) == 2
    assert seasons[0].season_number == 1
    assert seasons[1].season_number == 2

    # Episodes
    episodes = (
        (await db.execute(select(Episode).where(Episode.series_id == series.id))).scalars().all()
    )
    assert len(episodes) == 1
    assert episodes[0].title == "Pilot"
    assert episodes[0].episode_file_path == "/tv/Breaking Bad/Season 1/Breaking Bad - S01E01.mkv"


@pytest.mark.asyncio
async def test_full_series_sync_retires_and_reactivates_descendants(db: AsyncSession):
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series()]
    sonarr.get_episodes.return_value = [_sonarr_episode()]
    sonarr.get_episode_files.return_value = [_sonarr_episode_file()]
    sonarr.get_custom_formats.return_value = []
    sonarr.get_quality_profiles.return_value = []
    await SyncService(db, sonarr=sonarr).sync_all()

    series = await db.scalar(select(Series).where(Series.sonarr_id == 100))
    assert series is not None
    season = await db.scalar(select(Season).where(Season.series_id == series.id))
    episode = await db.scalar(select(Episode).where(Episode.series_id == series.id))
    media_file = await db.scalar(
        select(MediaFile)
        .join(EpisodeMediaFile, EpisodeMediaFile.media_file_id == MediaFile.id)
        .where(EpisodeMediaFile.episode_id == episode.id)
    )
    assert season is not None and episode is not None and media_file is not None

    sonarr.get_series.return_value = []
    await SyncService(db, sonarr=sonarr).sync_all()
    for row in (series, season, episode, media_file):
        await db.refresh(row)
        assert row.is_present is False and row.retired_at is not None

    sonarr.get_series.return_value = [_sonarr_series()]
    await SyncService(db, sonarr=sonarr).sync_all()
    for row in (series, season, episode, media_file):
        await db.refresh(row)
        assert row.is_present is True and row.retired_at is None


@pytest.mark.asyncio
async def test_sync_series_syncs_season_zero(db: AsyncSession):
    """Season 0 (Specials) should be synced."""
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [
        _sonarr_series(
            seasons=[
                {"seasonNumber": 0, "monitored": False},
                {"seasonNumber": 1, "monitored": True},
            ]
        )
    ]
    sonarr.get_episodes.return_value = []
    sonarr.get_episode_files.return_value = []

    svc = SyncService(db, sonarr=sonarr)
    await svc.sync_all()

    series = (await db.execute(select(Series).where(Series.sonarr_id == 100))).scalar_one()
    seasons = (
        (await db.execute(select(Season).where(Season.series_id == series.id))).scalars().all()
    )
    assert len(seasons) == 2
    assert {s.season_number for s in seasons} == {0, 1}


@pytest.mark.asyncio
async def test_sync_series_handles_missing_tvdb_id(db: AsyncSession):
    """Series without tvdbId should still sync (stored as NULL)."""
    sonarr = AsyncMock()
    sonarr.get_series.return_value = [_sonarr_series(tvdbId=0)]
    sonarr.get_episodes.return_value = []
    sonarr.get_episode_files.return_value = []

    svc = SyncService(db, sonarr=sonarr)
    await svc.sync_all()

    series = (await db.execute(select(Series).where(Series.sonarr_id == 100))).scalar_one()
    assert series.tvdb_id is None


# ---------------------------------------------------------------------------
# Poster path resolution
# ---------------------------------------------------------------------------


def test_resolve_movie_poster_default():
    """Default format: poster.jpg inside movie folder."""
    movie = Movie(id=1, title="Dune", year=2021, folder_path="/movies/Dune (2021)")
    with patch(
        "marquee.core.sync_service.safe_translate_and_validate",
        return_value=Path("/movies/Dune (2021)"),
    ):
        result = _resolve_poster_path(movie)
    assert result == Path("/movies/Dune (2021)/poster.jpg")


def test_resolve_movie_poster_with_basename():
    """{movie_basename} should be replaced with the media file stem."""
    from marquee.config import settings

    movie = Movie(
        id=1,
        title="Dune",
        year=2021,
        folder_path="/movies/Dune (2021)",
        movie_file_path="Dune (2021).mkv",
    )
    with (
        patch(
            "marquee.core.sync_service.safe_translate_and_validate",
            return_value=Path("/movies/Dune (2021)"),
        ),
        patch.object(settings, "MOVIE_POSTER_FORMAT", "{movie_basename}.jpg"),
    ):
        result = _resolve_poster_path(movie)
    assert result == Path("/movies/Dune (2021)/Dune (2021).jpg")


def test_resolve_series_poster():
    """Series poster should use SERIES_POSTER_FORMAT."""
    from marquee.config import settings

    series = Series(id=1, title="Breaking Bad", year=2008, series_path="/tv/Breaking Bad")
    with (
        patch(
            "marquee.core.sync_service.safe_translate_and_validate",
            return_value=Path("/tv/Breaking Bad"),
        ),
        patch.object(settings, "SERIES_POSTER_FORMAT", "poster.jpg"),
    ):
        result = _resolve_poster_path(series)
    assert result == Path("/tv/Breaking Bad/poster.jpg")


def test_resolve_season_poster():
    """Season poster should use SEASON_POSTER_FORMAT with season number."""
    from marquee.config import settings

    series = Series(series_path="/tv/Breaking Bad")
    season = Season(series_id=1, season_number=3)

    with (
        patch(
            "marquee.core.sync_service.safe_translate_and_validate",
            return_value=Path("/tv/Breaking Bad"),
        ),
        patch.object(settings, "SEASON_POSTER_FORMAT", "season{season:02d}-poster.jpg"),
    ):
        result = _resolve_poster_path(season, series=series)
    assert result == Path("/tv/Breaking Bad/season03-poster.jpg")


def test_resolve_poster_invalid_path_returns_none():
    """If path validation fails, return None gracefully."""
    movie = Movie(title="Test", year=2024, folder_path="/bad/../escape")
    with patch(
        "marquee.core.sync_service.safe_translate_and_validate",
        side_effect=ValueError("outside allowed roots"),
    ):
        result = _resolve_poster_path(movie)
    assert result is None


# ---------------------------------------------------------------------------
# Sync skips when clients are missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_all_skips_missing_clients(db: AsyncSession):
    """If neither Radarr nor Sonarr is configured, sync_all should succeed
    with an empty report."""
    svc = SyncService(db, radarr=None, sonarr=None)
    report = await svc.sync_all()

    assert report.movies.total == 0
    assert report.series.total == 0
    assert report.duration_seconds >= 0
