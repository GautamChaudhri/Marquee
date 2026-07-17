"""Identity-safe bounded reconciliation of unfinished attempts at worker startup."""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import select

from marquee.core.jobs.contracts import EffectSafety
from marquee.core.jobs.fenced_writer import AttemptOwnership, FencedWriter
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.process_identity import (
    CgroupV2Handle,
    IdentityStatus,
    ProcessIdentity,
    ProcessIdentityError,
    process_group_exists,
    read_boot_id,
    verify_process_identity,
)
from marquee.database import _get_session_factory
from marquee.models import RuntimeInstance
from marquee.models.job import Job, JobAttempt


@dataclass(frozen=True, slots=True)
class OrphanCandidate:
    ownership: AttemptOwnership
    job_type: str
    identity: ProcessIdentity | None
    partial_identity: bool
    runtime_instance_id: str | None
    runtime_observed: bool
    runtime_fresh: bool
    runtime_same_host: bool
    publication_started: bool


RecoveryDisposition = Literal["active", "interrupted", "unsafe", "stale"]
RecoveryAssessment = Literal["active", "recoverable", "unsafe"]


@dataclass(frozen=True, slots=True)
class OrphanAssessment:
    disposition: RecoveryAssessment
    reason: str
    proven_dead: bool = False


def _candidate_from_row(
    job: Job,
    attempt: JobAttempt,
    runtime: RuntimeInstance | None,
    *,
    now: datetime,
    current_boot: str,
) -> OrphanCandidate:
    values = (
        attempt.process_id,
        attempt.process_group_id,
        attempt.host_boot_id,
        (attempt.metrics or {}).get("process_start_ticks"),
    )
    present = tuple(value is not None for value in values)
    identity = None
    if all(present):
        process_id, process_group_id, host_boot_id, process_start_ticks = values
        assert process_id is not None
        assert process_group_id is not None
        assert host_boot_id is not None
        assert process_start_ticks is not None
        identity = ProcessIdentity(
            worker_node=attempt.worker_node_id or "unknown",
            host_boot_id=str(host_boot_id),
            pid=int(process_id),
            process_group_id=int(process_group_id),
            process_start_ticks=int(process_start_ticks),
            cgroup_path=attempt.cgroup_path,
        )
    return OrphanCandidate(
        ownership=AttemptOwnership(
            job_id=job.id,
            attempt_id=attempt.id,
            fence_token=attempt.fence_token,
            dispatch_generation=job.dispatch_generation,
        ),
        job_type=job.type,
        identity=identity,
        partial_identity=any(present) and not all(present),
        runtime_instance_id=attempt.runtime_instance_id,
        runtime_observed=runtime is not None,
        runtime_fresh=bool(
            runtime is not None
            and runtime.stopped_at is None
            and runtime.readiness != "stopped"
            and runtime.heartbeat_expires_at > now
        ),
        runtime_same_host=bool(
            (runtime is not None and runtime.host_boot_id == current_boot)
            or (identity is not None and identity.host_boot_id == current_boot)
        ),
        publication_started=bool(
            (attempt.metrics or {}).get("publish_intent") is not None
            or (attempt.metrics or {}).get("publication") is not None
        ),
    )


async def _bounded_candidates(worker_node: str, *, limit: int) -> tuple[OrphanCandidate, ...]:
    """Return the oldest global unfinished attempts; ``worker_node`` is diagnostic only."""
    del worker_node
    now = datetime.now(UTC)
    current_boot = read_boot_id()
    factory = _get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(Job, JobAttempt, RuntimeInstance)
                .join(JobAttempt, Job.current_attempt_id == JobAttempt.id)
                .outerjoin(RuntimeInstance, JobAttempt.runtime_instance_id == RuntimeInstance.id)
                .where(
                    Job.phase.in_(("running", "stopping")),
                    JobAttempt.phase.in_(("running", "stopping")),
                )
                .order_by(JobAttempt.id)
                .limit(limit)
            )
        ).all()
    return tuple(
        _candidate_from_row(job, attempt, runtime, now=now, current_boot=current_boot)
        for job, attempt, runtime in rows
    )


async def candidate_for_attempt(attempt_id: int) -> OrphanCandidate | None:
    """Load one exact unfinished attempt for delivery-time expiry reconciliation."""
    factory = _get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(Job, JobAttempt, RuntimeInstance)
                .join(JobAttempt, Job.current_attempt_id == JobAttempt.id)
                .outerjoin(RuntimeInstance, JobAttempt.runtime_instance_id == RuntimeInstance.id)
                .where(
                    JobAttempt.id == attempt_id,
                    Job.phase.in_(("running", "stopping")),
                    JobAttempt.phase.in_(("running", "stopping")),
                )
                .limit(1)
            )
        ).one_or_none()
    if row is None:
        return None
    job, attempt, runtime = row
    return _candidate_from_row(
        job,
        attempt,
        runtime,
        now=datetime.now(UTC),
        current_boot=read_boot_id(),
    )


async def reconcile_candidate(
    candidate: OrphanCandidate,
    *,
    cooperative_seconds: float,
    term_seconds: float,
) -> RecoveryDisposition:
    """Apply the locked recovery policy to one globally selected attempt."""
    assessment = await assess_candidate(
        candidate,
        cooperative_seconds=cooperative_seconds,
        term_seconds=term_seconds,
    )
    if assessment.disposition == "active":
        return "active"
    definition = JOB_DEFINITION_REGISTRY.get(candidate.job_type)
    writer = FencedWriter(candidate.ownership, definition)
    if assessment.disposition == "unsafe":
        disposition = await writer.unsafe(
            assessment.reason,
            code="mutation_recovery_not_safe",
            stage="publication_reconciliation",
        )
        return "unsafe" if disposition.value == "applied" else "stale"
    if definition.effect_safety == EffectSafety.STAGED_IDEMPOTENT:
        disposition = await writer.retry(reason=assessment.reason, delay_seconds=0)
    else:
        disposition = await writer.interrupt(assessment.reason)
    if disposition.value != "applied":
        return "stale"
    # The fenced writer's retry classifier converts ambiguous publication state to unsafe.
    factory = _get_session_factory()
    async with factory() as session:
        outcome = await session.scalar(
            select(Job.outcome).where(Job.id == candidate.ownership.job_id)
        )
    return "unsafe" if outcome == "unsafe" else "interrupted"


