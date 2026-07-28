"""Retry capability is definition-owned, never guessed from enabled state.

Whether a terminal job can be retried is declared by its definition and surfaced by its
presenter. Covers explicit retry truth for every registered definition, the bounded
canonical retry request, rejection of non-terminal and non-retryable outcomes, and the
public route denying retry of unsafe terminal jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.contracts import JobAction
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.policies import RetryMode
from marquee.core.jobs.presenters.base import present_actions
from marquee.core.jobs.retry_capability import resolve_retry_capability
from marquee.main import app
from marquee.models.job import Job


def _terminal_job(job_type: str, *, request: dict | None = None) -> Job:
    definition = JOB_DEFINITION_REGISTRY.get(job_type)
    subject_kind = next(iter(definition.subject_kinds), None) or "system"
    job_id = uuid4().hex
    return Job(
        id=job_id,
        root_id=job_id,
        type=job_type,
        payload_version=1,
        request=request if request is not None else {},
        phase="terminal",
        outcome="failed",
        desired_state="run",
        terminal_at=datetime.now(UTC),
        subject_kind=subject_kind,
        subject_reference="retry-subject" if subject_kind != "system" else None,
        subject_snapshot={},
    )


def test_every_registered_definition_has_explicit_retry_truth_in_presenter() -> None:
    """The registry, not `enabled`, decides what a terminal retry action means."""
    modes = {definition.retry_mode for definition in JOB_DEFINITION_REGISTRY}
    assert modes == {
        RetryMode.UNSUPPORTED,
        RetryMode.GENERIC,
        RetryMode.DOMAIN_COORDINATED,
    }

    for definition in JOB_DEFINITION_REGISTRY:
        request = {"echo": "retry"} if definition.job_type == "system_noop" else {}
        job = _terminal_job(definition.job_type, request=request)
        capability = resolve_retry_capability(job, definition)
        actions = present_actions(job, definition)
        assert (JobAction.RETRY in actions) is capability.available
        if definition.retry_mode == RetryMode.UNSUPPORTED:
            assert capability.available is False
            assert capability.reason in {
                "definition_retry_unsupported",
                "definition_is_not_enabled",
            }
        elif definition.retry_mode == RetryMode.GENERIC:
            assert capability.available is True
        else:
            assert capability.available is True


def test_generic_retry_requires_the_canonical_subject_and_bounded_request() -> None:
    definition = JOB_DEFINITION_REGISTRY.get("system_noop")
    missing_subject = _terminal_job("system_noop", request={"echo": "retry"})
    missing_subject.subject_reference = None
    assert (
        resolve_retry_capability(missing_subject, definition).reason
        == "bounded_retry_subject_unavailable"
    )

    invalid_request = _terminal_job("system_noop", request={"unbounded": object()})
    assert (
        resolve_retry_capability(invalid_request, definition).reason
        == "bounded_retry_request_unavailable"
    )


def test_retry_resolver_rejects_nonterminal_and_nonretryable_outcomes() -> None:
    definition = JOB_DEFINITION_REGISTRY.get("system_noop")
    active = _terminal_job("system_noop", request={"echo": "retry"})
    active.phase = "running"
    assert resolve_retry_capability(active, definition).reason == "retry_requires_terminal_job"

    for outcome in ("succeeded", "no_change", "superseded", "unsafe", "dead_letter"):
        terminal = _terminal_job("system_noop", request={"echo": "retry"})
        terminal.outcome = outcome
        assert (
            resolve_retry_capability(terminal, definition).reason
            == "terminal_outcome_is_not_retryable"
        )


@pytest.mark.asyncio
async def test_public_retry_denies_unsafe_terminal_jobs_for_every_definition(
    db: AsyncSession,
) -> None:
    """Every registered definition reaches the same resolver through the public command."""
    jobs = [_terminal_job(definition.job_type) for definition in JOB_DEFINITION_REGISTRY]
    for job in jobs:
        job.outcome = "unsafe"
    db.add_all(jobs)
    await db.commit()
    before_count = await db.scalar(select(func.count()).select_from(Job))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        for job in jobs:
            response = await client.post(
                f"/api/jobs/{job.id}/retry",
                json={"expected_fence_token": job.fence_token},
            )
            assert response.status_code == 409, response.text

    await db.rollback()
    assert await db.scalar(select(func.count()).select_from(Job)) == before_count
