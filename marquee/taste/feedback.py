"""Feedback: the owner's review of a run, recorded as one append-only preference event.

What each act does (decision 4 of the M5b brief):

========== ==================== ============================ =================================
action     chosen               positive exemplar            negative exemplars
========== ==================== ============================ =================================
approval   the run's pick       0.75, only with no active    none
                                positive
override   the candidate named  1.0, replaces the active one none
rank       first of the order   1.0, replaces the active one each hated candidate ranked
                                                             ``FEEDBACK_HARD_NEGATIVE_RANK_MAX``
                                                             or better
reject     none                 none                         the same, from every shown one
                                                             when none is named
========== ==================== ============================ =================================

An override that names the pick is recorded as an approval. A hated candidate whose bytes are
the movie's active positive ends that positive. A revoke brings back what its event ended, as far as the
rules allow (:func:`marquee.taste.exemplars.revoke_event`). Feedback goes to a movie's latest
completed run only. An event keeps its own copy of what was shown, because ``run_candidates`` rows go
when their run does: each shown candidate by TMDB's file name, with its hash, rank, score,
stack, and the normalised features from the run's archive.

The exemplars an event makes are active as soon as it is recorded, whatever happens to the
deploy the jobs layer queues for it (:mod:`marquee.jobs.review`). Nothing here commits.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.artwork import runs
from marquee.config import pipeline_settings
from marquee.db.models import Exemplar, PosterRun, PreferenceEvent, RunCandidate
from marquee.db.models.run import RUN_COMPLETED
from marquee.db.models.taste import (
    ACTION_APPROVAL,
    ACTION_OVERRIDE,
    ACTION_RANK,
    ACTION_REJECT,
    ACTION_REVOKE,
    MAX_EXPOSED,
    MAX_IDEMPOTENCY_KEY,
)
from marquee.taste import exemplars, rebuild

logger = logging.getLogger(__name__)

FEEDBACK_ACTIONS = (ACTION_APPROVAL, ACTION_OVERRIDE, ACTION_RANK, ACTION_REJECT)
DEPLOYING = frozenset({ACTION_APPROVAL, ACTION_OVERRIDE, ACTION_RANK})


class FeedbackError(Exception):
    """Feedback cannot be recorded as asked; the message says why."""


class UnknownRunError(FeedbackError):
    """No such run."""


class UnknownCandidateError(FeedbackError):
    """A candidate named is not one of the run's."""


class UnknownEventError(FeedbackError):
    """No such event."""


class FeedbackRefusedError(FeedbackError):
    """The run, the candidates, or the event do not allow this feedback."""


@dataclass
class Outcome:
    """What one act of feedback recorded and changed."""

    event: PreferenceEvent
    changes: exemplars.Changes = field(default_factory=exemplars.Changes)
    replayed: bool = False
    """The idempotency key was used before: this is that event, and nothing changed now."""
    deploy_candidate_id: int | None = None
    """The run candidate a deploy should write (approval, override, rank), else ``None``."""
    revision_before: str | None = None
    revision_after: str | None = None

    @property
    def revision_changed(self) -> bool:
        return not self.replayed and self.revision_before != self.revision_after


def check_key(key: str) -> str:
    if not isinstance(key, str) or not 1 <= len(key) <= MAX_IDEMPOTENCY_KEY:
        raise FeedbackRefusedError(
            f"an idempotency key is 1 to {MAX_IDEMPOTENCY_KEY} characters long"
        )
    return key


async def event_by_key(session: AsyncSession, key: str) -> PreferenceEvent | None:
    return await session.scalar(
        select(PreferenceEvent).where(PreferenceEvent.idempotency_key == key)
    )


async def latest_completed_run_id(session: AsyncSession, movie_id: int) -> int | None:
    return await session.scalar(
        select(func.max(PosterRun.id)).where(
            PosterRun.movie_id == movie_id, PosterRun.state == RUN_COMPLETED
        )
    )


