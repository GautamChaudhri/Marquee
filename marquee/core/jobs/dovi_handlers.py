"""Durable job handlers for Dolby Vision analysis and remediation.

``dovi_analyze`` inspects one movie's file (ffprobe + dovi_tool) and upserts the
result into ``DoviState``. Batches fan out one child per DoVi movie via
``job_manager.create_batch`` (parent type ``dovi_analyze_batch`` — like
``letterbox_detect_batch``, the parent needs no handler; child progress
aggregates automatically). ``dovi_convert`` creates a non-destructive Profile
8.1 candidate for supported Profile 5 / Profile 7 files.

Registered by import side-effect; ``marquee.core.jobs.worker`` imports this
module so the decorator runs before the worker resolves any job.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from marquee.core.jobs import job_manager
from marquee.core.jobs.handlers import register
from marquee.database import _get_session_factory
from marquee.models import DoviState, Job, Movie

logger = logging.getLogger(__name__)


async def _upsert_dovi_state(
    db,
    movie_id: int,
    *,
    fields: dict[str, Any] | None = None,
    status: str | None = None,
    error_reason: str | None = None,
) -> DoviState:
    """Create-or-update the single DoviState row for a movie."""
    state = (
        await db.execute(select(DoviState).where(DoviState.movie_id == movie_id))
    ).scalar_one_or_none()
    if state is None:
        state = DoviState(movie_id=movie_id)
        db.add(state)
    if fields is not None:
        for key, value in fields.items():
            setattr(state, key, value)
    if status is not None:
        state.status = status
    if error_reason is not None:
        state.error_reason = error_reason
    state.last_analyzed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(state)
    return state


@register("dovi_analyze")
async def dovi_analyze(job: Job) -> dict[str, Any]:
    from marquee.core import dovi_analysis  # noqa: PLC0415
    from marquee.core.media_files import (  # noqa: PLC0415
        MediaFileNotFoundError,
        MediaFileUnavailableError,
        ensure_media_file_for_movie,
        resolve_media_file,
    )
    from marquee.media.binaries import BinaryError  # noqa: PLC0415

    factory = _get_session_factory()
    movie_id = int(job.payload["movie_id"])

    async with factory() as db:
        movie = await db.get(Movie, movie_id)
        if movie is None:
            raise RuntimeError("movie not found")

        # Emit child-start progress if part of a batch.
        if job.parent_id:
            parent = await db.get(Job, job.parent_id)
            if parent:
                try:
                    await job_manager.emit(
                        db,
                        parent,
                        state="child_progress",
                        message=f"Analyzing {movie.title}",
                        detail={
                            "movie_id": movie_id,
                            "title": movie.title,
                            "stage": "started",
                            "progress": 0,
                        },
                    )
                except Exception:  # noqa: BLE001 - progress must not fail analysis
                    logger.exception(
                        "could not emit dovi child-start progress for movie %d", movie.id
                    )

        media_file = await ensure_media_file_for_movie(db, movie)
        if media_file is None:
            await _upsert_dovi_state(db, movie_id, status="error", error_reason="no_media_file")
            return {"movie_id": movie_id, "status": "error", "skipped": True}

        try:
            resolved = await resolve_media_file(db, media_file.id)
        except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
            await _upsert_dovi_state(db, movie_id, status="error", error_reason=str(exc))
            return {"movie_id": movie_id, "status": "error", "skipped": True}

        try:
            analysis = await dovi_analysis.analyze_path(resolved.path)
        except BinaryError as exc:
            # Soft-fail probe timeouts so a slow file never poisons a batch.
            if "timed out" in str(exc).lower():
                logger.warning(
                    "dovi probe timeout for movie %d (%s) — marking errored", movie.id, movie.title
                )
                await _upsert_dovi_state(db, movie_id, status="error", error_reason="probe_timeout")
                return {"movie_id": movie_id, "status": "error", "skipped": True}
            raise

        state = await _upsert_dovi_state(db, movie_id, fields=analysis.to_state_fields())
        return {
            "movie_id": movie_id,
            "status": state.status,
            "profile": state.dovi_profile,
            "el_type": state.el_type,
        }


@register("dovi_convert")
async def dovi_convert(job: Job) -> dict[str, Any]:
    from marquee.core.dovi_conversion import (  # noqa: PLC0415
        DoviConversionError,
        execute_conversion,
    )
    from marquee.core.media_files import (  # noqa: PLC0415
        MediaFileNotFoundError,
        MediaFileUnavailableError,
        ensure_media_file_for_movie,
        resolve_media_file,
    )

    factory = _get_session_factory()
    movie_id = int(job.payload["movie_id"])
    kind = job.payload.get("kind")
    if kind not in {"p5_to_p81", "p7_strip_el"}:
        raise DoviConversionError("invalid_kind", "Unsupported Dolby Vision conversion kind.")

    async with factory() as db:
        current = await db.get(Job, job.id)
        if current is None:
            raise RuntimeError("job not found")
        movie = await db.get(Movie, movie_id)
        if movie is None:
            raise RuntimeError("movie not found")
        media_file = await ensure_media_file_for_movie(db, movie)
        if media_file is None:
            raise DoviConversionError("no_media_file", "No media file is available for this movie.")
        try:
            resolved = await resolve_media_file(db, media_file.id)
        except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
            raise DoviConversionError("file_unavailable", str(exc)) from exc
        result = await execute_conversion(
            db,
            current,
            resolved=resolved,
            media_file=media_file,
            kind=kind,  # type: ignore[arg-type]
        )
        result["movie_id"] = movie_id
        return result
