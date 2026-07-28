"""Text profile CRUD + default selection (design/plans/03 §Group B).

Profiles govern what text the OCR gate tolerates on posters. Built-ins
(``title_only``, ``textless``) are immutable; custom profiles persist in
``data/text_profiles.json``. The per-movie override endpoints live here too
(they read/write ``Movie.text_profile_id``).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.text_profiles import (
    SCOPES,
    TextProfileError,
    create_profile,
    delete_profile,
    get_active_profile,
    get_default_profile_id,
    load_profiles,
    set_default_profile,
    update_profile,
)
from marquee.database import get_db
from marquee.models import Movie, Series

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/text-profiles", tags=["text-profiles"])
DbSession = Annotated[AsyncSession, Depends(get_db)]


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    settings: dict[str, Any] = Field(default_factory=dict)


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    settings: dict[str, Any] | None = None


class MovieProfileUpdate(BaseModel):
    profile_id: str | None = None


class SeriesProfileUpdate(BaseModel):
    show_profile_id: str | None = None
    season_profile_id: str | None = None


# ── Per-Entity Overrides (placed first to prevent scope collision) ───────


@router.get("/movie/{movie_id:int}")
async def get_movie_text_profile(movie_id: int, db: DbSession):
    movie = await db.get(Movie, movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie {movie_id} not found")
    effective = get_active_profile("movie", movie.text_profile_id).id
    return {
        "movie_id": movie_id,
        "profile_id": movie.text_profile_id,
        "effective_id": effective,
    }


@router.put("/movie/{movie_id:int}")
async def set_movie_text_profile(
    movie_id: int,
    body: MovieProfileUpdate,
    db: DbSession,
):
    movie = await db.get(Movie, movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail=f"Movie {movie_id} not found")

    profiles = load_profiles("movie")
    if body.profile_id is not None and body.profile_id not in profiles:
        raise HTTPException(status_code=400, detail=f"Unknown text profile {body.profile_id!r}")

    movie.text_profile_id = body.profile_id
    await db.commit()
    effective = get_active_profile("movie", movie.text_profile_id).id
    logger.info("TEXT PROFILES | movie %s override -> %s", movie_id, movie.text_profile_id)
    return {
        "movie_id": movie_id,
        "profile_id": movie.text_profile_id,
        "effective_id": effective,
    }


@router.get("/series/{series_id:int}")
async def get_series_text_profile(series_id: int, db: DbSession):
    series = await db.get(Series, series_id)
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series {series_id} not found")
    effective_show = get_active_profile("show", series.show_text_profile_id).to_dict()
    effective_season = get_active_profile("season", series.season_text_profile_id).to_dict()
    return {
        "show_profile_id": series.show_text_profile_id,
        "season_profile_id": series.season_text_profile_id,
        "effective_show": effective_show,
        "effective_season": effective_season,
    }


@router.put("/series/{series_id:int}")
async def set_series_text_profile(
    series_id: int,
    body: SeriesProfileUpdate,
    db: DbSession,
):
    series = await db.get(Series, series_id)
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series {series_id} not found")

    if body.show_profile_id is not None:
        profiles_show = load_profiles("show")
        if body.show_profile_id not in profiles_show:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown text profile {body.show_profile_id!r} for scope show",
            )

    if body.season_profile_id is not None:
        profiles_season = load_profiles("season")
        if body.season_profile_id not in profiles_season:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown text profile {body.season_profile_id!r} for scope season",
            )

    series.show_text_profile_id = body.show_profile_id
    series.season_text_profile_id = body.season_profile_id
    await db.commit()

    effective_show = get_active_profile("show", series.show_text_profile_id).to_dict()
    effective_season = get_active_profile("season", series.season_text_profile_id).to_dict()
    logger.info(
        "TEXT PROFILES | series %s override -> show: %s, season: %s",
        series_id,
        series.show_text_profile_id,
        series.season_text_profile_id,
    )
    return {
        "show_profile_id": series.show_text_profile_id,
        "season_profile_id": series.season_text_profile_id,
        "effective_show": effective_show,
        "effective_season": effective_season,
    }


# ── Global Panel CRUD (Scoped) ───────────────────────────────────────────


@router.get("")
async def list_text_profiles():
    result = {}
    for scope in SCOPES:
        profiles = load_profiles(scope)
        ordered = sorted(profiles.values(), key=lambda p: (not p.builtin, p.name.lower()))
        result[scope] = {
            "profiles": [p.to_dict() for p in ordered],
            "default_id": get_default_profile_id(scope),
        }
    return {"scopes": result}


@router.post("/{scope}", status_code=201)
async def create_text_profile(scope: str, body: ProfileCreate):
    if scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"Unknown scope: {scope!r}")
    try:
        profile = create_profile(scope, body.name, body.settings)
    except TextProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("TEXT PROFILES | created %s in scope %s", profile.id, scope)
    return profile.to_dict()


@router.put("/{scope}/default/{profile_id}")
async def set_default_text_profile(scope: str, profile_id: str):
    if scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"Unknown scope: {scope!r}")
    try:
        set_default_profile(scope, profile_id)
    except TextProfileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.info("TEXT PROFILES | default %s -> %s", scope, profile_id)
    return {"default_id": profile_id}


@router.put("/{scope}/{profile_id}")
async def update_text_profile(scope: str, profile_id: str, body: ProfileUpdate):
    if scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"Unknown scope: {scope!r}")
    try:
        profile = update_profile(scope, profile_id, name=body.name, settings=body.settings)
    except TextProfileError as exc:
        status = 404 if "Unknown" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    logger.info("TEXT PROFILES | updated %s in scope %s", profile.id, scope)
    return profile.to_dict()


@router.delete("/{scope}/{profile_id}")
async def delete_text_profile(scope: str, profile_id: str):
    if scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"Unknown scope: {scope!r}")
    try:
        delete_profile(scope, profile_id)
    except TextProfileError as exc:
        status = 404 if "Unknown" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    logger.info("TEXT PROFILES | deleted %s in scope %s", profile_id, scope)
    return {"deleted": profile_id}
