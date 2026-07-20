"""JMC6H H19 — review-reset semantics.

A review-queue reset changes review *disposition* only. It must never null the
database poster state while the deployed poster file stays on disk (that would
leave the DB and filesystem disagreeing). Clearing or removing deployed artwork is
the separately authorized canonical ``poster_reset`` job.
"""

from __future__ import annotations

import pytest

from marquee.api.routes.pipeline import reset_review_queue
from marquee.models import Movie
from tests.support.canonical_poster import seed_canonical_pipeline_run


@pytest.mark.asyncio
async def test_review_reset_changes_disposition_only_not_poster_state(db):
    movie = Movie(
        title="Deployed Movie",
        year=2020,
        folder_path="/library/Deployed",
        tmdb_id=5551,
        poster_path="/library/Deployed/poster.jpg",
        poster_source="tmdb",
        poster_ai_selected=True,
        poster_deployed_filename="poster.jpg",
    )
    db.add(movie)
    await db.flush()
    run = await seed_canonical_pipeline_run(
        db,
        run_id="review-reset-run",
        movie_id=movie.id,
        archive={"run_id": "review-reset-run", "movie_id": movie.id, "candidates": []},
    )
    await db.commit()

    result = await reset_review_queue(db)

    assert result["reset"] == 1
    assert result["runs_cleared"] == 1
    # H19: the reset must not clear any poster state.
    assert result["posters_reset"] == 0

    await db.refresh(run)
    await db.refresh(movie)
    # Review disposition changed — the run leaves the Review tab.
    assert run.feedback_event_id is not None
    # Deployed poster DB state is completely untouched (no DB/filesystem disagreement).
    assert movie.poster_path == "/library/Deployed/poster.jpg"
    assert movie.poster_deployed_filename == "poster.jpg"
    assert movie.poster_source == "tmdb"
    assert movie.poster_ai_selected is True
