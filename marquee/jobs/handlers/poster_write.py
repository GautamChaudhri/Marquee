"""The ``poster_deploy`` and ``poster_restore`` jobs: writes to a movie folder.

Both are class ``media_write`` (one at a time) and share one dedupe key per movie, so a
movie never has two writes queued or running. Both go through
:func:`marquee.artwork.deploy.write_poster`. Payloads carry the movie's id for that key;
the handlers check it against the database.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.artwork import backup, deploy
from marquee.config import settings
from marquee.db.engine import create_engine, create_session_factory
from marquee.db.models import (
    ARTWORK_POSTER,
    ORIGIN_LIBRARY,
    ORIGIN_MARQUEE,
    Artwork,
    Deployment,
    Job,
    Movie,
    PosterRun,
    RunCandidate,
)
from marquee.db.models.deployment import INITIATED_BY_OWNER, INITIATED_BY_POLICY, INITIATORS
from marquee.db.models.run import RUN_COMPLETED
from marquee.fs.paths import PathMappingError
from marquee.jobs import queue
from marquee.jobs.registry import JobContext, JobKind, PermanentJobError

WRITE_KEY = "poster_write:{movie_id}"


class WriteRefusedError(ValueError):
    """A deploy or restore cannot be queued; the message says why."""


def _int(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PermanentJobError(f"the payload needs {name}")
    return value


# ── What may be deployed ──────────────────────────────────────────────────


async def _candidate(
    session: AsyncSession, candidate_id: int
) -> tuple[RunCandidate, PosterRun, Movie]:
    candidate = await session.get(RunCandidate, candidate_id)
    if candidate is None:
        raise WriteRefusedError(f"no candidate {candidate_id}")
    run = await session.get(PosterRun, candidate.run_id)
    assert run is not None
    movie = await session.get(Movie, run.movie_id)
    assert movie is not None
    if run.state != RUN_COMPLETED:
        raise WriteRefusedError(f"run {run.id} is {run.state}; only a completed run's candidate")
    if candidate.sha256 is None:
        raise WriteRefusedError(
            f"candidate {candidate_id} ({candidate.source_file}) was never downloaded"
        )
    if not movie.is_present or not movie.has_file:
        raise WriteRefusedError(f"movie {movie.id} ({movie.title}) has no folder to write to")
    return candidate, run, movie


def missing_guard(current: deploy.OnDisk | None, row: Artwork | None) -> str | None:
    """``DEPLOY_POLICY=missing``, checked on the folder itself just before the write."""
    if current is None:
        return None
    if row is not None and row.origin == ORIGIN_MARQUEE and row.sha256 == current.sha256:
        return None
    return "DEPLOY_POLICY is missing and the folder holds a poster that is not Marquee's"


def _staged_path(job_id: int) -> Path:
    return settings.deploy_staging_dir / f"{job_id}.jpg"


def _keep_staged(job_id: int, data: bytes) -> None:
    folder = settings.deploy_staging_dir
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f".{job_id}.part"
    temporary.write_bytes(data)
    os.replace(temporary, _staged_path(job_id))


def _staged(job_id: int) -> bytes | None:
    path = _staged_path(job_id)
    if not path.is_file():
        return None
    data = path.read_bytes()
    try:
        deploy.validate_jpeg(data)
    except deploy.NotAJpegError:
        path.unlink(missing_ok=True)
        return None
    return data


async def _original(job_id: int, source_file: str) -> bytes:
    """The bytes this job deploys: fetched once, then the same bytes on every attempt."""
    data = await asyncio.to_thread(_staged, job_id)
    if data is None:
        data = await deploy.fetch_original(source_file)
        await asyncio.to_thread(_keep_staged, job_id, data)
    return data


async def poster_deploy(ctx: JobContext) -> dict[str, Any]:
    candidate_id = _int(ctx.payload, "candidate_id")
    movie_id = _int(ctx.payload, "movie_id")
    initiated_by = ctx.payload.get("initiated_by")
    if initiated_by not in INITIATORS:
        raise PermanentJobError(f"the payload's initiated_by must be one of {INITIATORS}")
    engine = create_engine()
    sessions = create_session_factory(engine)
    try:
        async with sessions() as session:
            try:
                candidate, run, movie = await _candidate(session, candidate_id)
            except WriteRefusedError as exc:
                raise PermanentJobError(str(exc)) from exc
            await session.commit()
        if movie.id != movie_id:
            raise PermanentJobError(f"candidate {candidate_id} is not movie {movie_id}'s")
        guard = None
        if initiated_by == INITIATED_BY_POLICY:
            if not candidate.is_pick:
                raise PermanentJobError("a deploy the policy queued accepts only the run's pick")
            if settings.DEPLOY_POLICY == "off":
                return {"skipped": True, "note": "DEPLOY_POLICY is off now", "movie_id": movie.id}
            guard = missing_guard if settings.DEPLOY_POLICY == "missing" else None
        base = {"movie_id": movie.id, "run_id": run.id, "candidate_id": candidate.id}
        try:
            done = await deploy.already_deployed(sessions, movie, candidate.id)
            if done is not None:
                result = deploy.recorded(
                    done, "already_deployed", "the poster was already deployed"
                )
                return {**base, "skipped": False, **result.to_dict()}
            data = await _original(ctx.job_id, candidate.source_file)
            result = await deploy.write_poster(
                sessions,
                deploy.Write(
                    movie=movie,
                    new=data,
                    new_origin=ORIGIN_MARQUEE,
                    action="deploy",
                    initiated_by=initiated_by,
                    job_id=ctx.job_id,
                    run_id=run.id,
                    candidate_id=candidate.id,
                    source_file=candidate.source_file,
                ),
                guard=guard,
            )
        except (deploy.NotAJpegError, deploy.RefusedTargetError, PathMappingError) as exc:
            # No retry can change these; a read-back mismatch or an I/O error still retries.
            raise PermanentJobError(str(exc)) from exc
        with contextlib.suppress(OSError):
            _staged_path(ctx.job_id).unlink(missing_ok=True)
        return {**base, "skipped": result.outcome == "skipped", **result.to_dict()}
    finally:
        await engine.dispose()


async def poster_restore(ctx: JobContext) -> dict[str, Any]:
    deployment_id = _int(ctx.payload, "deployment_id")
    movie_id = _int(ctx.payload, "movie_id")
    engine = create_engine()
    sessions = create_session_factory(engine)
    try:
        async with sessions() as session:
            undone = await session.get(Deployment, deployment_id)
            if undone is None:
                raise PermanentJobError(f"no deployment {deployment_id}")
            movie = await session.get(Movie, undone.movie_id)
            assert movie is not None
            own = await session.scalar(select(Deployment).where(Deployment.job_id == ctx.job_id))
            await session.commit()
        if movie.id != movie_id:
            raise PermanentJobError(f"deployment {deployment_id} is not movie {movie_id}'s")
        base = {"movie_id": movie.id, "restores_deployment_id": deployment_id}
        if own is not None:
            result = deploy.recorded(own, "already_recorded", "this job already made its write")
            return {**base, **result.to_dict()}
        data: bytes | None = None
        if undone.previous_sha256 is not None:
            if undone.backup_file is None:
                raise PermanentJobError(f"deployment {deployment_id} recorded no backup")
            data = await asyncio.to_thread(
                backup.verify, undone.backup_file, undone.previous_sha256
            )
        result = await deploy.write_poster(
            sessions,
            deploy.Write(
                movie=movie,
                new=data,
                new_origin=undone.previous_origin or ORIGIN_LIBRARY,
                action="restore",
                initiated_by=INITIATED_BY_OWNER,
                job_id=ctx.job_id,
                restores_deployment_id=deployment_id,
            ),
        )
        return {**base, **result.to_dict()}
    except (deploy.RefusedTargetError, PathMappingError) as exc:
        raise PermanentJobError(str(exc)) from exc
    finally:
        await engine.dispose()


POSTER_DEPLOY = JobKind(
    "poster_deploy",
    "media_write",
    handler=poster_deploy,
    max_attempts=3,
    timeout_s=5 * 60,
    dedupe_key=WRITE_KEY,
)
POSTER_RESTORE = JobKind(
    "poster_restore",
    "media_write",
    handler=poster_restore,
    max_attempts=3,
    timeout_s=5 * 60,
    dedupe_key=WRITE_KEY,
)


# ── Queueing ──────────────────────────────────────────────────────────────


async def add_deploy(
    session: AsyncSession,
    candidate_id: int,
    *,
    initiated_by: str = INITIATED_BY_OWNER,
    event_id: int | None = None,
) -> tuple[Job, bool]:
    """Queue a deploy of a candidate in the caller's transaction (not committed); returns the
    job and whether it is new. ``event_id`` names the feedback that asked for it.

    When a write of the same movie is already queued or running, that job is returned.
    """
    candidate, _, movie = await _candidate(session, candidate_id)
    if initiated_by == INITIATED_BY_POLICY and not candidate.is_pick:
        raise WriteRefusedError("the policy deploys only a run's pick")
    payload: dict[str, Any] = {
        "candidate_id": candidate.id,
        "initiated_by": initiated_by,
        "movie_id": movie.id,
    }
    if event_id is not None:
        payload["event_id"] = event_id
    return await queue.add_job(session, POSTER_DEPLOY, payload)


async def enqueue_deploy(
    session: AsyncSession, candidate_id: int, *, initiated_by: str = INITIATED_BY_OWNER
) -> tuple[Job, bool]:
    """Queue a deploy of a candidate, and commit; returns the job and whether it is new."""
    job, created = await add_deploy(session, candidate_id, initiated_by=initiated_by)
    await session.commit()
    return job, created


async def enqueue_restore(session: AsyncSession, deployment_id: int) -> tuple[Job, bool]:
    """Queue a restore of any deployment of a movie: it puts back what was in the folder
    before that deployment."""
    undone = await session.get(Deployment, deployment_id)
    if undone is None:
        raise WriteRefusedError(f"no deployment {deployment_id}")
    movie = await session.get(Movie, undone.movie_id)
    assert movie is not None
    return await queue.enqueue(
        session, POSTER_RESTORE, {"deployment_id": deployment_id, "movie_id": movie.id}
    )


async def queue_policy_deploy(
    session: AsyncSession, movie_id: int, pick_id: int
) -> dict[str, Any] | None:
    """After a run completes with a pick: queue the deploy ``DEPLOY_POLICY`` calls for.

    ``off`` queues nothing; ``missing`` only when the movie has no poster or its poster is
    Marquee's own; ``all`` whatever is there. Returns what was done, or ``None`` for ``off``.
    """
    policy = settings.DEPLOY_POLICY
    if policy == "off":
        return None
    if policy == "missing":
        row = await session.scalar(
            select(Artwork).where(
                Artwork.movie_id == movie_id, Artwork.artwork_kind == ARTWORK_POSTER
            )
        )
        if row is not None and row.origin != ORIGIN_MARQUEE:
            await session.commit()
            return {"policy": policy, "queued": False, "note": "the poster is the owner's"}
    job, created = await enqueue_deploy(session, pick_id, initiated_by=INITIATED_BY_POLICY)
    return {"policy": policy, "queued": created, "job_id": job.id}
