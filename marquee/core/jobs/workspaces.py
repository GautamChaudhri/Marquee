"""Attempt-scoped confined staging, cleanup, and quarantine."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from marquee.core.filesystem import (
    ClassifiedPath,
    FilesystemBoundary,
    FilesystemBoundaryError,
    boundary_for_roots,
)
from marquee.database import _get_session_factory
from marquee.models.job import JobAttempt

_JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AttemptWorkspace:
    boundary: FilesystemBoundary
    directory: ClassifiedPath
    job_id: str
    attempt_id: int
    fence_token: int

    def staging_file(self, name: str = "output.bin") -> tuple[ClassifiedPath, int]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name) or name in {".", ".."}:
            raise WorkspaceError("staging filename is invalid")
        staged = self.boundary.from_key(
            self.directory.root.name,
            f"{self.directory.key.value}/{name}",
        )
        return staged, self.boundary.create_file(staged)

    def cleanup(self) -> None:
        marker = self.directory.root.resolved() / self.directory.key.value / ".quarantine.json"
        if marker.exists():
            raise WorkspaceError("quarantined workspace cannot be automatically deleted")
        self.boundary.cleanup_directory(self.directory)

    def quarantine(self, *, code: str, summary: str) -> None:
        if not re.fullmatch(r"[a-z0-9_]{1,40}", code):
            raise WorkspaceError("quarantine code is invalid")
        document = json.dumps(
            {
                "attempt_id": self.attempt_id,
                "code": code,
                "fence_token": self.fence_token,
                "job_id": self.job_id,
                "quarantined_at": datetime.now(UTC).isoformat(),
                "summary": summary[:500],
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        marker = self.boundary.from_key(
            self.directory.root.name,
            f"{self.directory.key.value}/.quarantine.json",
        )
        try:
            fd = self.boundary.create_file(marker)
        except FileExistsError:
            return
        try:
            os.write(fd, document)
            os.fsync(fd)
        finally:
            os.close(fd)


class AttemptWorkspaceManager:
    def __init__(self, boundary: FilesystemBoundary, *, root_name: str = "data") -> None:
        self.boundary = boundary
        self.root_name = root_name

    @classmethod
    def for_data_dir(cls, data_dir: str | Path) -> AttemptWorkspaceManager:
        root = Path(data_dir).resolve(strict=True)
        return cls(
            boundary_for_roots({"data": root}, access="read_write", purpose="attempt workspaces")
        )

    def create(self, *, job_id: str, attempt_id: int, fence_token: int) -> AttemptWorkspace:
        if not _JOB_ID.fullmatch(job_id):
            raise WorkspaceError("canonical job identity is invalid")
        if isinstance(attempt_id, bool) or attempt_id < 1:
            raise WorkspaceError("attempt identity is invalid")
        if isinstance(fence_token, bool) or fence_token < 1:
            raise WorkspaceError("fence token is invalid")
        parent = self.boundary.from_key(self.root_name, f"jobs/workspaces/{job_id}")
        key = f"{parent.key.value}/{attempt_id}-{fence_token}"
        directory = self.boundary.from_key(self.root_name, key)
        try:
            self.boundary.create_directory(parent, parents=True)
            self.boundary.create_directory(directory)
        except (OSError, FilesystemBoundaryError) as exc:
            raise WorkspaceError("attempt workspace cannot be created safely") from exc
        return AttemptWorkspace(
            boundary=self.boundary,
            directory=directory,
            job_id=job_id,
            attempt_id=attempt_id,
            fence_token=fence_token,
        )

    def existing(self, *, job_id: str, attempt_id: int, fence_token: int) -> AttemptWorkspace:
        if not _JOB_ID.fullmatch(job_id) or attempt_id < 1 or fence_token < 1:
            raise WorkspaceError("workspace identity is invalid")
        physical = (
            self.boundary.roots[self.root_name].resolved()
            / "jobs"
            / "workspaces"
            / job_id
            / f"{attempt_id}-{fence_token}"
        )
        directory = self.boundary.classify(
            physical, roots=(self.root_name,), require_exists=True, write=True
        )
        return AttemptWorkspace(
            boundary=self.boundary,
            directory=directory,
            job_id=job_id,
            attempt_id=attempt_id,
            fence_token=fence_token,
        )


async def reconcile_stale_workspaces(data_dir: str | Path, *, limit: int = 100) -> dict[str, int]:
    """Delete only proven-finished unambiguous work; quarantine everything uncertain."""
    if limit < 1 or limit > 500:
        raise ValueError("workspace reconciliation limit is outside the bound")
    manager = AttemptWorkspaceManager.for_data_dir(data_dir)
    base = manager.boundary.roots[manager.root_name].resolved() / "jobs" / "workspaces"
    if not base.exists():
        return {"deleted": 0, "quarantined": 0, "active": 0}
    candidates: list[tuple[str, int, int]] = []
    for job_dir in sorted(base.iterdir(), key=lambda path: path.name):
        if len(candidates) >= limit:
            break
        if not _JOB_ID.fullmatch(job_dir.name) or not job_dir.is_dir() or job_dir.is_symlink():
            continue
        for attempt_dir in sorted(job_dir.iterdir(), key=lambda path: path.name):
            match = re.fullmatch(r"([1-9][0-9]*)-([1-9][0-9]*)", attempt_dir.name)
            if match and attempt_dir.is_dir() and not attempt_dir.is_symlink():
                candidates.append((job_dir.name, int(match.group(1)), int(match.group(2))))
            if len(candidates) >= limit:
                break
    if not candidates:
        return {"deleted": 0, "quarantined": 0, "active": 0}
    factory = _get_session_factory()
    async with factory() as session:
        attempts = {
            attempt.id: attempt
            for attempt in await session.scalars(
                select(JobAttempt).where(
                    JobAttempt.id.in_([attempt_id for _, attempt_id, _ in candidates])
                )
            )
        }
    counts = {"deleted": 0, "quarantined": 0, "active": 0}
    for job_id, attempt_id, fence_token in candidates:
        workspace = manager.existing(job_id=job_id, attempt_id=attempt_id, fence_token=fence_token)
        attempt = attempts.get(attempt_id)
        exact = bool(
            attempt is not None and attempt.job_id == job_id and attempt.fence_token == fence_token
        )
        if (
            exact
            and attempt is not None
            and attempt.phase
            in {
                "running",
                "stopping",
                "admitted",
            }
        ):
            counts["active"] += 1
            continue
        metrics = dict(attempt.metrics or {}) if exact and attempt is not None else {}
        ambiguous = bool(metrics.get("publish_intent") and not metrics.get("publication"))
        if exact and attempt is not None and attempt.phase == "finished" and not ambiguous:
            try:
                workspace.cleanup()
            except WorkspaceError:
                counts["quarantined"] += 1
            else:
                counts["deleted"] += 1
            continue
        workspace.quarantine(
            code="ambiguous_workspace",
            summary="workspace ownership or publication outcome cannot be proven",
        )
        counts["quarantined"] += 1
    return counts
