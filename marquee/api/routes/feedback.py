"""Feedback routes — approve / override / reject, plus undo (design 09).

Every submission maps to the scenarios in design 09 §3–6:

  - approve     → 1 positive label for the auto-pick (scenario A)
  - override    → negative for the auto-pick + positive for the user's pick
                  (scenarios B/C); retro features + gate snapshot if the pick
                  was rejected before its features were computed
  - reject_all  → 1 ``explicit_reject`` negative for the auto-pick (scenario D)

A single ``event_id`` ties together all rows a submission writes, so undo is
exact. Approve/override append the chosen poster to the taste profile and
(when thresholds are met) retrain the learned head.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.pipeline_config import pipeline_settings
from marquee.database import get_db
from marquee.ml import feedback_store, profile_updater
from marquee.models import Movie, PipelineRun
from marquee.pipeline.features import load_cached_embedding
from marquee.pipeline.run_manager import run_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    run_id: str
    action: str  # approve | override | reject_all | rank
    selected_filename: str | None = None
    # action="rank": ordered favorite tiers (ties share a sublist) + hated set.
    favorites: list[list[str]] | None = None
    hated: list[str] | None = None
    deploy: bool | None = None  # defaults to FEEDBACK_DEPLOY_DEFAULT


class UndoRequest(BaseModel):
    event_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _label_record(
    *,
    candidate: dict,
    label: int,
    action: str,
    role: str,
    event_id: str,
    ts: str,
    run: PipelineRun,
    movie: Movie | None,
    raw: dict | None,
    normalized: dict | None,
    extended: dict | None,
    exemplar_filename: str | None = None,
) -> dict:
    return {
        "v": 2,
        "event_id": event_id,
        "ts": ts,
        "run_id": run.run_id,
        "movie_id": run.movie_id,
        "tmdb_id": movie.tmdb_id if movie else None,
        "title": movie.title if movie else None,
        "year": movie.year if movie else None,
        "orig_filename": candidate["orig_filename"],
        "label": label,
        "action": action,
        "role": role,
        "rank": candidate.get("rank"),
        "stage_reached": candidate.get("stage_reached"),
        "rejection_reason": candidate.get("rejection_reason"),
        "scorer_name": run.scorer_name,
        "model_name": pipeline_settings.AI_MODEL,
        "gate_snapshot": feedback_store.gate_snapshot(),
        "raw_features": raw,
        "normalized_features": normalized,
        "extended_features": extended,
        "exemplar_filename": exemplar_filename,
    }


def _archive_features(candidate: dict) -> tuple[dict | None, dict | None, dict | None]:
    return (
        candidate.get("raw_features"),
        candidate.get("normalized_features"),
        candidate.get("extended_features"),
    )


def _ranking_record(
    *,
    event_id: str,
    ts: str,
    run: PipelineRun,
    movie: Movie | None,
    favorites: list[list[str]],
    hated: list[str],
    candidates: list[dict],
    favorites_exemplars: list[str],
    negatives_added: list[str],
) -> dict:
    """One self-contained v3 ranking event (see design 19). Stores the raw
    partial order + every paired candidate's normalized features, so the
    pairwise trainer never depends on a run's working dir surviving."""
    return {
        "v": 3,
        "type": "ranking",
        "event_id": event_id,
        "ts": ts,
        "run_id": run.run_id,
        "movie_id": run.movie_id,
        "tmdb_id": movie.tmdb_id if movie else None,
        "title": movie.title if movie else None,
        "year": movie.year if movie else None,
        "favorites": favorites,
        "hated": hated,
        "favorites_exemplars": favorites_exemplars,
        "negatives_added": negatives_added,
        "candidates": candidates,
        "scorer_name": run.scorer_name,
        "model_name": pipeline_settings.AI_MODEL,
        "gate_snapshot": feedback_store.gate_snapshot(),
    }


