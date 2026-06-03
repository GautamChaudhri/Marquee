"""Library routes — browse and inspect media items."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("/movies")
async def list_movies():
    """List all movies. Phase 4 — not yet implemented."""
    raise HTTPException(status_code=501, detail="Not implemented — Phase 4")


@router.get("/movies/{movie_id}")
async def get_movie(movie_id: int):
    """Get a single movie by ID. Phase 4 — not yet implemented."""
    raise HTTPException(status_code=501, detail="Not implemented — Phase 4")


@router.get("/series")
async def list_series():
    """List all TV series. Phase 4 — not yet implemented."""
    raise HTTPException(status_code=501, detail="Not implemented — Phase 4")


@router.get("/series/{series_id}")
async def get_series(series_id: int):
    """Get a single series by ID. Phase 4 — not yet implemented."""
    raise HTTPException(status_code=501, detail="Not implemented — Phase 4")


@router.get("/series/{series_id}/seasons")
async def list_seasons(series_id: int):
    """List seasons for a series. Phase 4 — not yet implemented."""
    raise HTTPException(status_code=501, detail="Not implemented — Phase 4")