async def blind_for(session: AsyncSession, run: PosterRun) -> bool | None:
    """Whether the run's profile had no positive exemplar of its movie; ``None`` when that
    profile is not registered, so nobody can say."""
    profile = await rebuild.profile_by_sha(session, run.profile_sha256)
    if profile is None:
        return None
    return run.movie_id not in set(profile.positive_movie_ids or [])


def _stack(row: RunCandidate) -> str | None:
    if row.stack_rank is None:
        return row.stack_label
    return f"{row.stack_rank}{row.stack_label or ''}"


def _archive_rows(folder: Path) -> dict[str, dict[str, Any]]:
    """The run archive's ledger rows by TMDB file name. Blocking."""
    try:
        payload = json.loads((folder / runs.RUN_ARCHIVE).read_text(encoding="utf-8"))
        rows = payload["diagnostic_ledger"]["candidates"]
        return {str(row["orig_filename"]): row for row in rows if isinstance(row, dict)}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FeedbackRefusedError(f"the run's archive cannot be read: {exc}") from exc


def _finite(value: Any) -> dict[str, float]:
    """The normalised features that are finite numbers; anything else is left out."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for name, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            continue
        number = float(raw)
        if number == number and abs(number) != float("inf"):
            out[str(name)] = number
    return out


def exposed_row(row: RunCandidate, archived: dict[str, Any] | None) -> dict[str, Any]:
    """One shown candidate, as an event keeps it."""
    return {
        "candidate_id": row.source_file,
        "sha256": row.sha256,
        "baseline_rank": row.rank,
        "baseline_score": row.final_score,
        "stack": _stack(row),
        "normalized_features": _finite((archived or {}).get("normalized_features")),
    }


def _ids(value: list[int] | None, what: str) -> list[int]:
    ids = list(value or [])
    if len(set(ids)) != len(ids):
        raise FeedbackRefusedError(f"{what} names a candidate twice")
    return ids


async def record(
    session: AsyncSession,
    *,
    run_id: int,
    action: str,
    idempotency_key: str,
    candidate_id: int | None = None,
    order: list[int] | None = None,
    hated: list[int] | None = None,
    shown: list[int] | None = None,
) -> Outcome:
    """Record one act of feedback on a run and apply it to the exemplars.

    Candidates are named by their ``run_candidates`` id. ``shown`` is what the owner was
    shown, in that order; by default every ranked candidate, by rank. A key already used
    returns the event it made and changes nothing.
    """
    check_key(idempotency_key)
    if (existing := await event_by_key(session, idempotency_key)) is not None:
        return await replay(session, existing)
    if action not in FEEDBACK_ACTIONS:
        raise FeedbackRefusedError(f"unknown action {action!r}; one of {FEEDBACK_ACTIONS}")
    run = await session.get(PosterRun, run_id)
    if run is None:
        raise UnknownRunError(f"no run {run_id}")
    movie = await exemplars.lock_movie(session, run.movie_id)
    assert movie is not None
    if run.state != RUN_COMPLETED:
        raise FeedbackRefusedError(f"run {run.id} is {run.state}; feedback goes to a completed run")
    latest = await latest_completed_run_id(session, movie.id)
    if latest != run.id:
        raise FeedbackRefusedError(
            f"run {run.id} is not the latest completed run of {movie.title}; feedback goes to "
            f"run {latest}"
        )

    candidates = await runs.candidates_of(session, run.id)
    by_id = {row.id: row for row in candidates}
    order_ids, hated_ids, shown_ids = (
        _ids(order, "order"),
        _ids(hated, "hated"),
        _ids(shown, "shown"),
    )
    named = order_ids + hated_ids + shown_ids + ([candidate_id] if candidate_id is not None else [])
    unknown = [n for n in named if n not in by_id]
    if unknown:
        raise UnknownCandidateError(f"candidate {unknown[0]} is not one of run {run.id}'s")
    pick = next((row for row in candidates if row.is_pick), None)

    chosen: RunCandidate | None = None
    if action == ACTION_OVERRIDE:
        if candidate_id is None:
            raise FeedbackRefusedError("an override names a candidate")
        chosen = by_id[candidate_id]
        if pick is not None and chosen.id == pick.id:
            action = ACTION_APPROVAL  # an override that names the pick is an approval
    if action == ACTION_APPROVAL:
        if pick is None:
            raise FeedbackRefusedError(f"run {run.id} has no pick to approve")
        chosen = pick
    elif action == ACTION_RANK:
        if not order_ids:
            raise FeedbackRefusedError("a rank needs an order of at least one candidate")
        if set(order_ids) & set(hated_ids):
            raise FeedbackRefusedError("a candidate cannot be both in the order and hated")
        chosen = by_id[order_ids[0]]
    elif action == ACTION_REJECT:
        chosen = None
    if action not in (ACTION_RANK, ACTION_REJECT) and (order_ids or hated_ids):
        raise FeedbackRefusedError(f"an {action} takes no order and no hated candidates")

    # What was shown: as given, else every ranked candidate; what the owner acted on must be
    # among it.
    acted = ([chosen.id] if chosen else []) + order_ids + hated_ids
    if shown_ids:
        missing = [n for n in acted if n not in shown_ids]
        if missing:
            raise FeedbackRefusedError(f"candidate {missing[0]} was not among those shown")
        shown_rows = [by_id[n] for n in shown_ids]
    else:
        shown_rows = [row for row in candidates if row.rank is not None]
        shown_rows += [by_id[n] for n in dict.fromkeys(acted) if by_id[n] not in shown_rows]
    if len(shown_rows) > MAX_EXPOSED:
        raise FeedbackRefusedError(f"an event keeps at most {MAX_EXPOSED} shown candidates")
    if action == ACTION_REJECT and not hated_ids:
        hated_ids = [row.id for row in shown_rows if row.sha256 is not None]
    folder = runs.run_dir(run.run_folder)
    for n in ([chosen.id] if chosen else []) + hated_ids:
        row = by_id[n]
        if row.sha256 is None or not await asyncio.to_thread((folder / row.source_file).is_file):
            raise FeedbackRefusedError(
                f"candidate {row.id} ({row.source_file}) has no downloaded file; it cannot be "
                "chosen or hated"
            )

    archived = await asyncio.to_thread(_archive_rows, folder)
    exposed = [exposed_row(row, archived.get(row.source_file)) for row in shown_rows]
    context: dict[str, Any] = {
        "order": [by_id[n].source_file for n in order_ids],
        "hated": [by_id[n].source_file for n in hated_ids],
        "selected_candidate": chosen.source_file if chosen else None,
    }
    now = datetime.now(UTC)
    event = PreferenceEvent(
        movie_id=movie.id,
        run_id=run.id,
        action=action,
        chosen_source_file=chosen.source_file if chosen else None,
        chosen_sha256=chosen.sha256 if chosen else None,
        pick_source_file=pick.source_file if pick else None,
        profile_sha256=run.profile_sha256,
        scorer_name=run.scorer_name,
        blind=await blind_for(session, run),
        exposed=exposed,
        context=context,
        idempotency_key=idempotency_key,
        created_at=now,
    )
    session.add(event)
    await session.flush()

    outcome = Outcome(event=event, revision_before=await exemplars.revision(session))
    changes = outcome.changes
    if chosen is not None and (
        action != ACTION_APPROVAL or await exemplars.active_positive(session, movie.id) is None
    ):
        assert chosen.sha256 is not None
        kept = await asyncio.to_thread(
            exemplars.keep_file, folder / chosen.source_file, chosen.sha256
        )
        await exemplars.set_positive(
            session,
            changes,
            movie_id=movie.id,
            weight=exemplars.APPROVAL_WEIGHT
            if action == ACTION_APPROVAL
            else exemplars.CHOICE_WEIGHT,
            sha256=chosen.sha256,
            file_name=kept,
            staged_name=exemplars.staged_name_for(
                movie.title, movie.year, Path(chosen.source_file).suffix
            ),
            event_id=event.id,
            source_file=chosen.source_file,
            now=now,
        )
    hard_max = pipeline_settings.FEEDBACK_HARD_NEGATIVE_RANK_MAX
    for n in hated_ids:
        row = by_id[n]
        assert row.sha256 is not None
        await exemplars.end_positive_with_image(
            session, changes, movie_id=movie.id, sha256=row.sha256, event_id=event.id, now=now
        )
        if row.rank is None or row.rank > hard_max:
            continue  # an easy negative: it informs the order, not the exemplars
        kept = await asyncio.to_thread(exemplars.keep_file, folder / row.source_file, row.sha256)
        await exemplars.add_negative(
            session,
            changes,
            movie_id=movie.id,
            sha256=row.sha256,
            file_name=kept,
            event_id=event.id,
            source_file=row.source_file,
            now=now,
        )
    outcome.revision_after = await exemplars.revision(session)
    outcome.deploy_candidate_id = chosen.id if chosen is not None and action in DEPLOYING else None
    logger.info(
        "FEEDBACK | event %d: %s on run %d of movie %d; %d exemplar(s) made, %d ended",
        event.id,
        action,
        run.id,
        movie.id,
        len(changes.created),
        len(changes.ended),
    )
    return outcome


async def revoke(
    session: AsyncSession, *, event_id: int, idempotency_key: str | None = None
) -> Outcome:
    """Record a ``revoke`` of one earlier event and undo what it did to the exemplars.

    The event must not be a revoke and must not be revoked already. It does not touch the
    library: restoring a deployment is how a poster is put back. The default key is
    ``revoke:<event id>``, so asking twice returns the same revoke.
    """
    key = check_key(idempotency_key or f"revoke:{event_id}")
    if (existing := await event_by_key(session, key)) is not None:
        return await replay(session, existing)
    target = await session.get(PreferenceEvent, event_id)
    if target is None:
        raise UnknownEventError(f"no event {event_id}")
    await exemplars.lock_movie(session, target.movie_id)
    if target.action == ACTION_REVOKE:
        raise FeedbackRefusedError(f"event {event_id} is a revoke; it cannot be revoked")
    earlier = await session.scalar(
        select(PreferenceEvent.id).where(PreferenceEvent.revokes_event_id == event_id)
    )
    if earlier is not None:
        raise FeedbackRefusedError(f"event {event_id} was already revoked by event {earlier}")
    now = datetime.now(UTC)
    revision_before = await exemplars.revision(session)
    changes = exemplars.Changes()
    newly_ended = await exemplars.revoke_event(session, changes, event_id=target.id, now=now)
    # The event is written once, after the exemplars changed, so its context can say what
    # came back: the table is append-only.
    event = PreferenceEvent(
        movie_id=target.movie_id,
        run_id=target.run_id,
        action=ACTION_REVOKE,
        exposed=[],
        context={"reactivated": [row.id for row in changes.reactivated]},
        revokes_event_id=target.id,
        idempotency_key=key,
        created_at=now,
    )
    session.add(event)
    await session.flush()
    for row in newly_ended:
        row.ended_by_event_id = event.id
    await session.flush()
    outcome = Outcome(event=event, changes=changes, revision_before=revision_before)
    outcome.revision_after = await exemplars.revision(session)
    logger.info(
        "FEEDBACK | event %d revokes event %d; %d exemplar(s) revoked, %d active again",
        event.id,
        target.id,
        len(outcome.changes.ended),
        len(outcome.changes.reactivated),
    )
    return outcome


async def replay(session: AsyncSession, event: PreferenceEvent) -> Outcome:
    """The outcome of an event recorded earlier, rebuilt from the records; nothing changes.

    The exemplars it made carry its id; the ones it ended carry its time as their end.
    """
    changes = exemplars.Changes()
    if event.action == ACTION_REVOKE:
        made = list(
            await session.scalars(
                select(Exemplar)
                .where(Exemplar.event_id == event.revokes_event_id)
                .order_by(Exemplar.id)
            )
        )
        changes.ended = [row for row in made if row.ended_at == event.created_at]
        reactivated = event.context.get("reactivated") or []
        if reactivated:
            changes.reactivated = list(
                await session.scalars(
                    select(Exemplar).where(Exemplar.id.in_(reactivated)).order_by(Exemplar.id)
                )
            )
    else:
        changes.created = list(
            await session.scalars(
                select(Exemplar).where(Exemplar.event_id == event.id).order_by(Exemplar.id)
            )
        )
        changes.ended = list(
            await session.scalars(
                select(Exemplar)
                .where(
                    Exemplar.movie_id == event.movie_id,
                    Exemplar.ended_at == event.created_at,
                    Exemplar.event_id.is_distinct_from(event.id),
                )
                .order_by(Exemplar.id)
            )
        )
    deploy_candidate_id = None
    if event.action in DEPLOYING and event.run_id is not None and event.chosen_source_file:
        deploy_candidate_id = await session.scalar(
            select(RunCandidate.id).where(
                RunCandidate.run_id == event.run_id,
                RunCandidate.source_file == event.chosen_source_file,
            )
        )
    return Outcome(
        event=event, changes=changes, replayed=True, deploy_candidate_id=deploy_candidate_id
    )


# ── Reading ───────────────────────────────────────────────────────────────


async def events_of_run(session: AsyncSession, run_id: int) -> list[PreferenceEvent]:
    """A run's events, and the revokes of them, oldest first."""
    own = select(PreferenceEvent.id).where(PreferenceEvent.run_id == run_id)
    rows = await session.scalars(
        select(PreferenceEvent)
        .where((PreferenceEvent.run_id == run_id) | PreferenceEvent.revokes_event_id.in_(own))
        .order_by(PreferenceEvent.id)
    )
    return list(rows)


