"""The ``poster_run`` job: one movie through the pipeline, with its run record.

The handler runs in the worker and does the database work around the GPU child's: it reads
the movie, starts the run record and commits, awaits the child (which never touches the
database) with a plain JSON payload, then reads the archive the pipeline left in the run's
folder, hashes the downloads, and completes the record. Every step is safe to repeat: a
second attempt of the job reuses the run's row and starts its folder over.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marquee.artwork import runs
from marquee.cancellation import JobCancelledError
from marquee.db.engine import create_engine, create_session_factory
from marquee.db.models import Job, Movie, PosterRun
from marquee.db.models.run import RUN_COMPLETED
from marquee.jobs import batch, queue
from marquee.jobs.gpu_child import describe_error
from marquee.jobs.handlers.poster_write import WriteRefusedError, queue_policy_deploy
from marquee.jobs.registry import JobContext, JobKind, PermanentJobError

logger = logging.getLogger(__name__)

CHILD_FUNCTION = "marquee.jobs.handlers.gpu:poster_run"
STOPPED = "the job stopped before the run finished"


def movie_id_of(payload: dict[str, Any]) -> int:
    value = payload.get("movie_id")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PermanentJobError("the payload needs a movie_id")
    return value


def child_payload(movie: Movie, run_folder: str) -> dict[str, Any]:
    """What the child needs: the folder, the movie, and the three text-gate fields."""
    return {
        "run_folder": run_folder,
        "movie_id": movie.id,
        "title": movie.title,
        "tmdb_id": movie.tmdb_id,
        "director": movie.director,
        "production_companies": list(movie.production_companies or []),
        "tagline": movie.tagline,
    }


async def _cancel_was_asked(session: AsyncSession, job_id: int) -> bool:
    asked = await session.scalar(select(Job.cancel_requested).where(Job.id == job_id))
    await session.commit()
    return bool(asked)


async def _end_run(
    sessions: async_sessionmaker[AsyncSession], ctx: JobContext, run_id: int, exc: BaseException
) -> None:
    """Record why the run ended without completing."""
    async with sessions() as session:
        if isinstance(exc, JobCancelledError):
            # Asked to cancel: the run is cancelled and its folder goes. A worker that is
            # stopping puts the job back instead; its next attempt starts the run over.
            if await _cancel_was_asked(session, ctx.job_id):
                await runs.cancel(session, run_id)
        elif isinstance(exc, asyncio.CancelledError):
            # A timeout, or a stopping worker that gave up waiting.
            await runs.fail(session, run_id, STOPPED)
        else:
            await runs.fail(session, run_id, describe_error(exc))


async def _policy(
    sessions: async_sessionmaker[AsyncSession], movie_id: int, pick_id: int | None
) -> dict[str, Any] | None:
    """Queue the deploy DEPLOY_POLICY calls for. The run is already complete, so a refusal is
    reported in the result rather than failing the run."""
    if pick_id is None:
        return None
    async with sessions() as session:
        try:
            return await queue_policy_deploy(session, movie_id, pick_id)
        except WriteRefusedError as exc:
            return {"queued": False, "note": str(exc)}


async def _recorded(session: AsyncSession, run: PosterRun, movie: Movie) -> dict[str, Any]:
    """The result of a run that already completed, from its record. Nothing is changed."""
    candidates = await runs.candidates_of(session, run.id)
    ranked = [runs.candidate_row(c) for c in candidates if c.rank is not None]
    pick = next((c for c in candidates if c.is_pick), None)
    await session.commit()
    return {
        "run_id": run.id,
        "movie_id": movie.id,
        "title": movie.title,
        "run_folder": run.run_folder,
        "status": run.engine_status,
        "counts": run.counts or {},
        "stages": run.stages or [],
        "ranked_total": len(ranked),
        "ranked": ranked[: runs.RANKED_SHOWN],
        "pick": {"candidate_id": pick.id, **runs.candidate_row(pick)} if pick else None,
        "deploy": None,
        "seconds": 0.0,
        "already_completed": True,
    }


async def poster_run(ctx: JobContext) -> dict[str, Any]:
    movie_id = movie_id_of(ctx.payload)
    started = time.perf_counter()
    engine = create_engine()
    sessions = create_session_factory(engine)
    try:
        async with sessions() as session:
            movie = await session.get(Movie, movie_id)
            if movie is None:
                raise PermanentJobError(f"no movie {movie_id}")
            done = await runs.run_for_job(session, ctx.job_id)
            if done is not None and done.state == RUN_COMPLETED:
                # An earlier attempt completed the run and the job ended before it said so
                # (a worker killed in between). Its candidates may already have feedback and
                # a deploy queued: return what was recorded, and change nothing.
                logger.info("RUN | %d: already completed; not run again", done.id)
                return await _recorded(session, done, movie)
            reason = runs.ineligible_reason(movie)
            if reason is not None:
                raise PermanentJobError(reason)
            run = await runs.start(session, job_id=ctx.job_id, movie=movie)
            run_id, folder, title = run.id, run.run_folder, movie.title
            payload = child_payload(movie, folder)
        logger.info("RUN | %d: movie %d (%s) in %s", run_id, movie_id, title, folder)
        try:
            result = runs.ChildResult.from_dict(await ctx.gpu(CHILD_FUNCTION, payload))
            read = await asyncio.to_thread(runs.read_run_folder, runs.run_dir(folder))
            async with sessions() as session:
                await runs.complete(session, run_id, result, read)
                pick = await runs.pick_of(session, run_id)
                pick_id = pick.id if pick is not None else None
        except BaseException as exc:
            with contextlib.suppress(Exception):
                await _end_run(sessions, ctx, run_id, exc)
            raise
        deployed = await _policy(sessions, movie_id, pick_id)
        pick_row = next((row for row in read.ranked if row["file"] == result.pick_file), None)
        return {
            "run_id": run_id,
            "movie_id": movie_id,
            "title": title,
            "run_folder": folder,
            "status": result.status,
            "counts": result.counts,
            "stages": result.stages,
            "ranked_total": read.ranked_total,
            "ranked": read.ranked,
            "pick": {"candidate_id": pick_id, **pick_row} if pick_row else None,
            "deploy": deployed,
            "seconds": round(time.perf_counter() - started, 1),
        }
    finally:
        await engine.dispose()


POSTER_RUN = JobKind(
    "poster_run",
    "gpu",
    handler=poster_run,
    max_attempts=2,
    timeout_s=10 * 60,
    dedupe_key="poster_run:{movie_id}",
)


async def enqueue_run(session: AsyncSession, movie_id: int) -> tuple[Job, bool]:
    """Queue a run of one movie; returns the job and whether it is new (one run per movie)."""
    return await queue.enqueue(session, POSTER_RUN, {"movie_id": movie_id})


async def enqueue_runs(
    session: AsyncSession, movie_ids: Sequence[int], *, payload: dict[str, Any]
) -> tuple[Job, list[Job]]:
    """Queue one batch of runs. A movie with a run already active is left out."""
    return await batch.enqueue_batch(
        session, [(POSTER_RUN, {"movie_id": movie_id}) for movie_id in movie_ids], payload=payload
    )


async def enqueue_scope(
    session: AsyncSession, scope: runs.Scope, *, limit: int | None = None
) -> tuple[Job, list[Job], int]:
    """Queue a batch of runs for the movies a scope covers; returns the batch, its children,
    and how many movies the scope found."""
    movie_ids = await runs.scope_movie_ids(session, scope, limit=limit)
    parent, children = await enqueue_runs(
        session, movie_ids, payload={"scope": scope, "movies": len(movie_ids)}
    )
    return parent, children, len(movie_ids)