def _resolve_pick(by_name: dict, name: str) -> dict | None:
    """Resolve a filename to its candidate dict, remapping a dedup-twin onto
    the survivor it collapsed into (mirrors the override remap)."""
    candidate = by_name.get(name)
    if candidate is None:
        return None
    if (candidate.get("rejection_reason") or "").startswith("dedup_") and candidate.get(
        "dedup_kept"
    ):
        survivor = by_name.get(candidate["dedup_kept"])
        if survivor is not None:
            return survivor
    return candidate


async def _normalized_for(
    request: Request, run: PipelineRun, candidate: dict, movie: Movie | None
) -> dict | None:
    """Normalized features for a candidate, backfilled via retro features when
    it was rejected before the style stage (same as the override path)."""
    _, normalized, _ = _archive_features(candidate)
    if normalized is None:
        _, normalized, _ = await _retro_features(
            request=request, run=run, candidate=candidate, movie=movie
        )
    return normalized


async def _retro_features(
    *,
    request: Request,
    run: PipelineRun,
    candidate: dict,
    movie: Movie | None,
) -> tuple[dict | None, dict | None, dict | None]:
    """Backfill features for a pick that was rejected before detail features."""
    orig = candidate["orig_filename"]
    originals = Path(run.output_dir or "") / "0-originals" / orig
    if not originals.is_file():
        logger.warning("RETRO | no source image for %s — label written without features", orig)
        return None, None, None

    from marquee.core.poster_sources.tmdb import PosterCandidate  # noqa: PLC0415
    from marquee.pipeline.retro_features import (  # noqa: PLC0415
        candidate_from_image,
        compute_full_features,
    )

    poster_candidate: PosterCandidate | None = None
    tmdb = getattr(request.app.state, "tmdb_client", None)
    if tmdb is not None and movie is not None and movie.tmdb_id is not None:
        try:
            images = await tmdb.get_movie_images(movie.tmdb_id)
            poster_candidate = next(
                (c for c in images if c.file_path.lstrip("/").split("/")[-1] == orig),
                None,
            )
        except Exception as exc:  # noqa: BLE001 — fall back to image-only metadata
            logger.warning("RETRO | TMDB lookup failed (%s) — using image dims", exc)
    if poster_candidate is None:
        poster_candidate = candidate_from_image(originals)

    title = movie.title if movie else ""

    def _compute():
        extractor = run_manager._ensure_extractor()
        return compute_full_features(
            image_path=originals,
            candidate=poster_candidate,
            movie_title=title,
            extractor=extractor,
        )

    features = await asyncio.to_thread(_compute)
    return features.raw_values(), features.normalized, features.extended


def _add_to_profile(orig_filename: str, image_path: Path, movie: Movie | None) -> str | None:
    """Append the pick to the taste profile; returns the exemplar filename."""
    embedding = load_cached_embedding(orig_filename)
    if embedding is None:
        logger.warning(
            "PROFILE | no cached embedding for %s — skipping exemplar add", orig_filename
        )
        return None
    source = image_path if image_path.is_file() else None
    if source is None:
        logger.warning("PROFILE | no source image for %s — skipping exemplar add", orig_filename)
        return None
    return profile_updater.add_exemplar(
        source_image=source,
        title=movie.title if movie else orig_filename,
        year=movie.year if movie else None,
        clip_embedding=embedding,
    )


def _maybe_retrain_head() -> dict:
    if not pipeline_settings.HEAD_AUTO_RETRAIN:
        return {"retrained": False, "reason": "auto-retrain disabled"}
    from marquee.ml.head_trainer import train_from_labels  # noqa: PLC0415

    head, info = train_from_labels()
    return {"retrained": head is not None, "reason": info.get("reason"), **info}


