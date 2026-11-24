"""The ``poster_run`` job: one movie through the pipeline, with its run record; and the
``poster_run_masked`` job: the same run against the active profile without the movie's own
exemplars, for a blind evaluation.

The handler runs in the worker and does the database work around the GPU child's: it reads
the movie, starts the run record and commits, awaits the child (which never touches the
database) with a plain JSON payload, then reads the archive the pipeline left in the run's
folder, hashes the downloads, and completes the record. Every step is safe to repeat: a
second attempt of the job reuses the run's row and starts its folder over.

Both kinds hand the child the active profile's and residual artifact's identity, so the
engine ranks with the active residual when it is compatible. A masked run also hands it the
exemplars to leave out; it is never part of the product loop (no deploy policy), a movie
keeps only its newest one, and when it belongs to an evaluation it records where the
owner's poster landed (:mod:`marquee.taste.evaluation`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marquee.artwork import runs
from marquee.cancellation import JobCancelledError
from marquee.config import pipeline_settings
from marquee.db.engine import create_engine, create_session_factory
from marquee.db.models import Evaluation, Job, Movie, PosterRun, ResidualTraining
from marquee.db.models.run import RUN_COMPLETED
from marquee.db.models.taste import POLARITY_POSITIVE
from marquee.jobs import batch, queue
from marquee.jobs.gpu_child import describe_error
from marquee.jobs.handlers.poster_write import WriteRefusedError, queue_policy_deploy
from marquee.jobs.registry import JobContext, JobKind, PermanentJobError
from marquee.taste import evaluation, exemplars, rebuild

logger = logging.getLogger(__name__)

CHILD_FUNCTION = "marquee.jobs.handlers.gpu:poster_run"
STOPPED = "the job stopped before the run finished"


def movie_id_of(payload: dict[str, Any]) -> int:
    value = payload.get("movie_id")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PermanentJobError("the payload needs a movie_id")
    return value


def evaluation_id_of(payload: dict[str, Any]) -> int | None:
    value = payload.get("evaluation_id")
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
        raise PermanentJobError("the payload's evaluation_id must be a positive whole number")
    return value


def child_payload(
    movie: Movie,
    run_folder: str,
    *,
    profile: dict[str, Any] | None = None,
    residual: dict[str, Any] | None = None,
    mask: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """What the child needs: the folder, the movie, the three text-gate fields, the active
    profile's and residual artifact's identity, and for a masked run what to leave out."""
    payload: dict[str, Any] = {
        "run_folder": run_folder,
        "movie_id": movie.id,
        "title": movie.title,
        "tmdb_id": movie.tmdb_id,
        "director": movie.director,
        "production_companies": list(movie.production_companies or []),
        "tagline": movie.tagline,
        "profile": profile,
        "residual": residual,
    }
    if mask is not None:
        payload["mask"] = mask
    return payload


async def identities(
    session: AsyncSession,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The active profile's and the active residual artifact's ``{"id", "sha256",
    "file_name"}``, as the child takes them. An artifact is passed only with its profile."""
    profile = await rebuild.active_profile(session)
    if profile is None:
        return None, None
    artifact = await session.scalar(select(ResidualTraining).where(ResidualTraining.is_active))
    residual = (
        {"id": artifact.id, "sha256": artifact.artifact_sha256, "file_name": artifact.file_name}
        if artifact is not None and artifact.artifact_sha256 and artifact.file_name
        else None
    )
    return {"id": profile.id, "sha256": profile.sha256, "file_name": profile.file_name}, residual


async def mask_of(session: AsyncSession, movie: Movie) -> dict[str, list[str]]:
    """The exemplars a masked run of ``movie`` leaves out: every active one of the movie, by
    the name the profile knows it under (a positive's staged name, a negative's kept name).
    A movie with no active positive cannot be measured blind."""
    rows = [row for row in await exemplars.active(session) if row.movie_id == movie.id]
    positives = sorted({row.staged_name for row in rows if row.polarity == POLARITY_POSITIVE})
    negatives = sorted({row.file_name for row in rows if row.polarity != POLARITY_POSITIVE})
    if not positives:
        raise PermanentJobError(
            f"movie {movie.id} ({movie.title}) has no active positive exemplar, so a masked "
            "run has nothing to leave out"
        )
    return {"positives": [name for name in positives if name], "negatives": negatives}


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


async def _run(
    ctx: JobContext, sessions: async_sessionmaker[AsyncSession], *, masked: bool
) -> dict[str, Any]:
    """One run of the movie in the payload, normal or masked; the job's result."""
    movie_id = movie_id_of(ctx.payload)
    started = time.perf_counter()
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
        mask = await mask_of(session, movie) if masked else None
        profile, residual = await identities(session)
        if masked and profile is None:
            raise PermanentJobError("there is no active taste profile to mask")
        run = await runs.start(session, job_id=ctx.job_id, movie=movie, masked=masked)
        run_id, folder, title = run.id, run.run_folder, movie.title
        payload = child_payload(movie, folder, profile=profile, residual=residual, mask=mask)
    logger.info(
        "RUN | %d: movie %d (%s) in %s%s",
        run_id,
        movie_id,
        title,
        folder,
        f", masked: {len(mask['positives'])} positive(s) and {len(mask['negatives'])} "
        "negative(s) left out"
        if mask
        else "",
    )
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
    deployed = None if masked else await _policy(sessions, movie_id, pick_id)
    pick_row = next((row for row in read.ranked if row["file"] == result.pick_file), None)
    return {
        "run_id": run_id,
        "movie_id": movie_id,
        "title": title,
        "run_folder": folder,
        "status": result.status,
        "counts": result.counts,
        "stages": result.stages,
        "scorer": result.scorer,
        "ranked_total": read.ranked_total,
        "ranked": read.ranked,
        "pick": {"candidate_id": pick_id, **pick_row} if pick_row else None,
        "deploy": deployed,
        "masked": masked,
        "seconds": round(time.perf_counter() - started, 1),
    }


