"""How often the owner kept the AI's pick, what is waiting for review, and the taste status.

Agreement (decision 10 of the M5b brief) is counted over feedback that is not revoked, on
blind runs only (made while the movie had no positive exemplar in the run's profile), the
first such event of each run. A review agrees when the chosen file is the run's pick; it is
"in the top 3" when the chosen candidate's rank was 3 or better; a ``reject`` is reviewed
and neither. Reviews of runs that were not blind, or whose profile is not registered, are
counted apart and never mixed in. A percentage is given only from
:data:`MIN_FOR_PERCENT` reviews; below that, counts alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import pipeline_settings
from marquee.db.models import (
    Artwork,
    Deployment,
    Exemplar,
    Movie,
    PosterRun,
    PreferenceEvent,
    RunCandidate,
    TasteProfile,
)
from marquee.db.models.deployment import INITIATED_BY_POLICY
from marquee.db.models.run import RUN_COMPLETED, in_product_loop
from marquee.db.models.taste import ACTION_REJECT, ACTION_REVOKE
from marquee.library.queries import posters_of
from marquee.taste import exemplars, feedback, rebuild

MIN_FOR_PERCENT = 10
TOP = 3


@dataclass
class Tally:
    reviewed: int = 0
    agreed: int = 0
    top3: int = 0

    @property
    def percent(self) -> float | None:
        """Agreed as a percentage, only from :data:`MIN_FOR_PERCENT` reviews."""
        if self.reviewed < MIN_FOR_PERCENT:
            return None
        return round(100 * self.agreed / self.reviewed, 1)

    def text(self) -> str:
        if not self.reviewed:
            return "0 reviewed"
        share = f" ({self.percent:.0f}%)" if self.percent is not None else ""
        return (
            f"{self.agreed} of {self.reviewed} agreed{share}, "
            f"{self.top3} of {self.reviewed} in the top {TOP}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewed": self.reviewed,
            "agreed": self.agreed,
            "top3": self.top3,
            "percent": self.percent,
        }


@dataclass
class Agreement:
    blind: Tally = field(default_factory=Tally)
    by_profile: dict[int | str, Tally] = field(default_factory=dict)
    by_month: dict[str, Tally] = field(default_factory=dict)
    not_blind: Tally = field(default_factory=Tally)
    unknown: Tally = field(default_factory=Tally)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blind": self.blind.to_dict(),
            "by_profile": {str(k): v.to_dict() for k, v in self.by_profile.items()},
            "by_month": {k: v.to_dict() for k, v in self.by_month.items()},
            "not_blind": self.not_blind.to_dict(),
            "unknown": self.unknown.to_dict(),
        }


def _chosen_rank(event: PreferenceEvent) -> int | None:
    """The chosen candidate's rank in the run, as the owner saw it. Events from before M5c
    have only ``baseline_rank``, which was the run's rank then."""
    for row in event.exposed or []:
        if isinstance(row, dict) and row.get("candidate_id") == event.chosen_source_file:
            rank = row.get("rank", row.get("baseline_rank"))
            return rank if isinstance(rank, int) and not isinstance(rank, bool) else None
    return None


def _count(tally: Tally, event: PreferenceEvent) -> None:
    tally.reviewed += 1
    if event.action == ACTION_REJECT or event.chosen_source_file is None:
        return
    if event.chosen_source_file == event.pick_source_file:
        tally.agreed += 1
    rank = _chosen_rank(event)
    if rank is not None and rank <= TOP:
        tally.top3 += 1


def first_reviews(events: list[PreferenceEvent], revoked: set[int]) -> list[PreferenceEvent]:
    """The first event of each run that is feedback and not revoked, oldest first."""
    seen: set[int] = set()
    out: list[PreferenceEvent] = []
    for event in sorted(events, key=lambda e: e.id):
        if event.action == ACTION_REVOKE or event.id in revoked:
            continue
        run = event.run_id if event.run_id is not None else -event.id
        if run in seen:
            continue
        seen.add(run)
        out.append(event)
    return out


