"""Subtitle policy routes (design §25.3) — CRUD + audit (dry-run) + apply.

Audit is read-only: it evaluates the policy against a selection and returns
per-file removals/protections/warnings + coverage deltas. Apply turns a clean
audit into a batch of confirmed ``subtitle_remove`` jobs (conservative: skips
files needing review, hardlinked files unless allowed, etc.).
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.job_submission import JobSubmissionResponse, submission_response
from marquee.core.jobs.audio_subtitle_planning import (
    AudioSubtitlePlanError,
    load_before_inventory,
)
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.submission import (
    IdempotencyConflictError,
    Initiator,
    SubjectLocator,
    SubmissionError,
    submit_job,
)
from marquee.core.jobs.subtitle_parents import FrozenFilePlan, create_subtitle_policy_batch
from marquee.core.jobs.track_selectors import selector_for
from marquee.core.subtitles import policy as policy_service
from marquee.database import get_db
from marquee.models import MediaFile, SubtitlePolicy

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
    movie_ids: list[int] = Field(min_length=1, max_length=500)


class PolicyAuditBody(BaseModel):
    scope: Literal["all", "movies", "tv"] = "all"


@router.post("/{policy_id}/audit", status_code=202)
async def audit_policy(
    policy_id: int, body: PolicyAuditBody, db: Annotated[AsyncSession, Depends(get_db)]
) -> JobSubmissionResponse:
    """Submit a read-only policy audit with its policy content frozen at enqueue."""
    async with db.begin():
        policy = await _load_policy(db, policy_id)
        try:
            result = await submit_job(
                db,
                job_type="subtitle_policy_audit",
                request={
                    "policy_id": policy.id,
                    "policy_revision": policy.revision,
                    "policy_snapshot": _policy_snapshot(policy),
                    "scope": body.scope,
                },
                subject=SubjectLocator(
                    kind="maintenance_scope",
                    reference="subtitle-policy-audit",
                ),
                trigger=TriggerKind.MANUAL,
                initiator=Initiator(kind="system", identifier="subtitle-policy-audit-api"),
                idempotency_key=f"subtitle_policy_audit:manual-{uuid4().hex}",
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=exc.api_detail) from exc
        except SubmissionError as exc:
            raise HTTPException(status_code=422, detail=exc.code) from exc
    return submission_response(result)


@router.post("/{policy_id}/apply", status_code=202)
async def apply_policy(
    policy_id: int,
    body: SelectionBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=240)],
) -> JobSubmissionResponse:
    """Evaluate once, freeze one immutable plan per file, and seal the batch."""
    try:
        async with db.begin():
            policy = await db.scalar(
                select(SubtitlePolicy).where(SubtitlePolicy.id == policy_id).with_for_update()
            )
            if policy is None:
                raise HTTPException(status_code=404, detail=f"Policy {policy_id} not found")
            rows = list(
                (
                    await db.scalars(
                        select(MediaFile)
                        .where(MediaFile.movie_id.in_(set(body.movie_ids)), MediaFile.is_present.is_(True))
                        .order_by(MediaFile.id)
                        .limit(501)
                    )
                ).all()
            )
            if len(rows) > 500:
                raise HTTPException(status_code=422, detail="Policy selection exceeds 500 files")
            plans: list[FrozenFilePlan] = []
            policy_document = _policy_snapshot(policy)
            for media_file in rows:
                _resolved, typed_inventory = await load_before_inventory(db, media_file.id)
                subtitle_entries = [
                    entry for entry in typed_inventory.entries if entry.facts.kind == "subtitle"
                ]
                audio_entries = [
                    entry for entry in typed_inventory.entries if entry.facts.kind == "audio"
                ]
                policy_tracks = [
                    {
                        "id": entry.track_key,
                        **entry.facts.model_dump(mode="json"),
                        "is_sdh": entry.facts.is_hearing_impaired,
                    }
                    for entry in subtitle_entries
                ]
                policy_audio = [
                    {"id": entry.track_key, **entry.facts.model_dump(mode="json")}
                    for entry in audio_entries
                ]
                evaluation = policy_service.evaluate_policy(
                    policy_tracks,
                    policy_audio,
                    policy_document,
                )
                if evaluation.has_review:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "policy_review_required",
                            "message": "The evaluated policy requires review before mutation.",
                        },
                    )
                selectors = []
                for track_id in evaluation.removals:
                    track = next(
                        (entry for entry in subtitle_entries if entry.track_key == track_id),
                        None,
                    )
                    if track is None or track.facts.source != "embedded":
                        raise HTTPException(
                            status_code=422,
                            detail="Policy selected an external or stale track that cannot be remuxed.",
                        )
                    selectors.append(selector_for(track, typed_inventory))
                plans.append(
                    FrozenFilePlan(
                        media_file_id=media_file.id,
                        remove_selectors=tuple(selectors),
                    )
                )
            result = await create_subtitle_policy_batch(
                db,
                policy_id=policy.id,
                policy_revision=policy.revision,
                plans=plans,
                idempotency_key=idempotency_key,
                initiator=Initiator(kind="user", identifier="subtitle-policy-api"),
                scope="selected",
            )
        return submission_response(result.parent)
    except AudioSubtitlePlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.api_detail) from exc
    except SubmissionError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
