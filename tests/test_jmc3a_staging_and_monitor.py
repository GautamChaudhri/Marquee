from __future__ import annotations

import inspect
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from marquee.core.jobs.publication import (
    PublicationCoordinator,
    PublicationError,
    file_signature,
)
from marquee.core.jobs.transport_intent_monitor import TransportIntentMonitor
from marquee.core.jobs.workspaces import AttemptWorkspaceManager, WorkspaceError
from marquee.models.job import Job, JobDispatch

NOW = datetime.now(UTC)


class Fence:
    def __init__(self, *, apply: bool = True, execute: bool = True, fail_after: bool = False):
        self.apply = apply
        self.execute = execute
        self.fail_after = fail_after
        self.intent = None
        self.evidence = None

    async def record_publish_intent(self, intent):
        self.intent = intent
        return "applied" if self.apply else "stale"

    async def publish_atomic(self, action, evidence):
        if not self.apply:
            return "stale"
        if self.execute:
            action()
        if self.fail_after:
            raise RuntimeError("synthetic commit crash")
        self.evidence = evidence
        return "applied"


def _write(fd: int, content: bytes) -> None:
    try:
        os.write(fd, content)
        os.fsync(fd)
    finally:
        os.close(fd)


def test_workspace_identity_collision_and_quarantine(tmp_path: Path) -> None:
    manager = AttemptWorkspaceManager.for_data_dir(tmp_path)
    workspace = manager.create(job_id="job_1", attempt_id=7, fence_token=9)
    with pytest.raises(WorkspaceError):
        manager.create(job_id="job_1", attempt_id=7, fence_token=9)
    staged, fd = workspace.staging_file()
    _write(fd, b"fixed")
    assert staged.key.value.endswith("/output.bin")
    workspace.quarantine(code="ambiguous_publish", summary="synthetic crash")
    with pytest.raises(WorkspaceError):
        workspace.cleanup()


@pytest.mark.asyncio
async def test_coordinator_validates_and_atomically_publishes(tmp_path: Path) -> None:
    workspace = AttemptWorkspaceManager.for_data_dir(tmp_path).create(
        job_id="job_2", attempt_id=1, fence_token=1
    )
    staged, fd = workspace.staging_file(".marquee-stage")
    _write(fd, b"validated output")
    destination = workspace.boundary.from_key(
        workspace.directory.root.name, f"{workspace.directory.key.value}/destination.bin"
    )
    fence = Fence()
    signature = await PublicationCoordinator(workspace.boundary).publish(
        staged=staged,
        destination=destination,
        expected_destination=None,
        fence=fence,
    )
    assert signature.sha256 == file_signature(workspace.boundary, destination).sha256
    assert fence.intent["destination_key"] == destination.key.value
    assert fence.evidence["output"]["sha256"] == signature.sha256


@pytest.mark.asyncio
async def test_stale_fence_and_changed_destination_never_publish(tmp_path: Path) -> None:
    workspace = AttemptWorkspaceManager.for_data_dir(tmp_path).create(
        job_id="job_3", attempt_id=1, fence_token=1
    )
    destination = workspace.boundary.from_key(
        "data", f"{workspace.directory.key.value}/destination.bin"
    )
    destination_fd = workspace.boundary.create_file(destination)
    _write(destination_fd, b"original")
    expected = file_signature(workspace.boundary, destination)
    staged, staged_fd = workspace.staging_file("stage.bin")
    _write(staged_fd, b"new")
    stale = Fence(apply=False)
    with pytest.raises(PublicationError, match="stale ownership"):
        await PublicationCoordinator(workspace.boundary).publish(
            staged=staged,
            destination=destination,
            expected_destination=expected,
            fence=stale,
        )
    assert file_signature(workspace.boundary, destination) == expected
    replacement_fd = workspace.boundary.create_file(destination, exclusive=False)
    _write(replacement_fd, b"changed")
    with pytest.raises(PublicationError, match="destination identity changed"):
        await PublicationCoordinator(workspace.boundary).publish(
            staged=staged,
            destination=destination,
            expected_destination=expected,
            fence=Fence(),
        )


