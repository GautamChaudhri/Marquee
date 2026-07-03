"""Text profile CRUD + default selection (design/plans/03 §Group B).

Profiles govern what text the OCR gate tolerates on posters. Built-ins
(``title_only``, ``textless``) are immutable; custom profiles persist in
``data/text_profiles.json``. The per-movie override endpoints live here too
(they read/write ``Movie.text_profile_id``).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from marquee.core.text_profiles import (
    TextProfileError,
    create_profile,
    delete_profile,
    get_default_profile_id,
    load_profiles,
    set_default_profile,
    update_profile,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/text-profiles", tags=["text-profiles"])


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    settings: dict[str, Any] = Field(default_factory=dict)


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    settings: dict[str, Any] | None = None


@router.get("")
async def list_text_profiles():
    profiles = load_profiles()
    ordered = sorted(profiles.values(), key=lambda p: (not p.builtin, p.name.lower()))
    return {
        "profiles": [p.to_dict() for p in ordered],
        "default_id": get_default_profile_id(),
    }


@router.post("", status_code=201)
async def create_text_profile(body: ProfileCreate):
    try:
        profile = create_profile(body.name, body.settings)
    except TextProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("TEXT PROFILES | created %s", profile.id)
    return profile.to_dict()


@router.put("/default/{profile_id}")
async def set_default_text_profile(profile_id: str):
    try:
        set_default_profile(profile_id)
    except TextProfileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.info("TEXT PROFILES | default -> %s", profile_id)
    return {"default_id": profile_id}


@router.put("/{profile_id}")
async def update_text_profile(profile_id: str, body: ProfileUpdate):
    try:
        profile = update_profile(profile_id, name=body.name, settings=body.settings)
    except TextProfileError as exc:
        status = 404 if "Unknown" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    logger.info("TEXT PROFILES | updated %s", profile.id)
    return profile.to_dict()


@router.delete("/{profile_id}")
async def delete_text_profile(profile_id: str):
    try:
        delete_profile(profile_id)
    except TextProfileError as exc:
        status = 404 if "Unknown" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    logger.info("TEXT PROFILES | deleted %s", profile_id)
    # Plain JSON body — the frontend client unconditionally parses JSON.
    return {"deleted": profile_id}
