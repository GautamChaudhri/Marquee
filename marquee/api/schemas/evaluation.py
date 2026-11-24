"""Blind evaluation responses. None of them carries a path."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from marquee.api.schemas.jobs import JobItem

Outcome = Literal["ranked", "gated", "not_offered", "failed"]
ExemplarSource = Literal["seed", "feedback"]


class EvaluationTally(BaseModel):
    evaluated: int
    ranked: int = Field(description="The owner's poster was among the ranked candidates")
    gated: int = Field(description="The owner's poster was a candidate a gate removed")
    gated_by: dict[str, int] = Field(description='"stage: reason" -> how many')
    not_offered: int = Field(description="No candidate was the owner's poster")
    failed: int = Field(description="The masked run did not complete")
    exact: int = Field(description="Of the ranked: the blind pick is the owner's poster")
    same_design: int = Field(description="Of the ranked: the pick is the owner's design")
    top3: int = Field(description="Of the ranked: the owner's design is in the first 3 cards")
    top5: int
    median_card: float | None
    mean_reciprocal_card: float | None


class EvaluationSummary(BaseModel):
    seed: EvaluationTally = Field(description="Posters the owner picked by hand: the headline")
    feedback: EvaluationTally = Field(description="Exemplars chosen from what Marquee showed")
    all: EvaluationTally


class EvaluationItem(BaseModel):
    id: int
    job_id: int | None = Field(description="The batch's parent job")
    state: str = Field(description="The batch's state: running, succeeded, failed, cancelled")
    profile_id: int | None
    profile_sha256: str
    residual_id: int | None
    scorer: Literal["weighted", "residual"] = Field(
        description="What the runs were expected to use when it was queued"
    )
    movies: int
    queued: int
    results: int = Field(description="Movies with a result so far")
    not_run: int = Field(description="Queued runs that ended without a result (cancelled)")
    progress: dict[str, int] = Field(description="The batch's children by state")
    created_at: datetime
    finished_at: datetime | None
    summary: EvaluationSummary


class EvaluationList(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[EvaluationItem]


class EvaluationRequest(BaseModel):
    movie_ids: list[int] | None = Field(default=None, min_length=1, max_length=1000)
    limit: int | None = Field(default=None, ge=1, description="At most this many movies")


class EvaluationQueued(BaseModel):
    evaluation: EvaluationItem
    batch: JobItem


class EvaluationResultItem(BaseModel):
    movie_id: int
    title: str
    year: int | None
    exemplar_id: int | None
    exemplar_source: ExemplarSource | None
    exemplar_image_url: str | None = Field(description="The owner's poster, as kept")
    run_id: int | None = Field(description="The masked run; null once a newer one replaced it")
    outcome: Outcome
    matched_by: Literal["file", "cosine"] | None
    match_file: str | None
    match_cosine: float | None
    best_cosine: float | None
    best_file: str | None
    unembedded: int | None
    rank: int | None
    ranked_count: int | None
    card: int | None
    cards: int | None
    pick_file: str | None
    pick_is_match: bool | None
    same_design: bool | None
    stage: str | None
    reason: str | None
    match_image_url: str | None
    pick_image_url: str | None


class EvaluationResultList(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[EvaluationResultItem]


class EvaluationHeadline(BaseModel):
    """The latest evaluation that succeeded, the one that succeeded before it for
    comparison, and one running. A cancelled or failed evaluation is neither headline."""

    latest: EvaluationItem | None
    previous: EvaluationItem | None
    running: EvaluationItem | None
