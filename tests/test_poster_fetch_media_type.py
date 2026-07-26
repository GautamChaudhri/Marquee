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
from marquee.core.jobs.internal_runner import _ocr_gate_context
from marquee.core.jobs.poster_pipeline import _subject_params, _text_gate_params
from marquee.models import Movie
from marquee.pipeline.orchestrator import PosterSubjectInput
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


# ── OCR text gate scope ──────────────────────────────────────────────────
# The gate has always had movie/show/season profiles; nothing selected one, so
# every run used the movie default. That silently applies a user's custom movie
# profile to TV and skips the season profile built for season art.


def _subject(media_type: str, season_number: int | None = None) -> PosterSubjectInput:
    return PosterSubjectInput(
        title="A Show", media_type=media_type, tmdb_id=1, season_number=season_number
    )


def test_gate_context_uses_the_scope_the_host_resolved():
    ctx = _ocr_gate_context(
        {"text_gate": {"scope": "season", "profile_id": None, "director": "D"}},
        _subject("season", 2),
    )
    assert ctx.scope == "season"
    assert ctx.season_number == 2
    assert ctx.director == "D"
    # The season fallback is the profile that expects "SEASON 2" on the art.
    assert ctx.profile.settings.allow_season is True
    assert ctx.profile.settings.require_title is False


def test_gate_context_falls_back_to_the_subject_scope():
    """An in-flight job enqueued before this change still gates correctly."""
    for media_type, expected in (("movie", "movie"), ("series", "show"), ("season", "season")):
        ctx = _ocr_gate_context({}, _subject(media_type, 1 if media_type == "season" else None))
        assert ctx.scope == expected


def test_gate_context_rejects_an_unknown_scope_rather_than_trusting_it():
    ctx = _ocr_gate_context({"text_gate": {"scope": "nonsense"}}, _subject("series"))
    assert ctx.scope == "show"


@pytest.mark.asyncio
async def test_text_gate_params_resolve_per_subject(db):
    from marquee.models import Movie, Season, Series

    movie = Movie(title="A Film", year=2024, folder_path="/m/f", tmdb_id=550, director="Fincher")
    series = Series(
        title="A Show", year=2014, series_path="/t/s", tmdb_id=59659, director="Creator"
    )
    db.add_all([movie, series])
    await db.flush()
    season = Season(series_id=series.id, season_number=3)
    db.add(season)
    await db.commit()

    movie_gate = await _text_gate_params(
        db, PosterPipelineRequestV1(movie_id=movie.id, tmdb_id=550, title="A Film"), "movie"
    )
    assert movie_gate["scope"] == "movie"
    assert movie_gate["director"] == "Fincher"

    series_gate = await _text_gate_params(
        db, PosterPipelineRequestV1(series_id=series.id, tmdb_id=59659, title="A Show"), "series"
    )
    assert series_gate["scope"] == "show"
    assert series_gate["director"] == "Creator"

    # A season carries the *series* metadata — a season row has none of its own.
    season_gate = await _text_gate_params(
        db,
        PosterPipelineRequestV1(season_id=season.id, tmdb_id=59659, title="A Show · Season 3"),
        "season",
    )
    assert season_gate["scope"] == "season"
    assert season_gate["director"] == "Creator"
