"""Confined immutable physical artifacts and bounded canonical virtual documents."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select

from marquee.config import settings
from marquee.core.filesystem import (
    ClassifiedPath,
    FilesystemBoundary,
    FilesystemBoundaryError,
    RootSpec,
)
from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.log_capture import CentralRedactor
from marquee.database import _get_session_factory
from marquee.models import Job, JobArtifact, JobAttempt, JobEvent, JobLog

_IDENTITY = re.compile(r"^[a-f0-9]{32}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,159}$")
_CHECKSUM = re.compile(r"^[a-f0-9]{64}$")
VIRTUAL_SOURCES = frozenset({"request", "plan", "result", "error", "events"})


class ArtifactError(RuntimeError):
    """Artifact policy, integrity, or storage failure."""


class ArtifactMissingError(ArtifactError):
    """The database record exists but its managed physical object does not."""


@dataclass(frozen=True)
class ArtifactPolicy:
    extension: str
    content_types: frozenset[str]
    max_bytes: int
    attempt_required: bool = True


ARTIFACT_POLICIES: dict[str, ArtifactPolicy] = {
    "managed_sidecar": ArtifactPolicy(
        ".srt", frozenset({"application/x-subrip"}), 64 * 1024 * 1024
    ),
    "taste_profile": ArtifactPolicy(
        ".npz", frozenset({"application/octet-stream"}), 256 * 1024 * 1024
    ),
    "taste_map": ArtifactPolicy(
        ".npz", frozenset({"application/octet-stream"}), 256 * 1024 * 1024
    ),
    "ranking_residual": ArtifactPolicy(
        ".npz", frozenset({"application/octet-stream"}), 16 * 1024 * 1024
    ),
    "command_report": ArtifactPolicy(".json", frozenset({"application/json"}), 1024 * 1024),
    "validation_report": ArtifactPolicy(
        ".json", frozenset({"application/json"}), 1024 * 1024
    ),
    "backup_manifest": ArtifactPolicy(
        ".json", frozenset({"application/json"}), 1024 * 1024
    ),
    "diagnostic_text": ArtifactPolicy(
        ".txt", frozenset({"text/plain"}), 1024 * 1024
    ),
    "evidence_image": ArtifactPolicy(
        ".jpg", frozenset({"image/jpeg"}), 10 * 1024 * 1024
    ),
    "taste_exemplar": ArtifactPolicy(
        ".jpg", frozenset({"image/jpeg"}), 25 * 1024 * 1024
    ),
    "evidence_frame": ArtifactPolicy(
        ".png", frozenset({"image/png"}), 10 * 1024 * 1024
    ),
    "media_candidate": ArtifactPolicy(
        ".mkv", frozenset({"video/x-matroska"}), 8 * 1024 * 1024 * 1024 * 1024
    ),
    "media_backup": ArtifactPolicy(
        ".mkv", frozenset({"video/x-matroska"}), 8 * 1024 * 1024 * 1024 * 1024
    ),
}


async def register_existing_physical_artifact(
    *,
    job_id: str,
    attempt_id: int,
    fence_token: int,
    source: ClassifiedPath,
    kind: Literal["managed_sidecar", "product_backup"],
    name: str,
    checksum: str,
    size_bytes: int,
    retention_class: str = "extended",
) -> JobArtifact:
    """Register an already-confined immutable product file without duplicating it."""
    if (
        not isinstance(source, ClassifiedPath)
        or source.root.name != "data"
        or not _IDENTITY.fullmatch(job_id)
        or attempt_id < 1
        or fence_token < 1
        or kind not in {"managed_sidecar", "product_backup"}
        or not _SAFE_NAME.fullmatch(name)
        or not _CHECKSUM.fullmatch(checksum)
        or size_bytes < 0
        or retention_class not in {"standard", "extended", "ephemeral"}
    ):
        raise ArtifactError("existing artifact contract is invalid")
    fd = await asyncio.to_thread(FilesystemBoundary({"data": source.root}).open_read, source)
    try:
        actual_size = (await asyncio.to_thread(os.fstat, fd)).st_size
    finally:
        os.close(fd)
    if actual_size != size_bytes:
        raise ArtifactError("existing artifact size does not match its evidence")

    factory = _get_session_factory()
    async with factory() as session, session.begin():
        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        attempt = await session.scalar(
            select(JobAttempt).where(
                JobAttempt.id == attempt_id,
                JobAttempt.job_id == job_id,
                JobAttempt.fence_token == fence_token,
            )
        )
        if job is None or attempt is None:
            raise ArtifactError("existing artifact attempt ownership is stale")
        existing = await session.scalar(
            select(JobArtifact).where(
                JobArtifact.job_id == job_id,
                JobArtifact.attempt_id == attempt_id,
                JobArtifact.kind == kind,
                JobArtifact.storage_key == source.key.value,
            )
        )
        if existing is not None:
            return existing
        row = JobArtifact(
            job_id=job_id,
            attempt_id=attempt_id,
            kind=kind,
            name=name,
            status="available",
            storage_key=source.key.value,
            content_type=(
                "application/x-subrip"
                if kind == "managed_sidecar"
                else "application/octet-stream"
            ),
            size_bytes=size_bytes,
            checksum=checksum,
            artifact_metadata={
                "subject_kind": job.subject_kind,
                "subject_reference": job.subject_reference,
                "source": "canonical_result",
            },
            retention_class=retention_class,
            expires_at=datetime.now(UTC) + _retention_delta(retention_class),
        )
        session.add(row)
        await session.flush((row,))
        await job_event_writer.append(
            session,
            job_id=job_id,
            attempt_id=attempt_id,
            event_key="artifact.available",
            state=job.phase,
            message="Job product evidence available",
            detail={"artifact_id": row.id, "kind": kind, "name": name},
            canonical_version=job.fence_token,
        )
        return row


def artifact_boundary(data_dir: str | Path) -> FilesystemBoundary:
    return FilesystemBoundary(
        {
            "data": RootSpec(
                name="data",
                path=Path(data_dir),
                purpose="job artifact storage",
                access="read_write",
                allow_symlinks=False,
                same_filesystem=False,
            )
        }
    )


def ensure_artifact_root(data_dir: str | Path) -> None:
    boundary = artifact_boundary(data_dir)
    directory = boundary.from_key("data", "jmc3/evidence/artifacts")
    boundary.create_directory(directory, parents=True)
    probe, fd = boundary.temporary_file(directory, prefix=".readiness-")
    try:
        os.write(fd, b"ready")
        os.fsync(fd)
    finally:
        os.close(fd)
        boundary.delete_file(probe, missing_ok=True)


def _validate_common(
    *, kind: str, name: str, content_type: str, retention_class: str, metadata: Mapping[str, Any]
) -> ArtifactPolicy:
    policy = ARTIFACT_POLICIES.get(kind)
    if policy is None:
        raise ArtifactError("artifact kind is not registered")
    if not _SAFE_NAME.fullmatch(name) or name in {".", ".."}:
        raise ArtifactError("artifact display name is unsafe")
    if content_type not in policy.content_types:
        raise ArtifactError("artifact content type conflicts with kind policy")
    if retention_class not in {"standard", "extended", "ephemeral", "pinned"}:
        raise ArtifactError("artifact retention class is invalid")
    if retention_class == "pinned" and kind != "taste_exemplar":
        raise ArtifactError("only canonical taste exemplars may use pinned retention")
    encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(metadata) > 32 or len(encoded.encode()) > 8192:
        raise ArtifactError("artifact metadata exceeds its bound")
    return policy


async def register_physical_artifact(
    *,
    job_id: str,
    attempt_id: int,
    fence_token: int,
    source: ClassifiedPath,
    kind: str,
    name: str,
    content_type: str,
    retention_class: str = "standard",
    metadata: Mapping[str, Any] | None = None,
    data_dir: str | Path | None = None,
) -> JobArtifact:
    """Copy one already-classified source into immutable managed storage."""
    if not isinstance(source, ClassifiedPath):
        raise ArtifactError("physical artifact source must already be classified")
    if not _IDENTITY.fullmatch(job_id) or attempt_id < 1 or fence_token < 1:
        raise ArtifactError("artifact provenance is invalid")
    safe_metadata = dict(metadata or {})
    policy = _validate_common(
        kind=kind,
        name=name,
        content_type=content_type,
        retention_class=retention_class,
        metadata=safe_metadata,
    )
    root = Path(data_dir or settings.DATA_DIR)
    data_root = RootSpec(
        name="data",
        path=root,
        purpose="job artifact storage",
        access="read_write",
        allow_symlinks=False,
        same_filesystem=False,
    )
    roots = {"data": data_root}
    if source.root.name == "data":
        if source.root.resolved() != data_root.resolved():
            raise ArtifactError("artifact source root conflicts with managed storage")
    else:
        roots[source.root.name] = source.root
    boundary = FilesystemBoundary(roots)
    factory = _get_session_factory()
    async with factory() as session, session.begin():
        attempt = await session.scalar(
            select(JobAttempt).where(
                JobAttempt.id == attempt_id,
                JobAttempt.job_id == job_id,
                JobAttempt.fence_token == fence_token,
            )
        )
        job = await session.scalar(select(Job).where(Job.id == job_id))
        if attempt is None or job is None:
            raise ArtifactError("artifact attempt ownership is stale")
        row = JobArtifact(
            job_id=job_id,
            attempt_id=attempt_id,
            kind=kind,
            name=name,
            status="pending",
            storage_key=f"jmc3/evidence/artifacts/{job_id}/pending{policy.extension}",
            content_type=content_type,
            artifact_metadata=safe_metadata,
            retention_class=retention_class,
        )
        session.add(row)
        await session.flush((row,))
        row.storage_key = (
            f"jmc3/evidence/artifacts/{job_id}/{row.id}/artifact-{row.id}{policy.extension}"
        )
        artifact_id = row.id

    directory = boundary.from_key("data", f"jmc3/evidence/artifacts/{job_id}/{artifact_id}")
    staged: ClassifiedPath | None = None
    try:
        directory = await asyncio.to_thread(boundary.create_directory, directory, parents=True)
        staged, write_fd = await asyncio.to_thread(
            boundary.temporary_file, directory, prefix=".artifact-"
        )
        read_fd = await asyncio.to_thread(boundary.open_read, source)
        digest = hashlib.sha256()
        size = 0
        try:
            while chunk := await asyncio.to_thread(os.read, read_fd, 1024 * 1024):
                size += len(chunk)
                if size > policy.max_bytes:
                    raise ArtifactError("artifact exceeds kind size policy")
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = await asyncio.to_thread(os.write, write_fd, view)
                    if written <= 0:
                        raise ArtifactError("artifact staging write made no progress")
                    view = view[written:]
            await asyncio.to_thread(os.fsync, write_fd)
            await asyncio.to_thread(os.fchmod, write_fd, 0o400)
        finally:
            os.close(read_fd)
            os.close(write_fd)
        destination = boundary.from_key(
            "data",
            f"jmc3/evidence/artifacts/{job_id}/{artifact_id}/artifact-{artifact_id}{policy.extension}",
        )
        await asyncio.to_thread(boundary.atomic_replace, staged, destination)
        checksum = digest.hexdigest()
        retention_delta = _retention_delta(retention_class)
        expires_at = datetime.now(UTC) + retention_delta if retention_delta is not None else None
        async with factory() as session, session.begin():
            row = await session.scalar(
                select(JobArtifact).where(JobArtifact.id == artifact_id).with_for_update()
            )
            job = await session.scalar(select(Job).where(Job.id == job_id))
            if row is None or row.status != "pending" or job is None:
                raise ArtifactError("artifact publication metadata is stale")
            row.status = "available"
            row.size_bytes = size
            row.checksum = checksum
            row.expires_at = expires_at
            if job.current_attempt_id == attempt_id and job.fence_token == fence_token:
                await job_event_writer.append(
                    session,
                    job_id=job_id,
                    attempt_id=attempt_id,
                    event_key="artifact.available",
                    state=job.phase,
                    message="Job artifact available",
                    detail={"artifact_id": artifact_id, "kind": kind, "name": name},
                    canonical_version=fence_token,
                )
            result = row
        return result
    except Exception as exc:
        if staged is not None:
            with contextlib.suppress(Exception):
                boundary.delete_file(staged, missing_ok=True)
        async with factory() as session, session.begin():
            row = await session.scalar(
                select(JobArtifact)
                .where(JobArtifact.id == artifact_id, JobArtifact.status == "pending")
                .with_for_update()
            )
            job = await session.scalar(select(Job).where(Job.id == job_id))
            if row is not None:
                row.status = "failed"
                row.artifact_metadata = {
                    **safe_metadata,
                    "failure_code": type(exc).__name__[:40],
                }
                if (
                    job is not None
                    and job.current_attempt_id == attempt_id
                    and job.fence_token == fence_token
                ):
                    await job_event_writer.append(
                        session,
                        job_id=job_id,
                        attempt_id=attempt_id,
                        event_key="artifact.failed",
                        state=job.phase,
                        message="Job artifact failed",
                        detail={"artifact_id": artifact_id, "kind": kind, "name": name},
                        canonical_version=fence_token,
                    )
        raise


async def register_virtual_artifact(
    *,
    job_id: str,
    source: Literal["request", "plan", "result", "error", "events"],
    name: str,
    attempt_id: int | None = None,
    fence_token: int | None = None,
    retention_class: str = "standard",
) -> JobArtifact:
    if source not in VIRTUAL_SOURCES or not _SAFE_NAME.fullmatch(name):
        raise ArtifactError("virtual artifact contract is invalid")
    if retention_class not in {"standard", "extended", "ephemeral"}:
        raise ArtifactError("artifact retention class is invalid")
    factory = _get_session_factory()
    async with factory() as session, session.begin():
        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None:
            raise ArtifactError("artifact job does not exist")
        if attempt_id is not None:
            attempt = await session.scalar(
                select(JobAttempt).where(
                    JobAttempt.id == attempt_id,
                    JobAttempt.job_id == job_id,
                    JobAttempt.fence_token == fence_token,
                )
            )
            if attempt is None:
                raise ArtifactError("artifact attempt ownership is stale")
        existing = await session.scalar(
            select(JobArtifact).where(
                JobArtifact.job_id == job_id,
                JobArtifact.attempt_id == attempt_id,
                JobArtifact.kind == f"canonical_{source}",
                JobArtifact.status == "available",
            )
        )
        if existing is not None:
            return existing
        document = await _virtual_document(session, job, source)
        encoded = _encode_virtual(document)
        through_cursor = (
            document["events"][-1]["cursor"] if source == "events" and document["events"] else 0
        )
        row = JobArtifact(
            job_id=job_id,
            attempt_id=attempt_id,
            kind=f"canonical_{source}",
            name=name,
            status="available",
            virtual_source={
                "version": 1,
                "document": source,
                **({"through_cursor": through_cursor} if source == "events" else {}),
            },
            content_type="application/json",
            size_bytes=len(encoded),
            checksum=hashlib.sha256(encoded).hexdigest(),
            retention_class=retention_class,
            expires_at=datetime.now(UTC) + _retention_delta(retention_class),
        )
        session.add(row)
        await session.flush((row,))
        if attempt_id is None or (
            job.current_attempt_id == attempt_id and job.fence_token == fence_token
        ):
            await job_event_writer.append(
                session,
                job_id=job_id,
                attempt_id=attempt_id,
                event_key="artifact.available",
                state=job.phase,
                message="Canonical job artifact available",
                detail={"artifact_id": row.id, "kind": row.kind, "name": row.name},
                canonical_version=job.fence_token,
            )
        return row


async def register_validation_result_artifact(
    *, job_id: str, attempt_id: int, fence_token: int
) -> JobArtifact | None:
    """Expose typed mutation validation as a named result-backed JobArtifact."""
    factory = _get_session_factory()
    async with factory() as session, session.begin():
        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        attempt = await session.scalar(
            select(JobAttempt).where(
                JobAttempt.id == attempt_id,
                JobAttempt.job_id == job_id,
                JobAttempt.fence_token == fence_token,
            )
        )
        if job is None or attempt is None:
            raise ArtifactError("validation artifact attempt ownership is stale")
        if not isinstance(job.result, dict) or "validation" not in job.result:
            return None
        existing = await session.scalar(
            select(JobArtifact).where(
                JobArtifact.job_id == job_id,
                JobArtifact.attempt_id == attempt_id,
                JobArtifact.kind == "validation_report",
                JobArtifact.status == "available",
            )
        )
        if existing is not None:
            return existing
        document = {
            "version": 1,
            "job_id": job_id,
            "document": "result",
            "value": {"validation": job.result["validation"]},
        }
        encoded = _encode_virtual(document)
        row = JobArtifact(
            job_id=job_id,
            attempt_id=attempt_id,
            kind="validation_report",
            name="Mutation validation evidence",
            status="available",
            virtual_source={"version": 1, "document": "result", "pointer": "/validation"},
            content_type="application/json",
            size_bytes=len(encoded),
            checksum=hashlib.sha256(encoded).hexdigest(),
            artifact_metadata={"result_pointer": "/validation"},
            retention_class="standard",
            expires_at=datetime.now(UTC) + _retention_delta("standard"),
        )
        session.add(row)
        await session.flush((row,))
        await job_event_writer.append(
            session,
            job_id=job_id,
            attempt_id=attempt_id,
            event_key="artifact.available",
            state=job.phase,
            message="Mutation validation evidence available",
            detail={"artifact_id": row.id, "kind": row.kind, "name": row.name},
            canonical_version=job.fence_token,
        )
        return row


async def repair_terminal_virtual_artifacts(*, limit: int = 100) -> dict[str, int]:
    """Boundedly restore missing canonical result/error evidence without rerunning work."""
    if not 1 <= limit <= 200:
        raise ArtifactError("terminal artifact repair limit is invalid")
    factory = _get_session_factory()
    async with factory() as session:
        jobs = list(
            await session.scalars(
                select(Job)
                .where(Job.phase == "terminal")
                .order_by(Job.terminal_at, Job.id)
                .limit(limit * 2)
            )
        )
        candidates: list[tuple[str, int | None, int, Literal["result", "error"]]] = []
        for job in jobs:
            source: Literal["result", "error"] | None = (
                "result" if job.result is not None else "error" if job.error is not None else None
            )
            if source is None:
                continue
            exists = await session.scalar(
                select(JobArtifact.id).where(
                    JobArtifact.job_id == job.id,
                    JobArtifact.attempt_id == job.current_attempt_id,
                    JobArtifact.kind == f"canonical_{source}",
                    JobArtifact.status == "available",
                )
            )
            if exists is None:
                candidates.append((job.id, job.current_attempt_id, job.fence_token, source))
            if len(candidates) >= limit:
                break

    repaired = failed = 0
    for job_id, attempt_id, fence_token, source in candidates:
        try:
            await register_virtual_artifact(
                job_id=job_id,
                attempt_id=attempt_id,
                fence_token=fence_token,
                source=source,
                name=f"Canonical {source}",
                retention_class="standard",
            )
            repaired += 1
        except Exception:
            failed += 1
    return {"examined": len(candidates), "repaired": repaired, "failed": failed}


async def materialize_virtual_artifact(row: JobArtifact) -> bytes:
    source = (row.virtual_source or {}).get("document")
    if source not in VIRTUAL_SOURCES:
        raise ArtifactError("virtual artifact source is unknown")
    factory = _get_session_factory()
    async with factory() as session:
        job = await session.get(Job, row.job_id)
        if job is None:
            raise ArtifactError("virtual artifact job is missing")
        document = await _virtual_document(
            session, job, source, through_cursor=(row.virtual_source or {}).get("through_cursor")
        )
        if (row.virtual_source or {}).get("pointer") == "/validation":
            value = document.get("value") if isinstance(document, dict) else None
            if not isinstance(value, dict) or "validation" not in value:
                raise ArtifactError("validation artifact source is unavailable")
            document = {
                "version": 1,
                "job_id": row.job_id,
                "document": "result",
                "value": {"validation": value["validation"]},
            }
    encoded = _encode_virtual(document)
    if row.size_bytes != len(encoded) or row.checksum != hashlib.sha256(encoded).hexdigest():
        raise ArtifactError("virtual artifact canonical document changed")
    return encoded


async def _virtual_document(
    session, job: Job, source: str, *, through_cursor: int | None = None
) -> Any:
    if source == "events":
        query = select(JobEvent).where(JobEvent.job_id == job.id)
        if through_cursor is not None:
            query = query.where(JobEvent.id <= through_cursor)
        rows = (
            await session.scalars(
                query.order_by(JobEvent.id.asc()).limit(settings.JOB_ARTIFACT_EVENT_LIMIT + 1)
            )
        ).all()
        if len(rows) > settings.JOB_ARTIFACT_EVENT_LIMIT:
            raise ArtifactError("semantic event document exceeds its bound")
        return {
            "version": 1,
            "job_id": job.id,
            "events": [
                {
                    "cursor": row.id,
                    "event_key": row.event_key,
                    "state": row.state,
                    "stage": row.stage,
                    "message": row.message,
                    "detail": {
                        key: value
                        for key, value in (row.detail or {}).items()
                        if not key.startswith("_")
                    },
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ],
        }
    document = getattr(job, source)
    if document is None:
        raise ArtifactError("canonical artifact document is unavailable")
    return {"version": 1, "job_id": job.id, "document": source, "value": document}


def _encode_virtual(document: Any) -> bytes:
    sanitized = _sanitize(document, CentralRedactor.configured(), depth=0)
    try:
        encoded = json.dumps(
            sanitized, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ArtifactError("canonical artifact document is malformed") from exc
    if len(encoded) > settings.JOB_ARTIFACT_VIRTUAL_MAX_BYTES:
        raise ArtifactError("canonical artifact document exceeds its bound")
    return encoded


def _sanitize(value: Any, redactor: CentralRedactor, *, depth: int) -> Any:
    if depth > 12:
        raise ArtifactError("canonical artifact document is too deeply nested")
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not (-1e15 <= value <= 1e15):
            raise ArtifactError("canonical artifact number is invalid")
        return value
    if isinstance(value, str):
        return redactor.redact_text(value[: settings.JOB_ARTIFACT_STRING_CHARS])[0]
    if isinstance(value, list | tuple):
        if len(value) > 1000:
            raise ArtifactError("canonical artifact array exceeds its bound")
        return [_sanitize(item, redactor, depth=depth + 1) for item in value]
    if isinstance(value, Mapping):
        if len(value) > 1000:
            raise ArtifactError("canonical artifact object exceeds its bound")
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 100:
                raise ArtifactError("canonical artifact key is invalid")
            if re.search(r"(?i)(password|secret|token|api[_-]?key|authorization)", key):
                result[key] = "[REDACTED]"
            else:
                result[key] = _sanitize(item, redactor, depth=depth + 1)
        return result
    raise ArtifactError("canonical artifact value type is invalid")


def _retention_delta(retention_class: str) -> timedelta | None:
    if retention_class == "pinned":
        return None
    return timedelta(
        days={"ephemeral": 7, "standard": 30, "extended": 365}[retention_class]
    )


def physical_artifact_file(row: JobArtifact, *, data_dir: str | Path | None = None):
    if row.storage_key is None:
        raise ArtifactError("artifact is not physical")
    boundary = artifact_boundary(data_dir or settings.DATA_DIR)
    return boundary, boundary.from_key("data", row.storage_key)


async def verify_physical_artifact(row: JobArtifact) -> tuple[FilesystemBoundary, ClassifiedPath]:
    boundary, classified = physical_artifact_file(row)
    try:
        fd = await asyncio.to_thread(boundary.open_read, classified)
    except FilesystemBoundaryError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            raise ArtifactMissingError("physical artifact storage is missing") from exc
        raise
    digest = hashlib.sha256()
    size = 0
    try:
        while chunk := await asyncio.to_thread(os.read, fd, 1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    finally:
        os.close(fd)
    if size != row.size_bytes or digest.hexdigest() != row.checksum:
        raise ArtifactError("physical artifact storage is corrupt")
    return boundary, classified


async def expire_artifacts(*, data_dir: str | Path, limit: int = 50) -> dict[str, int]:
    """Claim, physically remove, verify, then expire artifact metadata."""
    if not 1 <= limit <= 100:
        raise ArtifactError("artifact cleanup limit is invalid")
    factory = _get_session_factory()
    now = datetime.now(UTC)
    claimed: list[tuple[int, str | None]] = []
    async with factory() as session, session.begin():
        rows = (
            await session.scalars(
                select(JobArtifact)
                .where(JobArtifact.status == "available", JobArtifact.expires_at <= now)
                .order_by(JobArtifact.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            row.status = "expiring"
            claimed.append((row.id, row.storage_key))

    boundary = artifact_boundary(data_dir)
    deleted = missing = failed = 0
    for artifact_id, key in claimed:
        disposition = "virtual"
        try:
            if key is not None:
                classified = boundary.from_key("data", key)
                removed = await asyncio.to_thread(
                    boundary.delete_file, classified, missing_ok=True
                )
                disposition = "deleted" if removed else "missing"
                try:
                    fd = await asyncio.to_thread(boundary.open_read, classified)
                except (FileNotFoundError, FilesystemBoundaryError):
                    pass
                else:
                    os.close(fd)
                    raise ArtifactError("artifact remained after physical expiration")
            async with factory() as session, session.begin():
                row = await session.scalar(
                    select(JobArtifact)
                    .where(
                        JobArtifact.id == artifact_id,
                        JobArtifact.status == "expiring",
                    )
                    .with_for_update()
                )
                if row is None:
                    raise ArtifactError("artifact expiration claim was lost")
                row.status = "expired"
                row.artifact_metadata = {
                    **(row.artifact_metadata or {}),
                    "expiration_disposition": disposition,
                    "expired_at": now.isoformat(),
                }
                job = await session.get(Job, row.job_id)
                if job is not None:
                    await job_event_writer.append(
                        session,
                        job_id=row.job_id,
                        attempt_id=row.attempt_id,
                        event_key="artifact.expired",
                        state=job.phase,
                        message="Job artifact expired",
                        detail={
                            "artifact_id": row.id,
                            "kind": row.kind,
                            "name": row.name,
                            "disposition": disposition,
                        },
                        canonical_version=job.fence_token,
                    )
            deleted += disposition == "deleted"
            missing += disposition == "missing"
        except Exception as exc:
            failed += 1
            async with factory() as session, session.begin():
                row = await session.scalar(
                    select(JobArtifact)
                    .where(
                        JobArtifact.id == artifact_id,
                        JobArtifact.status == "expiring",
                    )
                    .with_for_update()
                )
                if row is not None:
                    row.status = "available"
                    row.artifact_metadata = {
                        **(row.artifact_metadata or {}),
                        "expiration_failure": type(exc).__name__[:40],
                    }
    return {
        "claimed": len(claimed),
        "expired": len(claimed) - failed,
        "deleted": deleted,
        "missing": missing,
        "failed": failed,
    }


async def expire_logs(*, data_dir: str | Path, limit: int = 50) -> dict[str, int]:
    """Expire sealed physical logs before their owning job rows become eligible."""
    if not 1 <= limit <= 100:
        raise ArtifactError("log cleanup limit is invalid")
    factory = _get_session_factory()
    now = datetime.now(UTC)
    claimed: list[tuple[int, str]] = []
    async with factory() as session, session.begin():
        rows = (
            await session.scalars(
                select(JobLog)
                .where(JobLog.seal_status == "sealed", JobLog.expires_at <= now)
                .order_by(JobLog.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            row.seal_status = "expiring"
            claimed.append((row.id, row.storage_key))

    boundary = artifact_boundary(data_dir)
    deleted = missing = failed = 0
    for log_id, key in claimed:
        try:
            classified = boundary.from_key("data", key)
            removed = await asyncio.to_thread(
                boundary.delete_file, classified, missing_ok=True
            )
            disposition = "deleted" if removed else "missing"
            try:
                fd = await asyncio.to_thread(boundary.open_read, classified)
            except (FileNotFoundError, FilesystemBoundaryError):
                pass
            else:
                os.close(fd)
                raise ArtifactError("log remained after physical expiration")
            async with factory() as session, session.begin():
                row = await session.scalar(
                    select(JobLog)
                    .where(JobLog.id == log_id, JobLog.seal_status == "expiring")
                    .with_for_update()
                )
                if row is None:
                    raise ArtifactError("log expiration claim was lost")
                row.seal_status = "expired"
                row.failure_code = (
                    "retention_source_missing" if disposition == "missing" else None
                )
            deleted += disposition == "deleted"
            missing += disposition == "missing"
        except Exception as exc:
            failed += 1
            async with factory() as session, session.begin():
                row = await session.scalar(
                    select(JobLog)
                    .where(JobLog.id == log_id, JobLog.seal_status == "expiring")
                    .with_for_update()
                )
                if row is not None:
                    row.seal_status = "sealed"
                    row.failure_code = f"retention_{type(exc).__name__}"[:40]
    return {
        "claimed": len(claimed),
        "expired": len(claimed) - failed,
        "deleted": deleted,
        "missing": missing,
        "failed": failed,
    }


async def reconcile_artifacts(*, data_dir: str | Path, limit: int = 100) -> dict[str, int]:
    """Report bounded missing tracked files and untracked managed files; never delete."""
    if not 1 <= limit <= 200:
        raise ArtifactError("artifact reconciliation limit is invalid")
    factory = _get_session_factory()
    async with factory() as session:
        tracked = set(
            await session.scalars(
                select(JobArtifact.storage_key).where(
                    JobArtifact.storage_key.is_not(None), JobArtifact.status == "available"
                )
            )
        )
    root = Path(data_dir) / "jmc3" / "evidence" / "artifacts"
    missing = sum(1 for key in list(tracked)[:limit] if not (Path(data_dir) / key).is_file())
    untracked = 0
    if root.exists():
        for path in root.rglob("artifact-*"):
            if untracked >= limit:
                break
            key = path.relative_to(data_dir).as_posix()
            if path.is_file() and key not in tracked:
                untracked += 1
    return {"missing": missing, "untracked": untracked}
