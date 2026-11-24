"""Run and deployment responses. None of them carries a local path or Radarr's path."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marquee.api.schemas.jobs import JobItem
from marquee.db.models import Deployment, PosterRun, RunCandidate

RunState = Literal["running", "completed", "failed", "cancelled"]
Scope = Literal["never-run", "no-poster", "all"]


class RunItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    movie_id: int
    job_id: int | None
    state: RunState
    run_folder: str = Field(description="The run folder's name inside the runs directory")
    engine_run_id: str | None
    engine_status: str | None = Field(
        description="completed, or flagged_manual when no candidate survived every gate"
    )
    scorer_name: str | None
    ai_model: str | None
    profile_sha256: str | None = Field(description="The taste profile's hash when it started")
    counts: dict[str, Any] | None
    stages: list[dict[str, Any]] | None
    candidate_count: int | None
    ranked_count: int | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class CandidateItem(BaseModel):
    id: int
    source_file: str = Field(description="TMDB's file name")
    sha256: str | None = Field(description="Of the 500 px file; null if never downloaded")
    stage_reached: str | None
    gate_decision: str | None
    rejection_reason: str | None
    rank: int | None
    final_score: float | None
    stack: str | None = Field(description="Design stack and variant, e.g. 1A")
    is_pick: bool = Field(description="The run's recommendation: rank 1")
    image_url: str | None = Field(description="The 500 px file, with the API key")

    @classmethod
    def build(cls, run_id: int, row: RunCandidate) -> Self:
        stack = (
            f"{row.stack_rank}{row.stack_label or ''}"
            if row.stack_rank is not None
            else row.stack_label
        )
        return cls(
            id=row.id,
            source_file=row.source_file,
            sha256=row.sha256,
            stage_reached=row.stage_reached,
            gate_decision=row.gate_decision,
            rejection_reason=row.rejection_reason,
            rank=row.rank,
            final_score=row.final_score,
            stack=stack,
            is_pick=row.is_pick,
            image_url=(
                f"/api/runs/{run_id}/candidates/{row.id}/image" if row.sha256 is not None else None
            ),
        )


class RunDetail(RunItem):
    candidates: list[CandidateItem]

    @classmethod
    def build(cls, run: PosterRun, candidates: list[RunCandidate]) -> Self:
        base = RunItem.model_validate(run).model_dump()
        return cls(**base, candidates=[CandidateItem.build(run.id, c) for c in candidates])


class RunList(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[RunItem]


class BatchRequest(BaseModel):
    scope: Scope | None = Field(
        default=None,
        description="never-run: eligible movies with no run; no-poster: with no poster; "
        "all: every eligible movie",
    )
    movie_ids: list[int] | None = Field(default=None, min_length=1, max_length=1000)
    limit: int | None = Field(default=None, ge=1, description="With scope: at most this many")

    @model_validator(mode="after")
    def _one_of(self) -> Self:
        if (self.scope is None) == (self.movie_ids is None):
            raise ValueError("give either scope or movie_ids")
        if self.limit is not None and self.scope is None:
            raise ValueError("limit goes with scope")
        return self


class BatchQueued(BaseModel):
    batch: JobItem = Field(description="The batch's parent job")
    movies: int = Field(description="Movies the request covered")
    queued: int = Field(description="Runs queued; a movie with a run already active is left out")


class DeployRequest(BaseModel):
    candidate_id: int | None = Field(default=None, description="Default: the run's pick")


class DeploymentItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    movie_id: int
    action: Literal["deploy", "restore"]
    initiated_by: Literal["policy", "owner"]
    run_id: int | None
    candidate_id: int | None
    source_file: str | None
    job_id: int | None
    file_name: str
    new_sha256: str | None = Field(description="Null when the write removed the poster")
    new_size_bytes: int | None
    previous_sha256: str | None = Field(description="Null when there was no poster")
    previous_origin: Literal["library", "marquee"] | None
    backup_file: str | None = Field(description="Relative to the backups directory")
    restores_deployment_id: int | None
    created_at: datetime

    @classmethod
    def build(cls, row: Deployment) -> Self:
        return cls.model_validate(row)


class DeploymentList(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[DeploymentItem]
