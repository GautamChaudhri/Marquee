"""Review, feedback, exemplar, and taste responses. None of them carries a path."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marquee.api.schemas.library import Poster
from marquee.db.models import Exemplar, PreferenceEvent
from marquee.db.models.taste import MAX_EXPOSED, MAX_IDEMPOTENCY_KEY

FeedbackAction = Literal["approval", "override", "rank", "reject"]
EventAction = Literal["approval", "override", "rank", "reject", "revoke"]
ExemplarState = Literal["active", "superseded", "revoked"]
Polarity = Literal["positive", "negative"]


class FeedbackRequest(BaseModel):
    """One act of feedback on a run. Candidates are named by their ids in the run."""

    model_config = ConfigDict(extra="forbid")

    action: FeedbackAction
    candidate_id: int | None = Field(default=None, description="override: the one chosen")
    order: list[int] | None = Field(
        default=None, max_length=MAX_EXPOSED, description="rank: best first"
    )
    hated: list[int] | None = Field(
        default=None,
        max_length=MAX_EXPOSED,
        description="rank or reject: the hate pile (reject: every shown one by default)",
    )
    shown: list[int] | None = Field(
        default=None,
        max_length=MAX_EXPOSED,
        description="What the owner was shown, in that order; default every ranked candidate",
    )
    deploy: bool = Field(
        default=True, description="Deploy the chosen poster (approval, override, rank)"
    )
    idempotency_key: str = Field(
        min_length=1,
        max_length=MAX_IDEMPOTENCY_KEY,
        description="Sent again with the same request, it returns the same event (200)",
    )

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.action == "override" and self.candidate_id is None:
            raise ValueError("an override names candidate_id")
        if self.action != "override" and self.candidate_id is not None:
            raise ValueError("only an override names candidate_id")
        if self.action == "rank" and not self.order:
            raise ValueError("a rank gives an order")
        if self.action not in ("rank", "reject") and (self.order or self.hated):
            raise ValueError("only rank and reject take order or hated")
        if self.action == "reject" and self.order:
            raise ValueError("a reject takes no order")
        return self


class RevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_IDEMPOTENCY_KEY,
        description="Default revoke:<event id>, so asking twice returns the same revoke",
    )


class RebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = Field(default=False, description="Build even when the profile is up to date")


class EventItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    movie_id: int
    run_id: int | None
    action: EventAction
    chosen_source_file: str | None = Field(description="TMDB's file name of the one chosen")
    chosen_sha256: str | None
    pick_source_file: str | None = Field(description="The run's pick, when the event was made")
    profile_sha256: str | None
    scorer_name: str | None
    blind: bool | None = Field(
        description="The run's profile had no positive exemplar of the movie; null: unknown"
    )
    exposed: list[dict[str, Any]] = Field(description="Every candidate shown, as it was")
    context: dict[str, Any] = Field(description="order, hated, selected_candidate")
    revokes_event_id: int | None
    revoked_by: int | None = Field(default=None, description="The revoke that undid this one")
    created_at: datetime

    @classmethod
    def build(cls, event: PreferenceEvent, revoked_by: int | None = None) -> Self:
        item = cls.model_validate(event)
        item.revoked_by = revoked_by
        return item


class ExemplarItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    movie_id: int
    title: str = Field(description="The movie's title")
    year: int | None = Field(description="The movie's year")
    polarity: Polarity
    weight: float
    source: Literal["seed", "feedback"]
    event_id: int | None
    state: ExemplarState
    sha256: str
    staged_name: str | None = Field(description="The name the trainer sees; positives only")
    source_file: str | None = Field(description="TMDB's file name; null for a seed exemplar")
    replaced_exemplar_id: int | None
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None
    ended_by_event_id: int | None = Field(
        description="The act of feedback that ended it; null while it is active, and for one "
        "ended before that was recorded"
    )
    image_url: str

    @classmethod
    def build(cls, row: Exemplar, names: Mapping[int, tuple[str, int | None]]) -> Self:
        """``names``: each movie's title and year by id (``library.queries.movie_names``)."""
        title, year = names[row.movie_id]
        values = {
            name: getattr(row, name)
            for name in cls.model_fields
            if name not in ("image_url", "title", "year")
        }
        return cls(
            **values, title=title, year=year, image_url=f"/api/taste/exemplars/{row.id}/image"
        )


