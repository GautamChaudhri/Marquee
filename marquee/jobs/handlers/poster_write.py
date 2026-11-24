"""The ``poster_deploy`` and ``poster_restore`` jobs: writes to a movie folder.

Both are class ``media_write``, so two writes never run at once. Both go through
:func:`marquee.artwork.deploy.write_poster`. Payloads carry the movie's id; the handlers
check it against the database.

**The newest write request for a movie wins.** A deploy or a restore is always queued,
except a request identical to the movie's newest write job while that job is still queued,
which is returned instead (:func:`add_write`). When a write job starts, first attempt or
retry, and a newer write job of the same movie is queued, running, or succeeded, the older
one succeeds as ``skipped: superseded by job N`` and writes nothing. A job that already
renamed its file into place (it kept an intent) records that write first, so the folder
never holds a poster no row describes; the newer job then writes over it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
from marquee.jobs.store import FRESH

WRITE_KINDS = ("poster_deploy", "poster_restore")
SUPERSEDING_STATES = ("queued", "running", "succeeded")
WRITE_LOCK_CLASS = int.from_bytes(b"mqwr", "big")
"""With the movie's id, the transaction lock that queues one write request of a movie at a
time (the two-key advisory lock space, apart from the worker's and the sync's keys)."""


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


async def _own_write(sessions: async_sessionmaker[AsyncSession], job_id: int) -> Deployment | None:
    """The deployments row this job already wrote, if an earlier attempt got that far."""
    async with sessions() as session:
        own = await session.scalar(select(Deployment).where(Deployment.job_id == job_id))
        await session.commit()
    return own


async def _original(job_id: int, source_file: str) -> bytes:
    """The bytes this job deploys: fetched once, then the same bytes on every attempt."""
    data = await asyncio.to_thread(_staged, job_id)
    if data is None:
        data = await deploy.fetch_original(source_file)
        await asyncio.to_thread(_keep_staged, job_id, data)
    return data


# ── The newest request wins ───────────────────────────────────────────────


def _of_movie(movie_id: int) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    """The conditions that pick the write jobs of one movie."""
    return Job.kind.in_(WRITE_KINDS), Job.payload["movie_id"].as_integer() == movie_id


async def newer_write(session: AsyncSession, job_id: int, movie_id: int) -> int | None:
    """The newest write job of the movie queued after this one that is queued, running, or
    succeeded: the one that wins over this job. ``None`` when there is none."""
    return await session.scalar(
        select(Job.id)
        .where(*_of_movie(movie_id), Job.id > job_id, Job.state.in_(SUPERSEDING_STATES))
        .order_by(Job.id.desc())
        .limit(1)
    )


async def _superseded(
    sessions: async_sessionmaker[AsyncSession], job_id: int, movie_id: int
) -> dict[str, Any] | None:
    """The result of a write job that steps aside for a newer one, or ``None`` to go ahead.

    A job that kept an intent may already have renamed its file into place; it goes ahead
    and records that write rather than leave the folder holding a poster no row describes.
    """
    if await asyncio.to_thread(deploy.intent_path(job_id).exists):
        return None
    async with sessions() as session:
        newer = await newer_write(session, job_id, movie_id)
        await session.commit()
    if newer is None:
        return None
    with contextlib.suppress(OSError):
        _staged_path(job_id).unlink(missing_ok=True)
    return {
        "movie_id": movie_id,
        "skipped": f"superseded by job {newer}",
        "superseded_by": newer,
    }


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
        if await _own_write(sessions, ctx.job_id) is None and (
            skipped := await _superseded(sessions, ctx.job_id, movie.id)
        ):
            return skipped
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
        if skipped := await _superseded(sessions, ctx.job_id, movie.id):
            return {**skipped, "restores_deployment_id": deployment_id}
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
)
POSTER_RESTORE = JobKind(
    "poster_restore",
    "media_write",
    handler=poster_restore,
    max_attempts=3,
    timeout_s=5 * 60,
)


# ── Queueing ──────────────────────────────────────────────────────────────


async def add_write(
    session: AsyncSession, kind: JobKind, payload: dict[str, Any]
) -> tuple[Job, bool]:
    """Queue a write of ``payload["movie_id"]`` in the caller's transaction; returns the job
    and whether it is new.

    The request is always queued, unless the movie's newest write job is this same request
    (kind and payload) and still queued: that job is returned. Requests for one movie are
    queued one at a time under a transaction lock, so "newest" is well defined.
    """
    movie_id = payload["movie_id"]
    await session.execute(select(func.pg_advisory_xact_lock(WRITE_LOCK_CLASS, movie_id)))
    newest = await session.scalar(
        select(Job).where(*_of_movie(movie_id)).order_by(Job.id.desc()).limit(1),
        execution_options=FRESH,
    )
    if (
        newest is not None
        and newest.state == "queued"
        and newest.kind == kind.name
        and newest.payload == payload
    ):
        return newest, False
    return await queue.add_job(session, kind, payload)


async def add_deploy(
    session: AsyncSession,
    candidate_id: int,
    *,
    initiated_by: str = INITIATED_BY_OWNER,
    event_id: int | None = None,
) -> tuple[Job, bool]:
    """Queue a deploy of a candidate in the caller's transaction (not committed); returns the
    job and whether it is new. ``event_id`` names the feedback that asked for it, so each
    act of feedback has a deploy of its own (see :func:`add_write`).
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
    return await add_write(session, POSTER_DEPLOY, payload)


async def enqueue_deploy(
    session: AsyncSession, candidate_id: int, *, initiated_by: str = INITIATED_BY_OWNER
) -> tuple[Job, bool]:
    """Queue a deploy of a candidate, and commit; returns the job and whether it is new."""
    job, created = await add_deploy(session, candidate_id, initiated_by=initiated_by)
    await session.commit()
    return job, created


async def enqueue_restore(session: AsyncSession, deployment_id: int) -> tuple[Job, bool]:
    """Queue a restore of any deployment of a movie, and commit: it puts back what was in
    the folder before that deployment."""
    undone = await session.get(Deployment, deployment_id)
    if undone is None:
        raise WriteRefusedError(f"no deployment {deployment_id}")
    movie = await session.get(Movie, undone.movie_id)
    assert movie is not None
    job, created = await add_write(
        session, POSTER_RESTORE, {"deployment_id": deployment_id, "movie_id": movie.id}
    )
    await session.commit()
    return job, created


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
