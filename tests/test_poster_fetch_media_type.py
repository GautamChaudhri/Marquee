"""The TMDB fetch stage has to address the namespace the subject actually lives in.

TMDB allocates ids per namespace, so sending a series id to ``/movie`` is not a
reliable 404 — where that id also exists as a movie it returns an unrelated
film's posters, and the run deploys them onto the show.
"""

from __future__ import annotations

import pytest

# delivery must land before poster_pipeline: the kernel handler registry at the
# foot of delivery imports back into poster_pipeline, so reaching that module
# first hits it half-initialized. Importing marquee.main has the same effect.
from marquee.core.jobs import delivery  # noqa: F401
from marquee.core.jobs.documents import PosterPipelineRequestV1
from marquee.core.jobs.poster_pipeline import _subject_params
from marquee.models import Movie
from marquee.pipeline.runner import fetch_candidates


class RecordingTMDB:
    """Records which endpoint family the stage reached for."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def get_movie_images(self, tmdb_id, include_language="en,null"):
        self.calls.append(("movie_images", tmdb_id))
        return []

    async def get_tv_images(self, tmdb_id, include_language="en,null"):
        self.calls.append(("tv_images", tmdb_id))
        return []

    async def get_season_images(self, tmdb_id, season_number, include_language="en,null"):
        self.calls.append(("season_images", tmdb_id, season_number))
        return []

    async def get_movie_primary_poster(self, tmdb_id):
        self.calls.append(("movie_primary", tmdb_id))
        return "movie.jpg"

    async def get_tv_primary_poster(self, tmdb_id):
        self.calls.append(("tv_primary", tmdb_id))
        return "tv.jpg"

    async def get_season_primary_poster(self, tmdb_id, season_number):
        self.calls.append(("season_primary", tmdb_id, season_number))
        return "season.jpg"


@pytest.mark.asyncio
async def test_movie_subject_uses_the_movie_namespace():
    tmdb = RecordingTMDB()
    _, primary = await fetch_candidates(
        tmdb, Movie(id=1, title="A Film", tmdb_id=550), media_type="movie"
    )
    assert tmdb.calls == [("movie_images", 550), ("movie_primary", 550)]
    assert primary == "movie.jpg"


@pytest.mark.asyncio
async def test_series_subject_uses_the_tv_namespace():
    tmdb = RecordingTMDB()
    _, primary = await fetch_candidates(
        tmdb, Movie(id=1, title="A Show", tmdb_id=196322), media_type="series"
    )
    assert tmdb.calls == [("tv_images", 196322), ("tv_primary", 196322)]
    assert primary == "tv.jpg"


@pytest.mark.asyncio
async def test_season_subject_uses_the_season_namespace():
    tmdb = RecordingTMDB()
    _, primary = await fetch_candidates(
        tmdb,
        Movie(id=1, title="A Show · Season 2", tmdb_id=196322),
        media_type="season",
        season_number=2,
    )
    assert tmdb.calls == [("season_images", 196322, 2), ("season_primary", 196322, 2)]
    assert primary == "season.jpg"


@pytest.mark.asyncio
async def test_season_without_a_number_is_refused_before_any_request():
    tmdb = RecordingTMDB()
    with pytest.raises(ValueError, match="season number"):
        await fetch_candidates(
            tmdb, Movie(id=1, title="A Show", tmdb_id=196322), media_type="season"
        )
    assert tmdb.calls == []


@pytest.mark.asyncio
async def test_unknown_media_type_is_refused_rather_than_defaulting_to_movie():
    tmdb = RecordingTMDB()
    with pytest.raises(ValueError, match="unsupported poster media type"):
        await fetch_candidates(
            tmdb, Movie(id=1, title="Thing", tmdb_id=1), media_type="episode"
        )
    assert tmdb.calls == []


def test_subject_params_carry_the_media_type_and_season_number():
    """The runner runs in a separate process and sees only these params."""
    series = _subject_params(
        PosterPipelineRequestV1(series_id=4, tmdb_id=196322, title="A Show"),
        {"kind": "series", "series_id": 4},
    )
    assert series["media_type"] == "series"
    assert series["season_number"] is None

    season = _subject_params(
        PosterPipelineRequestV1(season_id=9, tmdb_id=196322, title="A Show · Season 2"),
        {"kind": "season", "season_id": 9, "season_number": 2},
    )
    assert season["media_type"] == "season"
    assert season["season_number"] == 2
    # The series' TMDB id addresses the season endpoint; season_id is ours.
    assert season["tmdb_id"] == 196322

    movie = _subject_params(
        PosterPipelineRequestV1(movie_id=7, tmdb_id=550, title="A Film"),
        {"kind": "movie", "movie_id": 7},
    )
    assert movie["media_type"] == "movie"
    assert movie["season_number"] is None


def test_season_run_without_a_snapshot_number_fails_at_submission():
    with pytest.raises(ValueError, match="season number"):
        _subject_params(
            PosterPipelineRequestV1(season_id=9, tmdb_id=196322, title="A Show · Season 2"),
            {"kind": "season", "season_id": 9},
        )
