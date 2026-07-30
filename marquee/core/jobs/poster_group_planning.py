"""Deterministic planning for chunked and all-at-once poster groups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marquee.core.jobs.poster_group_limits import MAX_POSTER_GROUP_MEMBERS

PosterGroupBatchMode = Literal["chunked", "all_at_once"]


@dataclass(frozen=True, slots=True)
class PosterGroupPlan:
    mode: PosterGroupBatchMode
    selection_count: int
    target_size: int
    hard_group_size: int

    @property
    def all_at_once(self) -> bool:
        return self.mode == "all_at_once"


@dataclass(frozen=True, slots=True)
class PosterGroupExecutionOptions:
    enabled: bool
    mode: PosterGroupBatchMode
    chunk_size: int


def resolve_poster_group_execution_options(
    *,
    requested_mode: str | None,
    requested_chunk_size: int | None,
    configured_enabled: bool,
    configured_mode: str,
    configured_chunk_size: int,
) -> PosterGroupExecutionOptions:
    """Resolve request-scoped overrides ahead of the database-owned defaults."""
    enabled = configured_enabled or requested_mode is not None or requested_chunk_size is not None
    if not enabled:
        return PosterGroupExecutionOptions(enabled=False, mode="chunked", chunk_size=8)

    mode = requested_mode or configured_mode
    if mode not in {"chunked", "all_at_once"}:
        raise ValueError("poster group batch mode must be chunked or all_at_once")
    chunk_size = int(
        requested_chunk_size if requested_chunk_size is not None else configured_chunk_size
    )
    if not 1 <= chunk_size <= 16:
        raise ValueError("poster group chunk size must be between 1 and 16")
    return PosterGroupExecutionOptions(
        enabled=True,
        mode=mode,
        chunk_size=chunk_size,
    )


def build_poster_group_plan(*, selection_count: int, mode: str, chunk_size: int) -> PosterGroupPlan:
    """Resolve one bounded policy without silently changing requested semantics."""
    if selection_count < 1:
        raise ValueError("poster groups require at least one selected subject")
    if mode not in {"chunked", "all_at_once"}:
        raise ValueError("poster group batch mode must be chunked or all_at_once")
    if mode == "all_at_once":
        if selection_count > MAX_POSTER_GROUP_MEMBERS:
            raise ValueError(
                "all-at-once poster groups support at most "
                f"{MAX_POSTER_GROUP_MEMBERS} selected subjects"
            )
        return PosterGroupPlan(
            mode="all_at_once",
            selection_count=selection_count,
            target_size=selection_count,
            hard_group_size=selection_count,
        )
    bounded_chunk_size = min(16, max(1, int(chunk_size)))
    return PosterGroupPlan(
        mode="chunked",
        selection_count=selection_count,
        target_size=bounded_chunk_size,
        hard_group_size=16,
    )


def linear_poster_groups[T](values: list[T], plan: PosterGroupPlan) -> list[list[T]]:
    """Split a stable sequence according to a resolved poster-group plan."""
    return [
        values[offset : offset + plan.target_size]
        for offset in range(0, len(values), plan.target_size)
    ]