async def revoked_ids(session: AsyncSession) -> dict[int, int]:
    """Revoked event id -> the id of the revoke that revoked it."""
    rows = await session.execute(
        select(PreferenceEvent.revokes_event_id, PreferenceEvent.id).where(
            PreferenceEvent.revokes_event_id.is_not(None)
        )
    )
    return {int(revoked): int(by) for revoked, by in rows}


# ── What the residual reads ───────────────────────────────────────────────


@dataclass(frozen=True)
class ResidualEvent:
    """An event in the shape ``marquee.ml.residual.build_residual_pairs`` reads."""

    id: str
    subject_kind: str
    subject_reference: str
    action: str
    exposed_candidates: list[dict[str, Any]]
    training_context: dict[str, Any]
    revoked_event_id: str | None
    """The revoke that revoked this event, if any: a revoked event gives no pairs."""


def residual_event(event: PreferenceEvent, revoked_by: int | None) -> ResidualEvent:
    return ResidualEvent(
        id=str(event.id),
        subject_kind=event.subject_kind,
        subject_reference=str(event.movie_id),
        action=event.action,
        exposed_candidates=list(event.exposed or []),
        training_context=dict(event.context or {}),
        revoked_event_id=str(revoked_by) if revoked_by is not None else None,
    )


async def residual_events(session: AsyncSession) -> list[ResidualEvent]:
    """Every event, oldest first, in the shape the residual reads."""
    revoked = await revoked_ids(session)
    events = await session.scalars(select(PreferenceEvent).order_by(PreferenceEvent.id))
    return [residual_event(event, revoked.get(event.id)) for event in events]
