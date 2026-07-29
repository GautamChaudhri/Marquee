"""Single definition-owned truth for user retry presentation and execution."""

from __future__ import annotations

from dataclasses import dataclass

from marquee.core.jobs.contracts import EffectSafety
from marquee.core.jobs.definitions import JobDefinition
from marquee.core.jobs.policies import RetryMode
from marquee.models.job import Job


@dataclass(frozen=True, slots=True)
class RetryCapability:
    available: bool
    mode: RetryMode
    reason: str | None = None


def resolve_retry_capability(job: Job, definition: JobDefinition) -> RetryCapability:
    """Resolve the one retry decision from immutable definition and canonical state."""
    mode = definition.retry_mode
    if not definition.action_policy.retry:
        return RetryCapability(False, mode, "definition_retry_unsupported")
    if job.phase != "terminal":
        return RetryCapability(False, mode, "retry_requires_terminal_job")
    partial_group = (
        job.type == "poster_pipeline_group"
        and job.outcome == "partially_succeeded"
        and isinstance(job.result, dict)
        and isinstance(job.result.get("failed_count"), int)
        and job.result["failed_count"] > 0
    )
    partial_parent = definition.parent_policy is not None and job.outcome == "partially_succeeded"
    if job.outcome not in {"failed", "cancelled"} and not partial_group and not partial_parent:
        return RetryCapability(False, mode, "terminal_outcome_is_not_retryable")
    if not definition.enabled and definition.parent_policy is None:
        return RetryCapability(False, mode, "definition_is_not_enabled")
    if mode == RetryMode.UNSUPPORTED:
        return RetryCapability(False, mode, "definition_retry_unsupported")
    if mode == RetryMode.GENERIC:
        if definition.effect_safety == EffectSafety.UNSAFE_MUTATION:
            return RetryCapability(False, mode, "generic_retry_effect_is_not_replay_safe")
        if (
            not job.subject_kind
            or job.subject_kind not in definition.subject_kinds
            or not job.subject_reference
        ):
            return RetryCapability(False, mode, "bounded_retry_subject_unavailable")
        try:
            definition.request.validate(job.request, version=job.payload_version)
        except (TypeError, ValueError):
            return RetryCapability(False, mode, "bounded_retry_request_unavailable")
        return RetryCapability(True, mode)
    if mode == RetryMode.DOMAIN_COORDINATED:
        return RetryCapability(True, mode)
    return RetryCapability(False, mode, "definition_retry_unsupported")