class ExemplarList(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ExemplarItem]


class ExemplarChanges(BaseModel):
    created: list[ExemplarItem]
    ended: list[ExemplarItem]
    reactivated: list[ExemplarItem]


class FeedbackJobs(BaseModel):
    deploy: int | None = Field(description="The poster_deploy job, if one was queued")
    rebuild: int | None = Field(description="The taste_rebuild job, if one was queued")


class FeedbackResult(BaseModel):
    event: EventItem
    exemplars: ExemplarChanges
    jobs: FeedbackJobs
    deploy_note: str | None = Field(description="Why no deploy was queued, when it was refused")


class EventList(BaseModel):
    items: list[EventItem]


class FeedbackListItem(BaseModel):
    """One act of feedback, as a list shows it: what was done, to which movie, and when.
    The candidates it showed are counted, not copied (``GET /api/runs/{id}/feedback`` has
    them)."""

    id: int
    movie_id: int
    title: str
    year: int | None
    run_id: int | None
    action: EventAction
    chosen_source_file: str | None
    pick_source_file: str | None
    blind: bool | None
    shown: int = Field(description="How many candidates the owner was shown")
    context: dict[str, Any] = Field(description="order, hated, selected_candidate")
    revokes_event_id: int | None
    revoked_by: int | None = Field(description="The revoke that undid this one")
    created_at: datetime

    @classmethod
    def build(
        cls, event: PreferenceEvent, title: str, year: int | None, revoked_by: int | None
    ) -> Self:
        return cls(
            id=event.id,
            movie_id=event.movie_id,
            title=title,
            year=year,
            run_id=event.run_id,
            action=event.action,  # type: ignore[arg-type]
            chosen_source_file=event.chosen_source_file,
            pick_source_file=event.pick_source_file,
            blind=event.blind,
            shown=len(event.exposed or []),
            context=dict(event.context or {}),
            revokes_event_id=event.revokes_event_id,
            revoked_by=revoked_by,
            created_at=event.created_at,
        )


class FeedbackList(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[FeedbackListItem]


class PendingItem(BaseModel):
    movie_id: int
    title: str
    year: int | None
    run_id: int
    ranked: int | None
    finished_at: datetime | None
    policy_deployed: bool = Field(description="The deploy policy deployed this run's pick")
    blind: bool | None
    poster: Poster | None = Field(description="The movie's poster now, as a movie item has it")
    pick_image_url: str | None = Field(
        description="The run's pick, its 500 px file (null when it was never downloaded)"
    )


class PendingList(BaseModel):
    items: list[PendingItem]


class TallyItem(BaseModel):
    reviewed: int
    agreed: int
    top3: int
    percent: float | None = Field(description="Only from 10 reviews")


class AgreementItem(BaseModel):
    blind: TallyItem = Field(description="The first unrevoked review of each blind run")
    by_profile: dict[str, TallyItem]
    by_month: dict[str, TallyItem]
    not_blind: TallyItem = Field(description="Counted apart, never in the agreement")
    unknown: TallyItem = Field(description="Runs whose profile is not registered")


class ProfileItem(BaseModel):
    id: int
    sha256: str
    source: Literal["seed", "rebuild"]
    revision: str
    positives: int
    negatives: int
    created_at: datetime


class ResidualItem(BaseModel):
    subjects: int
    pairs: int
    min_subjects: int
    min_pairs: int
    trained: bool


class TasteStatus(BaseModel):
    exemplars: dict[str, dict[str, int]] = Field(description="state -> polarity -> count")
    profile: ProfileItem | None
    revision: str = Field(description="The active exemplars' revision")
    rebuild_due: bool
    agreement: AgreementItem
    residual: ResidualItem
    pending_reviews: int
