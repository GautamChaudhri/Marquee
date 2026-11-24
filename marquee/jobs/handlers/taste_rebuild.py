"""The ``taste_rebuild`` job: build the taste profile from the active exemplars and publish it.

The handler runs in the worker, as ``poster_run``'s does: it reads the exemplars, stages
their kept images into a build folder of its own, calls the GPU child (which runs the trainer
and never touches the database), checks the result, and publishes it
(:mod:`marquee.taste.rebuild`). Every step is safe to repeat: a profile an earlier attempt
built and kept is published without building again, and ``TASTE_PROFILE_PATH`` always holds a
complete profile, the old one or the new one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marquee.cancellation import JobCancelledError
from marquee.config import pipeline_settings, settings
from marquee.db.engine import create_engine, create_session_factory
from marquee.db.models import Job
from marquee.db.models.taste import PROFILE_SOURCE_REBUILD
from marquee.jobs import queue
from marquee.jobs.registry import JobContext, JobKind, PermanentJobError
from marquee.taste import exemplars, rebuild

logger = logging.getLogger(__name__)

CHILD_FUNCTION = "marquee.jobs.handlers.gpu:taste_rebuild"
_REVISION = re.compile(r"[0-9a-f]{64}")


def _payload(payload: dict[str, Any]) -> tuple[str, bool]:
    revision = payload.get("revision")
    force = payload.get("force", False)
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        raise PermanentJobError("the payload needs the exemplar revision to build")
    if not isinstance(force, bool):
        raise PermanentJobError("the payload's force must be true or false")
    return revision, force


def _remove(build_dir: Path) -> None:
    if build_dir.is_dir() and not build_dir.is_symlink():
        shutil.rmtree(build_dir, ignore_errors=True)


async def _publish(
    sessions: async_sessionmaker[AsyncSession],
    plan: rebuild.BuildPlan,
    sha256: str,
    job_id: int,
    build_dir: Path,
) -> dict[str, Any]:
    """Put the kept profile over ``TASTE_PROFILE_PATH``, then make its row the active one.

    File first: a crash in between leaves a complete profile in place whose row is not
    active yet, which the doctor reports and the job's next attempt finishes.
    """
    file_name = f"{sha256}{rebuild.PROFILE_SUFFIX}"
    destination = Path(pipeline_settings.TASTE_PROFILE_PATH)
    await asyncio.to_thread(rebuild.install, file_name, sha256, destination)
    async with sessions() as session:
        previous = await rebuild.active_profile(session)
        row = await rebuild.register(
            session,
            sha256=sha256,
            revision=plan.revision,
            source=PROFILE_SOURCE_REBUILD,
            positives=len(plan.positives),
            negatives=len(plan.negatives),
            movie_ids=plan.movie_ids,
            file_name=file_name,
            job_id=job_id,
        )
        keep = await rebuild.kept_names(session)
        await session.commit()
        profile_id, previous_id = row.id, previous.id if previous is not None else None
    await asyncio.to_thread(_remove, build_dir)
    pruned = await asyncio.to_thread(rebuild.prune, keep)
    logger.info(
        "TASTE | profile %d (%s) published: %d positives, %d negatives; %d old cop%s removed",
        profile_id,
        sha256[:12],
        len(plan.positives),
        len(plan.negatives),
        len(pruned),
        "y" if len(pruned) == 1 else "ies",
    )
    return {
        "profile_id": profile_id,
        "previous_profile_id": previous_id,
        "sha256": sha256,
        "positives": len(plan.positives),
        "negatives": len(plan.negatives),
        "pruned": pruned,
    }


async def taste_rebuild(ctx: JobContext) -> dict[str, Any]:
    revision, force = _payload(ctx.payload)
    started = time.perf_counter()
    build_dir = settings.taste_build_dir / str(ctx.job_id)
    engine = create_engine()
    sessions = create_session_factory(engine)
    try:
        async with sessions() as session:
            current = await exemplars.revision(session)
            active = await rebuild.active_profile(session)
            await session.commit()
        base: dict[str, Any] = {"revision": revision}
        marker = await asyncio.to_thread(rebuild.read_marker, build_dir)
        if revision != current:
            await asyncio.to_thread(_remove, build_dir)
            return {**base, "skipped": "superseded", "current_revision": current}
        if (
            active is not None
            and active.revision == current
            and (not force or (marker is not None and marker[1] == active.sha256))
        ):
            # Up to date, or an earlier attempt published this build and stopped before
            # cleaning up.
            await asyncio.to_thread(_remove, build_dir)
            return {
                **base,
                "skipped": "up to date",
                "profile_id": active.id,
                "sha256": active.sha256,
            }

        async with sessions() as session:
            try:
                plan = await rebuild.plan_build(session)
            except rebuild.RebuildRefusedError as exc:
                raise PermanentJobError(str(exc)) from exc
            await session.commit()
        sha256: str | None = None
        if marker is not None and marker[0] == plan.revision:
            with contextlib.suppress(rebuild.ProfileError, OSError):
                kept = rebuild.profile_copy(f"{marker[1]}{rebuild.PROFILE_SUFFIX}")
                await asyncio.to_thread(rebuild.verify_built, kept, plan)
                sha256 = marker[1]
                logger.info(
                    "TASTE | job %d: publishing the profile an earlier attempt built", ctx.job_id
                )
        built = sha256 is None
        if built:
            await asyncio.to_thread(rebuild.stage, plan, build_dir)
            positive_weights, negative_weights = plan.weights()
            try:
                await ctx.gpu(
                    CHILD_FUNCTION,
                    {
                        "build": build_dir.name,
                        "positive_weights": positive_weights,
                        "negative_weights": negative_weights,
                    },
                )
            except JobCancelledError:
                await asyncio.to_thread(_remove, build_dir)
                raise
            output = build_dir / rebuild.BUILT
            try:
                facts = await asyncio.to_thread(rebuild.verify_built, output, plan)
            except rebuild.ProfileError as exc:
                raise PermanentJobError(str(exc)) from exc
            await asyncio.to_thread(rebuild.keep_copy, output, facts.sha256)
            await asyncio.to_thread(rebuild.mark_built, build_dir, plan.revision, facts.sha256)
            sha256 = facts.sha256
        published = await _publish(sessions, plan, sha256, ctx.job_id, build_dir)
        return {
            **base,
            "skipped": None,
            "built": built,
            **published,
            "movies": len(plan.movie_ids),
            "seconds": round(time.perf_counter() - started, 1),
        }
    finally:
        await engine.dispose()


TASTE_REBUILD = JobKind(
    "taste_rebuild",
    "gpu",
    handler=taste_rebuild,
    max_attempts=2,
    timeout_s=15 * 60,
    dedupe_key="taste_rebuild:{revision}",
)


async def add_rebuild(
    session: AsyncSession, revision: str, *, force: bool = False, event_id: int | None = None
) -> tuple[Job, bool]:
    """Queue a rebuild of this exemplar revision in the caller's transaction (not committed).

    A rebuild of the same revision already queued or running is returned instead.
    """
    payload: dict[str, Any] = {"revision": revision, "force": force}
    if event_id is not None:
        payload["event_id"] = event_id
    return await queue.add_job(session, TASTE_REBUILD, payload)


async def enqueue_rebuild(session: AsyncSession, *, force: bool = False) -> tuple[Job, bool]:
    """Queue a rebuild of the current exemplar revision, and commit."""
    job, created = await add_rebuild(session, await exemplars.revision(session), force=force)
    await session.commit()
    return job, created
