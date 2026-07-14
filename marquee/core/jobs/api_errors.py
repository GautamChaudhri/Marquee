"""Frozen typed error detail envelope for the canonical job APIs.

Follows the established `HTTPException(status, detail={"code": ..., ...})`
convention used by the versioned-configuration routes: the response body is
`{"detail": <JobApiErrorDetail>}`.  Conflict responses carry the current
canonical state so an optimistic client can reconcile without a second read.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from marquee.core.jobs.documents import StrictDocument


class JobApiErrorDetail(StrictDocument):
    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)
    job_id: str | None = Field(default=None, max_length=32)
    current_phase: str | None = Field(default=None, max_length=40)
    current_outcome: str | None = Field(default=None, max_length=40)
    current_desired_state: str | None = Field(default=None, max_length=40)
    current_version: int | None = Field(default=None, ge=0)
    context: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def bound_context(
        cls, value: dict[str, str | int | float | bool | None]
    ) -> dict[str, str | int | float | bool | None]:
        if len(value) > 16:
            raise ValueError("error context is bounded to 16 entries")
        for key, item in value.items():
            lowered = key.lower()
            if len(key) > 80 or any(
                token in lowered for token in ("secret", "token", "password")
            ):
                raise ValueError("error context keys must be bounded and non-secret")
            if isinstance(item, str) and len(item) > 300:
                raise ValueError("error context strings are bounded")
        return value


# Frozen command/read error codes; additions are explicit API changes.
ERROR_JOB_NOT_FOUND = "job_not_found"
ERROR_STALE_JOB_VERSION = "stale_job_version"
ERROR_ACTION_NOT_ALLOWED = "action_not_allowed"
ERROR_INVALID_CURSOR = "invalid_cursor"
ERROR_INVALID_FILTER = "invalid_filter"
ERROR_UNMIGRATED = "unmigrated_job_command"