@pytest.mark.asyncio
async def test_commit_crash_leaves_durable_intent_for_quarantine(tmp_path: Path) -> None:
    workspace = AttemptWorkspaceManager.for_data_dir(tmp_path).create(
        job_id="job_4", attempt_id=1, fence_token=1
    )
    staged, fd = workspace.staging_file("stage.bin")
    _write(fd, b"published before synthetic commit failure")
    destination = workspace.boundary.from_key(
        "data", f"{workspace.directory.key.value}/destination.bin"
    )
    fence = Fence(fail_after=True)
    with pytest.raises(RuntimeError, match="synthetic commit crash"):
        await PublicationCoordinator(workspace.boundary).publish(
            staged=staged,
            destination=destination,
            expected_destination=None,
            fence=fence,
        )
    assert fence.intent is not None
    assert file_signature(workspace.boundary, destination).sha256 == fence.intent["output"]["sha256"]
    workspace.quarantine(code="ambiguous_publish", summary="commit outcome unknown")


class FakeGateway:
    def __init__(self, statuses: dict[str, str]):
        self.statuses = statuses
        self.cancelled: list[str] = []

    async def known_ticket_statuses(self, session, *, job_ids):
        assert len(job_ids) <= 100
        return {job_id: self.statuses[job_id] for job_id in job_ids if job_id in self.statuses}

    async def cancel_known_ticket(self, session, *, job_id):
        self.cancelled.append(job_id)


def _job(job_id: str, ticket: int) -> Job:
    return Job(
        id=job_id,
        type="system_noop",
        request={"echo": {}},
        phase="queued",
        desired_state="cancel",
        fence_token=0,
        priority=50,
        eligible_at=NOW,
        pgq_job_id=ticket,
        dispatch_generation=1,
        root_id=job_id,
        trigger_kind="system",
        feature_area="system",
        presentation_family="system",
        subject_kind="system_work",
        subject_snapshot={},
        created_at=NOW,
        queued_at=NOW,
    )


@pytest.mark.asyncio
async def test_monitor_is_bounded_and_uses_public_gateway(db) -> None:
    job = _job("monitor_job", 991)
    db.add_all(
        [
            job,
            JobDispatch(
                job_id=job.id,
                generation=1,
                pgq_job_id=991,
                entrypoint="control",
                dedupe_key="marquee:monitor_job:1",
                priority=50,
                eligible_at=NOW,
                disposition="active",
            ),
        ]
    )
    await db.commit()
    gateway = FakeGateway({job.id: "queued"})
    result = await TransportIntentMonitor(gateway, batch_size=1).run_once()
    assert result == {"cancelled": 0, "retried": 1, "attention": 0}
    assert gateway.cancelled == [job.id]
    source = inspect.getsource(TransportIntentMonitor)
    assert ".enqueue(" not in source
    assert "pgqueuer." not in source
    assert "skip_locked=True" in source
    assert ".limit(self.batch_size)" in source


@pytest.mark.asyncio
async def test_monitor_surfaces_missing_ticket_without_scheduling(db) -> None:
    job = _job("missing_ticket", 992)
    db.add_all(
        [
            job,
            JobDispatch(
                job_id=job.id,
                generation=1,
                pgq_job_id=992,
                entrypoint="control",
                dedupe_key="marquee:missing_ticket:1",
                priority=50,
                eligible_at=NOW,
                disposition="active",
            ),
        ]
    )
    await db.commit()
    await TransportIntentMonitor(FakeGateway({}), batch_size=1).run_once()
    await db.rollback()
    refreshed = await db.scalar(select(Job).where(Job.id == job.id))
    assert refreshed is not None
    assert refreshed.attention["code"] == "transport_link_attention"