async def agreement(session: AsyncSession) -> Agreement:
    """Decision 10, over every recorded event."""
    events = list(await session.scalars(select(PreferenceEvent).order_by(PreferenceEvent.id)))
    revoked = set(await feedback.revoked_ids(session))
    profiles = dict((await session.execute(select(TasteProfile.sha256, TasteProfile.id))).all())
    result = Agreement()
    for event in first_reviews(events, revoked):
        if event.blind is None:
            _count(result.unknown, event)
            continue
        if not event.blind:
            _count(result.not_blind, event)
            continue
        _count(result.blind, event)
        profile = profiles.get(event.profile_sha256 or "", (event.profile_sha256 or "?")[:12])
        _count(result.by_profile.setdefault(profile, Tally()), event)
        _count(result.by_month.setdefault(event.created_at.strftime("%Y-%m"), Tally()), event)
    return result


# ── What is waiting for review ────────────────────────────────────────────


@dataclass(frozen=True)
class Pending:
    movie_id: int
    title: str
    year: int | None
    run_id: int
    ranked: int | None
    finished_at: datetime | None
    policy_deployed: bool
    blind: bool | None
    poster: Artwork | None = None
    """The movie's poster row now, if it has one."""
    pick_id: int | None = None
    """The run's pick, when its file was downloaded (so its image can be shown)."""


async def pending_reviews(session: AsyncSession, *, limit: int | None = None) -> list[Pending]:
    """Movies whose latest completed run has no feedback that is not revoked; those the
    deploy policy deployed first, then the newest runs."""
    latest = (
        select(PosterRun.movie_id, func.max(PosterRun.id).label("run_id"))
        .where(PosterRun.state == RUN_COMPLETED, in_product_loop())
        .group_by(PosterRun.movie_id)
        .subquery()
    )
    revokes = select(PreferenceEvent.revokes_event_id).where(
        PreferenceEvent.revokes_event_id.is_not(None)
    )
    reviewed = exists(
        select(PreferenceEvent.id).where(
            PreferenceEvent.run_id == latest.c.run_id,
            PreferenceEvent.action != ACTION_REVOKE,
            PreferenceEvent.id.not_in(revokes),
        )
    )
    policy = exists(
        select(Deployment.id).where(
            Deployment.run_id == latest.c.run_id,
            Deployment.initiated_by == INITIATED_BY_POLICY,
            Deployment.action == "deploy",
        )
    ).label("policy")
    statement = (
        select(Movie, PosterRun, policy)
        .join(latest, latest.c.movie_id == Movie.id)
        .join(PosterRun, PosterRun.id == latest.c.run_id)
        .where(~reviewed, Movie.is_present.is_(True))
        .order_by(policy.desc(), PosterRun.finished_at.desc().nulls_last(), Movie.sort_title)
    )
    if limit is not None:
        statement = statement.limit(limit)
    rows = (await session.execute(statement)).all()
    posters = await posters_of(session, [movie.id for movie, _, _ in rows])
    picks: dict[int, int] = {}
    if rows:
        found = await session.execute(
            select(RunCandidate.run_id, RunCandidate.id).where(
                RunCandidate.run_id.in_([run.id for _, run, _ in rows]),
                RunCandidate.is_pick.is_(True),
                RunCandidate.sha256.is_not(None),
            )
        )
        picks = {row.run_id: row.id for row in found}
    out = []
    for movie, run, deployed in rows:
        out.append(
            Pending(
                movie_id=movie.id,
                title=movie.title,
                year=movie.year,
                run_id=run.id,
                ranked=run.ranked_count,
                finished_at=run.finished_at,
                policy_deployed=bool(deployed),
                blind=await feedback.blind_for(session, run),
                poster=posters.get(movie.id),
                pick_id=picks.get(run.id),
            )
        )
    return out


@dataclass(frozen=True)
class EventPage:
    total: int
    items: list[tuple[PreferenceEvent, str, int | None]]
    """Each event with its movie's title and year."""


