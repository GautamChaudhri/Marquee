"""Subtitle policy routes (design §25.3) — CRUD + audit (dry-run) + apply.

Audit is read-only: it evaluates the policy against a selection and returns
per-file removals/protections/warnings + coverage deltas. Apply turns a clean
audit into a batch of confirmed ``subtitle_remove`` jobs (conservative: skips
files needing review, hardlinked files unless allowed, etc.).
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.manager import UnmigratedJobPlatformError
from marquee.core.media_files import ensure_media_file_for_movie
from marquee.core.subtitles import service
from marquee.core.subtitles.policy import evaluate_policy
from marquee.database import get_db
from marquee.models import Movie, SubtitlePolicy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/subtitle-policies", tags=["subtitle-policies"])


class PolicyBody(BaseModel):
    name: str
    mode: str = "blocklist"  # allowlist | blocklist
    languages: list[str] = []
    enabled: bool = False
    unknown_action: str = "keep"
    protect_forced: bool = True
    protect_default: bool = True
    protect_last_full_dialogue: bool = True
    include_external: bool = False
    auto_apply: bool = False
    audit_only: bool = True
    hardlink_action: str = "block"
    backup_mode: str = "none"


def _policy_dict(p: SubtitlePolicy) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "enabled": p.enabled,
        "revision": p.revision,
        "mode": p.mode,
        "languages": json.loads(p.languages_json) if p.languages_json else [],
        "unknown_action": p.unknown_action,
        "protect_forced": p.protect_forced,
        "protect_default": p.protect_default,
        "protect_last_full_dialogue": p.protect_last_full_dialogue,
        "include_external": p.include_external,
        "auto_apply": p.auto_apply,
        "audit_only": p.audit_only,
        "hardlink_action": p.hardlink_action,
        "backup_mode": p.backup_mode,
    }


def _policy_snapshot(p: SubtitlePolicy) -> dict:
    return {
        "mode": p.mode,
        "languages": json.loads(p.languages_json) if p.languages_json else [],
        "unknown_action": p.unknown_action,
        "protect_forced": p.protect_forced,
        "protect_default": p.protect_default,
        "protect_last_full_dialogue": p.protect_last_full_dialogue,
        "include_external": p.include_external,
    }


@router.get("")
async def list_policies(db: Annotated[AsyncSession, Depends(get_db)]):
    rows = (await db.execute(select(SubtitlePolicy))).scalars().all()
    return {"policies": [_policy_dict(p) for p in rows]}


@router.post("", status_code=201)
async def create_policy(body: PolicyBody, db: Annotated[AsyncSession, Depends(get_db)]):
    policy = SubtitlePolicy(
        name=body.name,
        mode=body.mode,
        languages_json=json.dumps(body.languages),
        enabled=body.enabled,
        unknown_action=body.unknown_action,
        protect_forced=body.protect_forced,
        protect_default=body.protect_default,
        protect_last_full_dialogue=body.protect_last_full_dialogue,
        include_external=body.include_external,
        auto_apply=body.auto_apply,
        audit_only=body.audit_only,
        hardlink_action=body.hardlink_action,
        backup_mode=body.backup_mode,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return _policy_dict(policy)


async def _load_policy(db: AsyncSession, policy_id: int) -> SubtitlePolicy:
    policy = await db.get(SubtitlePolicy, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Policy {policy_id} not found")
    return policy


@router.get("/{policy_id}")
async def get_policy(policy_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    return _policy_dict(await _load_policy(db, policy_id))


@router.put("/{policy_id}")
async def update_policy(
    policy_id: int, body: PolicyBody, db: Annotated[AsyncSession, Depends(get_db)]
):
    policy = await _load_policy(db, policy_id)
    policy.name = body.name
    policy.mode = body.mode
    policy.languages_json = json.dumps(body.languages)
    policy.enabled = body.enabled
    policy.unknown_action = body.unknown_action
    policy.protect_forced = body.protect_forced
    policy.protect_default = body.protect_default
    policy.protect_last_full_dialogue = body.protect_last_full_dialogue
    policy.include_external = body.include_external
    policy.auto_apply = body.auto_apply
    policy.audit_only = body.audit_only
    policy.hardlink_action = body.hardlink_action
    policy.backup_mode = body.backup_mode
    policy.revision += 1  # invalidates any in-flight plans built on the old rules
    await db.commit()
    return _policy_dict(policy)


@router.delete("/{policy_id}")
async def delete_policy(policy_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    policy = await _load_policy(db, policy_id)
    await db.delete(policy)
    await db.commit()
    return {"deleted": policy_id}


class SelectionBody(BaseModel):
    movie_ids: list[int] = []


@router.post("/{policy_id}/audit")
async def audit_policy(
    policy_id: int, body: SelectionBody, db: Annotated[AsyncSession, Depends(get_db)]
):
    """Dry-run the policy across a movie selection (read-only)."""
    policy = await _load_policy(db, policy_id)
    snapshot = _policy_snapshot(policy)
    items = []
    total_removals = 0
    for movie_id in body.movie_ids:
        movie = (await db.execute(select(Movie).where(Movie.id == movie_id))).scalar_one_or_none()
        if movie is None:
            continue
        media_file = await ensure_media_file_for_movie(db, movie)
        if media_file is None:
            items.append({"movie_id": movie_id, "skipped": "no_media_file"})
            continue
        try:
            inventory = await service.get_inventory_dict(db, media_file.id)
        except Exception as exc:  # noqa: BLE001
            items.append({"movie_id": movie_id, "skipped": f"unavailable: {exc}"})
            continue
        ev = evaluate_policy(inventory["tracks"], inventory.get("audio_streams", []), snapshot)
        total_removals += len(ev.removals)
        items.append(
            {
                "movie_id": movie_id,
                "media_file_id": media_file.id,
                "removals": ev.removals,
                "protected": len(ev.protected),
                "review_required": ev.review_required,
                "warnings": ev.warnings,
                "coverage_before": ev.coverage_before,
                "coverage_after": ev.coverage_after,
            }
        )
    return {"policy_id": policy_id, "total_removals": total_removals, "items": items}


@router.post("/{policy_id}/apply", status_code=202)
async def apply_policy(
    policy_id: int, body: SelectionBody, db: Annotated[AsyncSession, Depends(get_db)]
):
    """Fail closed until subtitle-policy mutations have a canonical definition."""
    raise UnmigratedJobPlatformError(f"subtitle_policy.apply:{policy_id}")
