"""Schema tests for the new foundation models added for the feature build:

- ``Movie.genres`` JSON round-trip
- ``Movie`` artwork deploy columns
- ``PipelineRun`` insert + status lifecycle
- ``ArtworkEvent`` append-only audit rows
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from marquee.models import ArtworkEvent, Movie, PipelineRun


async def _make_movie(db, **kwargs) -> Movie:
    movie = Movie(
        title=kwargs.pop("title", "Die Hard"),
        year=kwargs.pop("year", 1988),
        folder_path=kwargs.pop("folder_path", "/movies/Die Hard (1988)"),
        **kwargs,
    )
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    return movie


@pytest.mark.asyncio
async def test_movie_genres_json_roundtrip(db):
    movie = await _make_movie(db, genres=["Action", "Thriller"])
    fetched = (await db.execute(select(Movie).where(Movie.id == movie.id))).scalar_one()
    assert fetched.genres == ["Action", "Thriller"]


@pytest.mark.asyncio
async def test_movie_genres_default_none(db):
    movie = await _make_movie(db)
    assert movie.genres is None


@pytest.mark.asyncio
async def test_movie_artwork_deploy_columns_default(db):
    movie = await _make_movie(db)
    # poster_user_approved is NOT NULL default False; the other two are NULL.
    assert movie.poster_user_approved is False
    assert movie.poster_deployed_filename is None
    assert movie.poster_deployed_at is None


@pytest.mark.asyncio
async def test_pipeline_run_insert(db):
    movie = await _make_movie(db)
    run = PipelineRun(
        run_id="abc123",
        movie_id=movie.id,
        status="running",
        scorer_name="weighted",
        output_dir="/tmp/runs/Die Hard",
    )
    db.add(run)
    await db.commit()

    fetched = (
        await db.execute(select(PipelineRun).where(PipelineRun.run_id == "abc123"))
    ).scalar_one()
    assert fetched.movie_id == movie.id
    assert fetched.status == "running"
    assert fetched.started_at is not None
    assert fetched.completed_at is None
    assert fetched.feedback_event_id is None


@pytest.mark.asyncio
async def test_artwork_event_audit_row(db):
    movie = await _make_movie(db)
    event = ArtworkEvent(
        movie_id=movie.id,
        action="deploy",
        source="pipeline",
        detail='{"path": "/movies/Die Hard (1988)/poster.jpg"}',
    )
    db.add(event)
    await db.commit()

    rows = (
        (await db.execute(select(ArtworkEvent).where(ArtworkEvent.movie_id == movie.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].action == "deploy"
    assert rows[0].source == "pipeline"
    assert rows[0].created_at is not None
