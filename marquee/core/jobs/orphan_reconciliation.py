"""Identity-safe bounded reconciliation of unfinished attempts at worker startup."""

from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from marquee.core.jobs.fenced_writer import AttemptOwnership, FencedWriter
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.process_identity import (
    CgroupV2Handle,
    IdentityStatus,
    ProcessIdentity,
    ProcessIdentityError,
    process_group_exists,
    verify_process_identity,
)
from marquee.database import _get_session_factory
from marquee.models.job import Job, JobAttempt


@dataclass(frozen=True, slots=True)
class OrphanCandidate:
    ownership: AttemptOwnership
    job_type: str
    identity: ProcessIdentity | None
    partial_identity: bool


async def _bounded_candidates(worker_node: str, *, limit: int) -> tuple[OrphanCandidate, ...]:
    factory = _get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(Job, JobAttempt)
                .join(JobAttempt, Job.current_attempt_id == JobAttempt.id)
                .where(
                    Job.phase.in_(("running", "stopping")),
                    JobAttempt.phase.in_(("running", "stopping")),
                    JobAttempt.worker_node_id == worker_node,
                )
                .order_by(JobAttempt.id)
                .limit(limit)
            )
        ).all()
    candidates: list[OrphanCandidate] = []
    for job, attempt in rows:
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
                worker_node=worker_node,
                host_boot_id=str(host_boot_id),
                pid=int(process_id),
                process_group_id=int(process_group_id),
                process_start_ticks=int(process_start_ticks),
                cgroup_path=attempt.cgroup_path,
            )
        candidates.append(
            OrphanCandidate(
                ownership=AttemptOwnership(
                    job_id=job.id,
                    attempt_id=attempt.id,
                    fence_token=attempt.fence_token,
                    dispatch_generation=job.dispatch_generation,
                ),
                job_type=job.type,
                identity=identity,
                partial_identity=any(present) and not all(present),
            )
        )
    return tuple(candidates)


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
    counts = {"interrupted": 0, "unsafe": 0, "stale": 0}
    for candidate in await _bounded_candidates(worker_node, limit=limit):
        definition = JOB_DEFINITION_REGISTRY.get(candidate.job_type)
        writer = FencedWriter(candidate.ownership, definition)
        if candidate.partial_identity:
            disposition = await writer.unsafe("partial durable process identity; no signal sent")
            counts["unsafe" if disposition.value == "applied" else "stale"] += 1
            continue
        if candidate.identity is None:
            disposition = await writer.interrupt("processless attempt confirmed after worker restart")
            counts["interrupted" if disposition.value == "applied" else "stale"] += 1
            continue
        status = await terminate_verified_orphan(
            candidate.identity,
            cooperative_seconds=cooperative_seconds,
            term_seconds=term_seconds,
        )
        if status == IdentityStatus.DEAD:
            disposition = await writer.interrupt("verified orphan process tree is dead")
            counts["interrupted" if disposition.value == "applied" else "stale"] += 1
        else:
            disposition = await writer.unsafe(
                f"orphan process identity is {status.value}; no speculative signal or replay"
            )
            counts["unsafe" if disposition.value == "applied" else "stale"] += 1
    return counts
