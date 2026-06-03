"""Pipeline routes — trigger poster processing."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.post("/movie/{movie_id}")
async def process_movie(movie_id: int):
    """Run the poster pipeline for a single movie. Phase 3 — not yet implemented."""
    raise HTTPException(status_code=501, detail="Not implemented — Phase 3")


@router.post("/series/{series_id}")
async def process_series(series_id: int):
    """Run the poster pipeline for a TV series (series + season posters).
    Phase 3 — not yet implemented."""
    raise HTTPException(status_code=501, detail="Not implemented — Phase 3")


@router.post("/missing")
async def process_all_missing():
    """Run the poster pipeline for every item needing a poster.
    Phase 3 — not yet implemented."""
    raise HTTPException(status_code=501, detail="Not implemented — Phase 3")