async def _deploy_pick(
    db: AsyncSession, movie: Movie, pick: dict, run: PipelineRun
) -> tuple[str | None, str | None]:
    """Deploy the user's chosen poster to the media folder. Best-effort:
    a deploy failure must not lose the label/profile work already written."""
    from marquee.core.poster_service import poster_service, tmdb_original_url  # noqa: PLC0415

    # Prefer the recorded (full-res for ranked picks) image; fall back to w500.
    source = Path(pick.get("image_path") or "")
    if not source.is_file():
        source = Path(run.output_dir or "") / "0-originals" / pick["orig_filename"]
    if not source.is_file():
        return None, f"source image not found for {pick['orig_filename']}"

    try:
        result = await poster_service.deploy(
            db,
            movie,
            source,
            source="feedback",
            ai_selected=True,
            user_approved=True,
            poster_source_url=tmdb_original_url(pick["orig_filename"]),
        )
        return result.deployed_path, None
    except Exception as exc:  # noqa: BLE001 — surfaced, not fatal to the feedback
        logger.warning("DEPLOY | feedback deploy failed for %s: %s", movie.title, exc)
        return None, str(exc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("")
async def submit_feedback(
    body: FeedbackRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    run = (
        await db.execute(select(PipelineRun).where(PipelineRun.run_id == body.run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {body.run_id} not found")

    archive = run_manager.load_archive(body.run_id, run.archive_path)
    if archive is None:
        raise HTTPException(status_code=404, detail="Run archive unavailable")

    movie = (await db.execute(select(Movie).where(Movie.id == run.movie_id))).scalar_one_or_none()

    by_name = {c["orig_filename"]: c for c in archive.get("candidates", [])}
    auto = next((c for c in archive.get("candidates", []) if c.get("rank") == 1), None)

    event_id = uuid4().hex
    ts = datetime.now(UTC).isoformat()
    records: list[dict] = []
    exemplar_added: str | None = None
    remapped_to: str | None = None
    pick: dict | None = None
    favorites_exemplars: list[str] = []
    negatives_added: list[str] = []

    if body.action == "reject_all":
        if auto is None:
            raise HTTPException(status_code=400, detail="No auto-pick to reject")
        raw, norm, ext = _archive_features(auto)
        records.append(
            _label_record(
                candidate=auto,
                label=0,
                action="reject_all",
                role="explicit_reject",
                event_id=event_id,
                ts=ts,
                run=run,
                movie=movie,
                raw=raw,
                normalized=norm,
                extended=ext,
            )
        )

    elif body.action in ("approve", "override"):
        if body.action == "approve":
            pick = auto
        else:
            if not body.selected_filename:
                raise HTTPException(status_code=400, detail="override requires selected_filename")
            pick = by_name.get(body.selected_filename)
            if pick is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"{body.selected_filename!r} is not a candidate of this run",
                )
            # Remap a dedup-twin pick onto the survivor it collapsed into.
            if (pick.get("rejection_reason") or "").startswith("dedup_") and pick.get("dedup_kept"):
                survivor = by_name.get(pick["dedup_kept"])
                if survivor is not None:
                    remapped_to = pick["dedup_kept"]
                    pick = survivor

        if pick is None:
            raise HTTPException(status_code=400, detail="No auto-pick available to approve")

        # Override = pairwise: negative for the auto-pick the user passed over.
        if (
            body.action == "override"
            and auto is not None
            and auto["orig_filename"] != pick["orig_filename"]
        ):
            raw, norm, ext = _archive_features(auto)
            records.append(
                _label_record(
                    candidate=auto,
                    label=0,
                    action="override",
                    role="auto_pick",
                    event_id=event_id,
                    ts=ts,
                    run=run,
                    movie=movie,
                    raw=raw,
                    normalized=norm,
                    extended=ext,
                )
            )

        # Positive for the pick — backfill features if it was rejected early.
        raw, norm, ext = _archive_features(pick)
        if norm is None:
            raw, norm, ext = await _retro_features(
                request=request, run=run, candidate=pick, movie=movie
            )

        # Add to the taste profile (source = the recorded image, original-res
        # for ranked picks).
        image_path = Path(pick.get("image_path") or "")
        exemplar_added = await asyncio.to_thread(
            _add_to_profile, pick["orig_filename"], image_path, movie
        )

        records.append(
            _label_record(
                candidate=pick,
                label=1,
                action=body.action,
                role="user_pick",
                event_id=event_id,
                ts=ts,
                run=run,
                movie=movie,
                raw=raw,
                normalized=norm,
                extended=ext,
                exemplar_filename=exemplar_added,
            )
        )

        # Optional: the overridden auto-pick joins the negative exemplars.
        if (
            body.action == "override"
            and pipeline_settings.FEEDBACK_NEGATIVES_FROM_OVERRIDES
            and auto is not None
        ):
            _copy_negative(auto)

    elif body.action == "rank":
        favorites_in = [tier for tier in (body.favorites or []) if tier]
        hated_in = body.hated or []
        if not favorites_in and not hated_in:
            raise HTTPException(status_code=400, detail="rank requires favorites and/or hated")

        def _resolve_all(names: list[str]) -> list[dict]:
            resolved = []
            for name in names:
                candidate = _resolve_pick(by_name, name)
                if candidate is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"{name!r} is not a candidate of this run",
                    )
                resolved.append(candidate)
            return resolved

        fav_tiers = [_resolve_all(tier) for tier in favorites_in]
        hated_cands = _resolve_all(hated_in)
        chosen = {c["orig_filename"] for tier in fav_tiers for c in tier}
        chosen |= {c["orig_filename"] for c in hated_cands}

        # Embed every paired candidate with its (backfilled) normalized features.
        embedded: list[dict] = []
        seen: set[str] = set()

        async def _emit(candidate: dict, bucket: str, tier: int | None) -> None:
            name = candidate["orig_filename"]
            if name in seen:
                return
            normalized = await _normalized_for(request, run, candidate, movie)
            if not normalized:
                return  # nothing trainable — skip (still added to profile below)
            seen.add(name)
            embedded.append(
                {
                    "orig_filename": name,
                    "bucket": bucket,
                    "tier": tier,
                    "pipeline_rank": candidate.get("rank"),
                    "rejection_reason": candidate.get("rejection_reason"),
                    "normalized_features": normalized,
                }
            )

        for tier_index, tier in enumerate(fav_tiers, start=1):
            for candidate in tier:
                await _emit(candidate, "fav", tier_index)
        for candidate in hated_cands:
            await _emit(candidate, "hate", None)
        # Indifferent = ranked survivors the user left untouched.
        for candidate in archive.get("candidates", []):
            if candidate.get("rank") is not None and candidate["orig_filename"] not in chosen:
                await _emit(candidate, "indiff", None)

        # Channel 1 (positive exemplars): tier-1 favorites.
        if fav_tiers:
            for candidate in fav_tiers[0]:
                added = await asyncio.to_thread(
                    _add_to_profile,
                    candidate["orig_filename"],
                    Path(candidate.get("image_path") or ""),
                    movie,
                )
                if added:
                    favorites_exemplars.append(added)

        # Channel 1 (hard negatives): hated posters the pipeline ranked high.
        rank_max = pipeline_settings.FEEDBACK_HARD_NEGATIVE_RANK_MAX
        for candidate in hated_cands:
            rank = candidate.get("rank")
            if rank is not None and rank <= rank_max:
                added = _copy_negative(candidate)
                if added:
                    negatives_added.append(added)

        records.append(
            _ranking_record(
                event_id=event_id,
                ts=ts,
                run=run,
                movie=movie,
                favorites=[[c["orig_filename"] for c in tier] for tier in fav_tiers],
                hated=[c["orig_filename"] for c in hated_cands],
                candidates=embedded,
                favorites_exemplars=favorites_exemplars,
                negatives_added=negatives_added,
            )
        )
        exemplar_added = favorites_exemplars[0] if favorites_exemplars else None
        pick = fav_tiers[0][0] if fav_tiers else None

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action {body.action!r}")

    feedback_store.append_labels(records)

    # Profile changed → next pipeline run must reload the taste store.
    if exemplar_added is not None:
        run_manager.reset_extractor()

    head_info = await asyncio.to_thread(_maybe_retrain_head)

    # Deploy the chosen poster to the media folder (approve/override only).
    deployed_to = None
    deploy_error = None
    deploy = pipeline_settings.FEEDBACK_DEPLOY_DEFAULT if body.deploy is None else body.deploy
    if deploy and pick is not None and movie is not None:
        deployed_to, deploy_error = await _deploy_pick(db, movie, pick, run)

    # Mark the run reviewed.
    run.feedback_event_id = event_id
    await db.commit()

    gate_override = _gate_override_for(pick) if pick is not None else None

    return {
        "event_id": event_id,
        "labels_written": len(records),
        "exemplar_added": exemplar_added,
        "favorites_exemplars": favorites_exemplars,
        "negatives_added": negatives_added,
        "remapped_to": remapped_to,
        "gate_override": gate_override,
        "head": head_info,
        "deployed_to": deployed_to,
        "deploy_error": deploy_error,
    }


@router.post("/undo")
async def undo_feedback(
    body: UndoRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    removed = feedback_store.remove_event(body.event_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No labels for event {body.event_id}")

    removed_exemplars: list[str] = []
    removed_negatives: list[str] = []
    for row in removed:
        # v2 approve/override carry one exemplar; v3 ranking events carry lists
        # of added positive exemplars + hard-negative files.
        exemplar_names = [row.get("exemplar_filename"), *(row.get("favorites_exemplars") or [])]
        for exemplar in exemplar_names:
            if exemplar and await asyncio.to_thread(profile_updater.remove_exemplar, exemplar):
                removed_exemplars.append(exemplar)
        for negative in row.get("negatives_added") or []:
            if _remove_negative(negative):
                removed_negatives.append(negative)

    if removed_exemplars:
        run_manager.reset_extractor()

    # Clear the reviewed marker on any run that pointed at this event.
    runs = (
        (
            await db.execute(
                select(PipelineRun).where(PipelineRun.feedback_event_id == body.event_id)
            )
        )
        .scalars()
        .all()
    )
    for run in runs:
        run.feedback_event_id = None
    await db.commit()

    head_info = await asyncio.to_thread(_maybe_retrain_head)
    return {
        "removed_labels": len(removed),
        "exemplars_removed": removed_exemplars,
        "negatives_removed": removed_negatives,
        "head": head_info,
    }


def _gate_override_for(pick: dict) -> dict | None:
    reason = (pick.get("rejection_reason") or "").split(":", 1)[0]
    if reason not in feedback_store.GATE_REASON_KNOBS:
        return None
    alerts = {a["gate"]: a for a in feedback_store.gate_override_alerts()}
    alert = alerts.get(reason)
    return {
        "reason": reason,
        "count_at_current_threshold": alert["overrides"] if alert else 1,
    }


def _copy_negative(candidate: dict) -> str | None:
    """Copy a disliked poster into NEGATIVE_DATA_DIR. Returns the filename
    added (for undo), or None if the source was missing or already present."""
    import shutil  # noqa: PLC0415

    source = Path(candidate.get("image_path") or "")
    if not source.is_file():
        return None
    dest_dir = Path(pipeline_settings.NEGATIVE_DATA_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name
    if dest.exists():
        return None
    shutil.copy2(source, dest)
    logger.info("NEGATIVE | added %s to negative exemplars", source.name)
    return dest.name


def _remove_negative(filename: str) -> bool:
    """Remove a negative exemplar file added by a ranking event (undo)."""
    target = Path(pipeline_settings.NEGATIVE_DATA_DIR) / filename
    if target.is_file():
        target.unlink()
        logger.info("NEGATIVE | removed %s", filename)
        return True
    return False
