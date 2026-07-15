"""Shared JMC5B media-mutation test harness.

Registered as a pytest plugin from ``tests/conftest.py`` so the fixtures resolve
by name without cross-importing test modules.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from marquee.core.filesystem import FilesystemBoundary, RootSpec
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.process_launcher import ProcessLauncher
from marquee.core.jobs.track_inventory_adapter import inventory_from_probe
from marquee.core.media_files import compute_signature
from marquee.core.subtitles.probe import probe_container
from marquee.database import _get_session_factory
from marquee.models import Job, MediaFile


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


def real_signature(path: Path) -> str:
    """The exact signature the handler recomputes from the live file."""
    stat = path.stat()
    return compute_signature(path, size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def inventory_of(path: Path, signature: str | None = None):
    result = probe_container(path)
    return inventory_from_probe(
        signature=signature or real_signature(path),
        audio_streams=result.audio_streams,
        subtitles=result.subtitles,
        container=result.container,
    )


def launcher_for(tmp_path: Path) -> ProcessLauncher:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    boundary = FilesystemBoundary(
        {"test": RootSpec(name="test", path=tmp_path, purpose="test", access="read_write")}
    )
    return ProcessLauncher(
        worker_node="jmc5b-test",
        boundary=boundary,
        working_directory=boundary.classify(work),
    )


async def media_file_row(db, path: Path) -> MediaFile:
    row = MediaFile(source_key=f"test:{uuid4().hex}", path=str(path), source="radarr")
    db.add(row)
    await db.flush()
    return row


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
            feature_area="audio_subtitles",
            presentation_family="audio_subtitles",
            subject_kind="track",
            subject_reference=str(request["media_file_id"]),
            subject_snapshot={"version": 1, "kind": "track"},
        )
    )
    await db.commit()
    return SimpleNamespace(
        delivery=SimpleNamespace(canonical_job_id=job_id),
        attempt=SimpleNamespace(attempt_id=1, fence_token=7),
        request=request,
        definition=JOB_DEFINITION_REGISTRY.get(job_type),
        cancellation=SimpleNamespace(is_cancelled=lambda: False),
        writer=Fence(),
        process_launcher=launcher_for(tmp_path),
        session_factory=_get_session_factory(),
    )


@pytest.fixture
def jmc5b_media_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let confined synthetic tmp paths validate, as the other media suites do."""
    from marquee.config import settings

    monkeypatch.setattr(settings, "RADARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "SONARR_MEDIA_PATH", None)
    monkeypatch.setattr(settings, "RADARR_PATH_PREFIX", None)
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [])


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Confine canonical backups and managed sidecars to a synthetic data root."""
    from marquee.config import settings

    root = tmp_path / "data"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", str(root))
    return root