async def poster_run(ctx: JobContext) -> dict[str, Any]:
    engine = create_engine()
    sessions = create_session_factory(engine)
    try:
        return await _run(ctx, sessions, masked=False)
    finally:
        await engine.dispose()


async def _is_requeue(sessions: async_sessionmaker[AsyncSession], ctx: JobContext) -> bool:
    """A stopping worker puts the job back: its next attempt records the outcome."""
    async with sessions() as session:
        return not await _cancel_was_asked(session, ctx.job_id)


async def masked_run(ctx: JobContext) -> dict[str, Any]:
    """A masked run of one movie, and its evaluation's result when it belongs to one."""
    movie_id = movie_id_of(ctx.payload)
    evaluation_id = evaluation_id_of(ctx.payload)
    engine = create_engine()
    sessions = create_session_factory(engine)
    try:
        try:
            result = await _run(ctx, sessions, masked=True)
        except BaseException as exc:
            if evaluation_id is not None and not (
                isinstance(exc, JobCancelledError) and await _is_requeue(sessions, ctx)
            ):
                with contextlib.suppress(Exception):
                    async with sessions() as session:
                        run = await runs.run_for_job(session, ctx.job_id)
                        await evaluation.record_failure(
                            session,
                            evaluation_id,
                            movie_id,
                            run_id=run.id if run is not None else None,
                            reason=describe_error(exc),
                        )
            raise
        run_id = result["run_id"]
        async with sessions() as session:
            removed = await runs.remove_older_masked(session, movie_id, run_id)
        if evaluation_id is not None:
            async with sessions() as session:
                row = await evaluation.record_run(session, evaluation_id, movie_id, run_id)
            result["evaluation"] = {"id": evaluation_id, **evaluation.result_line(row)}
        return {**result, "removed_masked_runs": removed}
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

POSTER_RUN_MASKED = JobKind(
    "poster_run_masked",
    "gpu",
    handler=masked_run,
    max_attempts=2,
    timeout_s=10 * 60,
    dedupe_key="poster_run_masked:{movie_id}",
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


EVALUATION_LOCK = int.from_bytes(b"mqev", "big")
"""The transaction lock that queues one evaluation at a time."""


class EvaluationRefusedError(Exception):
    """An evaluation cannot be queued now; the message says why."""


async def enqueue_evaluation(
    session: AsyncSession, movie_ids: Sequence[int] | None = None, *, limit: int | None = None
) -> tuple[Evaluation, Job, list[Job]]:
    """Queue a blind evaluation: one batch of masked runs, and its record; one transaction.

    Every movie a run accepts that has an active positive exemplar, or the movies named
    (each must be such a movie). Refused while another evaluation runs, when there is no
    active profile, or when the profile is not built from the active exemplars (a rebuild is
    due): a masked run leaves out the movie's rows of that profile, and they must be the
    ones its exemplars name.
    """
    await session.execute(select(func.pg_advisory_xact_lock(EVALUATION_LOCK)))
    if (other := await evaluation.running(session)) is not None:
        raise EvaluationRefusedError(f"evaluation {other.id} is still running")
    profile = await rebuild.active_profile(session)
    if profile is None:
        raise EvaluationRefusedError("there is no active taste profile")
    if profile.revision != await exemplars.revision(session):
        raise EvaluationRefusedError(
            "the taste profile is not built from the active exemplars yet; rebuild it first"
        )
    wanted = list(dict.fromkeys(movie_ids)) if movie_ids is not None else None
    found = await evaluation.evaluable_movie_ids(session, wanted, limit=limit)
    if wanted is not None:
        refused = [movie_id for movie_id in wanted if movie_id not in set(found)]
        if refused:
            raise EvaluationRefusedError(
                "these movies cannot be evaluated (unknown, cannot be run, or no active "
                f"positive exemplar): {refused[:50]}"
            )
    if not found:
        raise EvaluationRefusedError("no movie can be evaluated")
    _, residual = await identities(session)
    scorer = (
        "residual"
        if residual is not None and pipeline_settings.SCORER != "weighted"
        else "weighted"
    )
    row = Evaluation(
        profile_id=profile.id,
        profile_sha256=profile.sha256,
        residual_id=residual["id"] if residual else None,
        scorer=scorer,
        movies=len(found),
        queued=0,
    )
    session.add(row)
    await session.flush()
    parent, children = await batch.enqueue_batch(
        session,
        [
            (POSTER_RUN_MASKED, {"movie_id": movie_id, "evaluation_id": row.id})
            for movie_id in found
        ],
        payload={"evaluation_id": row.id, "movies": len(found)},
        commit=False,
    )
    row.job_id = parent.id
    row.queued = len(children)
    await session.commit()
    logger.info(
        "EVAL | evaluation %d queued: %d of %d movies (batch %d)",
        row.id,
        len(children),
        len(found),
        parent.id,
    )
    return row, parent, children