async def assess_candidate(
    candidate: OrphanCandidate,
    *,
    cooperative_seconds: float,
    term_seconds: float,
) -> OrphanAssessment:
    """Assess liveness and replay safety without changing canonical ownership."""
    if candidate.runtime_fresh:
        return OrphanAssessment("active", "runtime incarnation heartbeat is fresh")
    if candidate.partial_identity:
        return OrphanAssessment("unsafe", "partial durable process identity; no signal sent")
    if candidate.identity is not None and not candidate.runtime_observed:
        return OrphanAssessment(
            "unsafe",
            "process identity has no runtime-incarnation liveness evidence; no signal sent",
        )

    proven_dead = candidate.identity is None
    if candidate.identity is not None and candidate.runtime_same_host:
        status = await terminate_verified_orphan(
            candidate.identity,
            cooperative_seconds=cooperative_seconds,
            term_seconds=term_seconds,
        )
        if status != IdentityStatus.DEAD:
            return OrphanAssessment(
                "unsafe",
                f"same-host orphan identity is {status.value}; no speculative signal or replay",
            )
        proven_dead = True

    definition = JOB_DEFINITION_REGISTRY.get(candidate.job_type)
    if definition.effect_safety == EffectSafety.UNSAFE_MUTATION:
        return OrphanAssessment(
            "unsafe",
            "prior mutation process/publication state is not replay-safe",
            proven_dead=proven_dead,
        )
    if (
        definition.effect_safety == EffectSafety.STAGED_IDEMPOTENT
        and candidate.publication_started
    ):
        return OrphanAssessment(
            "unsafe",
            "prior staged publication state is not replay-safe",
            proven_dead=proven_dead,
        )
    return OrphanAssessment(
        "recoverable",
        (
            "verified orphan process tree is dead"
            if proven_dead
            else "expired remote runtime superseded by replay-safe policy"
        ),
        proven_dead=proven_dead,
    )


async def terminate_verified_orphan(
    identity: ProcessIdentity,
    *,
    cooperative_seconds: float,
    term_seconds: float,
) -> IdentityStatus:
    """Signal only an exact durable match and positively confirm group death."""
    status = verify_process_identity(identity)
    if status != IdentityStatus.MATCH:
        return status
    cgroup: CgroupV2Handle | None = None
    if identity.cgroup_path is not None:
        root = Path("/sys/fs/cgroup").resolve(strict=True)
        try:
            path = Path(identity.cgroup_path).resolve(strict=True)
        except OSError:
            return IdentityStatus.UNKNOWN
        expected_name = f"marquee-{identity.pid}-{identity.process_start_ticks}"
        if not path.is_relative_to(root) or path.name != expected_name:
            return IdentityStatus.MISMATCH
        cgroup = CgroupV2Handle(path)
        try:
            members = {
                int(value)
                for value in (path / "cgroup.procs").read_text(encoding="ascii").splitlines()
            }
        except (OSError, ValueError):
            return IdentityStatus.UNKNOWN
        if identity.pid not in members:
            return IdentityStatus.MISMATCH

    def tree_exists() -> bool:
        return cgroup.populated() if cgroup is not None else process_group_exists(
            identity.process_group_id
        )

    for sig, timeout in (
        (signal.SIGINT, cooperative_seconds),
        (signal.SIGTERM, term_seconds),
        (signal.SIGKILL, 2.0),
    ):
        current = verify_process_identity(identity)
        if current in {IdentityStatus.MISMATCH, IdentityStatus.UNKNOWN}:
            return current
        try:
            if sig == signal.SIGKILL and cgroup is not None:
                cgroup.kill()
            elif current == IdentityStatus.MATCH:
                os.killpg(identity.process_group_id, sig)
            elif tree_exists():
                return IdentityStatus.UNKNOWN
            else:
                return IdentityStatus.DEAD
        except ProcessLookupError:
            if not tree_exists():
                return IdentityStatus.DEAD
        except ProcessIdentityError:
            return IdentityStatus.UNKNOWN
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                alive = tree_exists()
            except ProcessIdentityError:
                return IdentityStatus.UNKNOWN
            if not alive:
                if cgroup is not None:
                    try:
                        cgroup.cleanup()
                    except ProcessIdentityError:
                        return IdentityStatus.UNKNOWN
                return IdentityStatus.DEAD
            await asyncio.sleep(0.02)
    return IdentityStatus.UNKNOWN


async def reconcile_startup_orphans(
    *,
    worker_node: str,
    cooperative_seconds: float,
    term_seconds: float,
    limit: int = 50,
) -> dict[str, int]:
    if limit < 1 or limit > 500:
        raise ValueError("orphan reconciliation limit is outside the bound")
    counts = {"active": 0, "interrupted": 0, "unsafe": 0, "stale": 0}
    for candidate in await _bounded_candidates(worker_node, limit=limit):
        disposition = await reconcile_candidate(
            candidate,
            cooperative_seconds=cooperative_seconds,
            term_seconds=term_seconds,
        )
        counts[disposition] += 1
    return counts
