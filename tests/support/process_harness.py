"""Shared execution-context test harness.

Registered as a pytest plugin from ``tests/conftest.py`` so the fixtures resolve
by name without cross-importing test modules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from marquee.core.filesystem import FilesystemBoundary, RootSpec
from marquee.core.jobs.execution_io import ExecutionIO
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.process_launcher import ProcessLauncher
from marquee.core.jobs.workspaces import AttemptWorkspaceManager
from marquee.database import _get_session_factory
from marquee.models import Job, JobAttempt


class Fence:
    """Minimal fenced writer: always owns the attempt, publishes when asked."""

    def __init__(self) -> None:
        self.publish_intents: list[dict] = []
        self.publications: list[dict] = []

    async def owns_current_attempt(self, _session) -> bool:
        return True

    async def record_publish_intent(self, intent: dict) -> str:
        self.publish_intents.append(intent)
        return "applied"

    async def publish_atomic(self, action, evidence: dict) -> str:
        action()
        self.publications.append(evidence)
        return "applied"


def launcher_for(tmp_path: Path) -> ProcessLauncher:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    boundary = FilesystemBoundary(
        {"test": RootSpec(name="test", path=tmp_path, purpose="test", access="read_write")}
    )
    return ProcessLauncher(
        worker_node="harness-test",
        boundary=boundary,
        working_directory=boundary.classify(work),
    )


async def execution_context(db, tmp_path: Path, *, job_type: str, request: dict):
    job_id = uuid4().hex
    db.add(
        Job(
            id=job_id,
            type=job_type,
            payload_version=1,
            request=request,
            phase="running",
            desired_state="run",
            fence_token=7,
            root_id=job_id,
            trigger_kind="manual",
            feature_area="ai_posters",
            presentation_family="ai_posters",
            subject_kind="track",
            subject_reference=str(request["media_file_id"]),
            subject_snapshot={"version": 1, "kind": "track"},
        )
    )
    await db.flush()
    attempt = JobAttempt(
        job_id=job_id,
        number=1,
        fence_token=7,
        phase="running",
        started_at=datetime.now(UTC),
    )
    db.add(attempt)
    await db.flush()
    job = await db.get(Job, job_id)
    assert job is not None
    job.current_attempt_id = attempt.id
    await db.commit()
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    workspace = AttemptWorkspaceManager.for_data_dir(data_root).create(
        job_id=job_id,
        attempt_id=attempt.id,
        fence_token=7,
    )
    fence = Fence()

    async def owns_fence() -> bool:
        return await fence.owns_current_attempt(None)

    async def progress_stage(*_args, **_kwargs) -> None:
        return None

    return SimpleNamespace(
        delivery=SimpleNamespace(canonical_job_id=job_id),
        attempt=SimpleNamespace(attempt_id=attempt.id, fence_token=7),
        request=request,
        configuration={},
        definition=JOB_DEFINITION_REGISTRY.get(job_type),
        cancellation=SimpleNamespace(is_cancelled=lambda: False),
        writer=fence,
        process_launcher=launcher_for(tmp_path),
        progress=SimpleNamespace(stage=progress_stage),
        io=ExecutionIO(cancelled=lambda: False, owns_fence=owns_fence),
        workspace=workspace,
        session_factory=_get_session_factory(),
    )


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Confine canonical backups and managed sidecars to a synthetic data root."""
    from marquee.config import settings

    root = tmp_path / "data"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", str(root))
    return root
