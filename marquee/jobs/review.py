"""Review: the owner's feedback, with the deploy and the rebuild it calls for, in one commit.

:func:`submit` records the feedback (:func:`marquee.taste.feedback.record`), queues the
owner's deploy of the chosen candidate when the action deploys and the caller did not say
otherwise, queues a ``taste_rebuild`` when the exemplar revision changed, and commits once.
:func:`revoke` likewise. The API and the command line call these; the taste package cannot,
because queueing a job is above it.

A refused deploy (the movie has no folder to write to) does not undo the feedback: the
exemplars are the owner's statement, the deploy is a separate matter. The result says why.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.db.models import Job
from marquee.jobs.handlers.poster_write import WriteRefusedError, add_deploy
from marquee.jobs.handlers.taste_rebuild import TASTE_REBUILD, add_rebuild
from marquee.taste import feedback

logger = logging.getLogger(__name__)


@dataclass
class ReviewResult:
    outcome: feedback.Outcome
    deploy_job: Job | None = None
    deploy_note: str | None = None
    rebuild_job: Job | None = None

    @property
    def created(self) -> bool:
        """False when the idempotency key was used before and nothing changed now."""
        return not self.outcome.replayed


async def _jobs_of(session: AsyncSession, event_id: int) -> tuple[Job | None, Job | None]:
    """The deploy and the rebuild that the event queued, found by the event id they carry."""
    rows = await session.scalars(
        select(Job).where(Job.payload["event_id"].as_integer() == event_id).order_by(Job.id)
    )
    jobs = list(rows)
    deploy = next((job for job in jobs if job.kind == "poster_deploy"), None)
    rebuild = next((job for job in jobs if job.kind == TASTE_REBUILD.name), None)
    return deploy, rebuild


async def _replayed(session: AsyncSession, outcome: feedback.Outcome) -> ReviewResult:
    deploy, rebuild = await _jobs_of(session, outcome.event.id)
    await session.commit()
    return ReviewResult(outcome=outcome, deploy_job=deploy, rebuild_job=rebuild)


async def _queue(session: AsyncSession, outcome: feedback.Outcome, *, deploy: bool) -> ReviewResult:
    result = ReviewResult(outcome=outcome)
    if deploy and outcome.deploy_candidate_id is not None:
        try:
            result.deploy_job, _ = await add_deploy(
                session, outcome.deploy_candidate_id, event_id=outcome.event.id
            )
        except WriteRefusedError as exc:
            result.deploy_note = f"not deployed: {exc}"
    if outcome.revision_changed:
        assert outcome.revision_after is not None
        result.rebuild_job, _ = await add_rebuild(
            session, outcome.revision_after, event_id=outcome.event.id
        )
    return result


async def submit(
    session: AsyncSession,
    *,
    run_id: int,
    action: str,
    idempotency_key: str,
    candidate_id: int | None = None,
    order: list[int] | None = None,
    hated: list[int] | None = None,
    shown: list[int] | None = None,
    deploy: bool = True,
) -> ReviewResult:
    """Record feedback on a run, queue what it calls for, and commit once."""
    try:
        outcome = await feedback.record(
            session,
            run_id=run_id,
            action=action,
            idempotency_key=idempotency_key,
            candidate_id=candidate_id,
            order=order,
            hated=hated,
            shown=shown,
        )
        if outcome.replayed:
            return await _replayed(session, outcome)
        result = await _queue(session, outcome, deploy=deploy)
        await session.commit()
    except IntegrityError:
        # The same key, sent twice at once: the other request recorded it first.
        await session.rollback()
        existing = await feedback.event_by_key(session, idempotency_key)
        if existing is None:
            raise
        return await _replayed(session, await feedback.replay(session, existing))
    except BaseException:
        await session.rollback()
        raise
    return result


async def revoke(
    session: AsyncSession, *, event_id: int, idempotency_key: str | None = None
) -> ReviewResult:
    """Revoke one earlier event, queue the rebuild it calls for, and commit once."""
    try:
        outcome = await feedback.revoke(session, event_id=event_id, idempotency_key=idempotency_key)
        if outcome.replayed:
            return await _replayed(session, outcome)
        result = await _queue(session, outcome, deploy=False)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return result