async def list_events(
    session: AsyncSession, *, movie_id: int | None = None, page: int = 1, page_size: int = 50
) -> EventPage:
    """Preference events, newest first, revokes included, with each movie's title and year."""
    conditions = [] if movie_id is None else [PreferenceEvent.movie_id == movie_id]
    total = await session.scalar(
        select(func.count()).select_from(PreferenceEvent).where(*conditions)
    )
    rows = await session.execute(
        select(PreferenceEvent, Movie.title, Movie.year)
        .join(Movie, Movie.id == PreferenceEvent.movie_id)
        .where(*conditions)
        .order_by(PreferenceEvent.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    return EventPage(total=total or 0, items=[(e, title, year) for e, title, year in rows])


async def run_reviewed(session: AsyncSession, run_id: int) -> bool:
    """Whether the run has feedback that is not revoked."""
    revokes = select(PreferenceEvent.revokes_event_id).where(
        PreferenceEvent.revokes_event_id.is_not(None)
    )
    found = await session.scalar(
        select(PreferenceEvent.id)
        .where(
            and_(
                PreferenceEvent.run_id == run_id,
                PreferenceEvent.action != ACTION_REVOKE,
                PreferenceEvent.id.not_in(revokes),
            )
        )
        .limit(1)
    )
    return found is not None


# ── The status ────────────────────────────────────────────────────────────


@dataclass
class Status:
    exemplars: dict[str, dict[str, int]]
    """state -> polarity -> count."""
    profile: TasteProfile | None
    revision: str
    rebuild_due: bool
    agreement: Agreement
    residual_subjects: int
    residual_pairs: int
    residual_min_subjects: int
    residual_min_pairs: int
    pending_reviews: int
    residual: dict[str, Any] = field(default_factory=dict)
    """What :func:`marquee.taste.residuals.state` says."""

    def to_dict(self) -> dict[str, Any]:
        profile = self.profile
        return {
            "exemplars": self.exemplars,
            "profile": (
                {
                    "id": profile.id,
                    "sha256": profile.sha256,
                    "source": profile.source,
                    "revision": profile.revision,
                    "positives": profile.positives,
                    "negatives": profile.negatives,
                    "created_at": profile.created_at.isoformat(),
                }
                if profile is not None
                else None
            ),
            "revision": self.revision,
            "rebuild_due": self.rebuild_due,
            "agreement": self.agreement.to_dict(),
            "residual": {
                **self.residual,
                "subjects": self.residual_subjects,
                "pairs": self.residual_pairs,
                "min_subjects": self.residual_min_subjects,
                "min_pairs": self.residual_min_pairs,
                "trained": bool(self.residual.get("active")),
            },
            "pending_reviews": self.pending_reviews,
        }


async def status(session: AsyncSession) -> Status:
    """Exemplars by state and polarity, the active profile and whether a rebuild is due,
    agreement, and the residual: what the events give it and its state."""
    from marquee.ml.residual import build_residual_pairs
    from marquee.taste import residuals

    counts: dict[str, dict[str, int]] = {}
    rows = await session.execute(
        select(Exemplar.state, Exemplar.polarity, func.count()).group_by(
            Exemplar.state, Exemplar.polarity
        )
    )
    for state, polarity, count in rows:
        counts.setdefault(state, {})[polarity] = int(count)
    profile = await rebuild.active_profile(session)
    revision = await exemplars.revision(session)
    pairs = build_residual_pairs(await feedback.residual_events(session))
    return Status(
        exemplars=counts,
        profile=profile,
        revision=revision,
        rebuild_due=(
            profile.revision != revision
            if profile is not None
            else any(by.get("positive") for state, by in counts.items() if state == "active")
        ),
        agreement=await agreement(session),
        residual_subjects=len({pair.subject for pair in pairs}),
        residual_pairs=len(pairs),
        residual_min_subjects=pipeline_settings.RESIDUAL_MIN_SUBJECTS,
        residual_min_pairs=pipeline_settings.RESIDUAL_MIN_PAIRS,
        pending_reviews=len(await pending_reviews(session)),
        residual=await residuals.state(session),
    )
