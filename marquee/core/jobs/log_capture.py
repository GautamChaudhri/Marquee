"""Bounded, redacted, per-attempt JSONL capture and sealing."""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import contextvars
import gzip
import hashlib
import json
import logging
import math
import os
import re
import threading
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update

from marquee.config import settings
from marquee.core.filesystem import (
    ClassifiedPath,
    FilesystemBoundary,
    FilesystemBoundaryError,
    boundary_for_roots,
)
from marquee.core.jobs.event_service import job_event_writer
from marquee.core.jobs.process_identity import (
    IdentityStatus,
    ProcessIdentity,
    verify_process_identity,
)
from marquee.database import _get_session_factory
from marquee.models.job import Job, JobAttempt
from marquee.models.job_evidence import JobLog

logger = logging.getLogger(__name__)

LOG_SOURCES = ("python", "stdout", "stderr", "system")
LOG_LEVELS = ("debug", "info", "warning", "error")
TRUNCATION_RESERVE_BYTES = 1024
MAX_LOG_FIELDS = 16
MAX_LOG_FIELD_CHARS = 500
_IDENTITY = re.compile(r"^[a-f0-9]{32}$")
_STAGE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_SENSITIVE_ARGUMENT = re.compile(
    r"^--?(?:api[_-]?key|authorization|callback[_-]?token|credential|database[_-]?url|"
    r"password|passwd|secret|token)$",
    re.IGNORECASE,
)
_JOINED_ARGUMENT = re.compile(
    r"^(--?(?:api[_-]?key|authorization|callback[_-]?token|credential|database[_-]?url|"
    r"password|passwd|secret|token))=(.*)$",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)(\bauthorization\s*:\s*bearer\s+)[^\s,;]{1,512}")
_SECRET_HEADER = re.compile(
    r"(?i)(\b(?:x-api-key|api-key|x-auth-token)\s*:\s*)[^\s,;]{1,512}"
)
_SECRET_VALUE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|callback[_-]?token|password|"
    r"passwd|secret|token)\s*[=:]\s*)[^\s&;,]{1,512}"
)
_URL_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/@\s]{1,512}@")


class AttemptLogError(RuntimeError):
    """Raised when attempt-log ownership or storage cannot be established safely."""


class AttemptLogLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    cursor: int = Field(ge=1)
    timestamp: datetime
    level: Literal["debug", "info", "warning", "error"]
    source: Literal["python", "stdout", "stderr", "system"]
    stage: str | None = None
    message: str = Field(max_length=16_384)
    fields: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("stage")
    @classmethod
    def valid_stage(cls, value: str | None) -> str | None:
        if value is not None and (len(value) > 80 or not _STAGE.fullmatch(value)):
            raise ValueError("log stage must be a bounded semantic key")
        return value

    @field_validator("fields")
    @classmethod
    def bounded_fields(
        cls, value: dict[str, str | int | float | bool | None]
    ) -> dict[str, str | int | float | bool | None]:
        if len(value) > MAX_LOG_FIELDS:
            raise ValueError("log fields exceed the fixed entry bound")
        for key, field_value in value.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", key):
                raise ValueError("log field key is not allowlisted")
            if re.search(r"authorization|command|environment|password|path|secret|token", key):
                raise ValueError("sensitive log field key is prohibited")
            if isinstance(field_value, str) and len(field_value) > MAX_LOG_FIELD_CHARS:
                raise ValueError("log field value exceeds the fixed character bound")
            if isinstance(field_value, float) and not math.isfinite(field_value):
                raise ValueError("log field number must be finite")
        return value


class CentralRedactor:
    """Apply configured exact-value and bounded structural redaction."""

    marker = "[REDACTED]"

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        normalized = {
            value
            for value in secrets
            if isinstance(value, str) and value and len(value) <= 2048
        }
        self._secrets = tuple(sorted(normalized, key=len, reverse=True))
        self.overlap = max((len(value) for value in self._secrets), default=0) + 1024

    @classmethod
    def configured(cls) -> CentralRedactor:
        return cls(settings.job_log_redaction_secrets)

    def redact_text(self, value: str) -> tuple[str, int]:
        redacted = value
        replacements = 0
        for secret in self._secrets:
            count = redacted.count(secret)
            if count:
                redacted = redacted.replace(secret, self.marker)
                replacements += count
        for pattern, replacement in (
            (_BEARER, rf"\1{self.marker}"),
            (_SECRET_HEADER, rf"\1{self.marker}"),
            (_URL_USERINFO, rf"\1{self.marker}@"),
            (_SECRET_VALUE, rf"\1{self.marker}"),
        ):
            redacted, count = pattern.subn(replacement, redacted)
            replacements += count
        return redacted, replacements

    def redact_arguments(self, arguments: Sequence[str]) -> tuple[str, ...]:
        safe: list[str] = []
        hide_next = False
        for argument in arguments:
            if hide_next:
                safe.append(self.marker)
                hide_next = False
                continue
            if _SENSITIVE_ARGUMENT.fullmatch(argument):
                safe.append(argument)
                hide_next = True
                continue
            joined = _JOINED_ARGUMENT.fullmatch(argument)
            if joined:
                safe.append(f"{joined.group(1)}={self.marker}")
                continue
            redacted, _ = self.redact_text(argument)
            safe.append(redacted)
        return tuple(safe)


@dataclass(frozen=True, slots=True)
class _Observation:
    source: str
    data: bytes | str
    level: str = "info"
    stage: str | None = None
    fields: Mapping[str, str | int | float | bool | None] = field(default_factory=dict)
    eof: bool = False


@dataclass(slots=True)
class _StreamState:
    decoder: codecs.IncrementalDecoder
    buffer: str = ""


@dataclass(frozen=True, slots=True)
class AttemptLogFile:
    boundary: FilesystemBoundary
    directory: ClassifiedPath
    path: ClassifiedPath
    storage_key: str


class AttemptLogFiles:
    def __init__(self, boundary: FilesystemBoundary, *, root_name: str = "data") -> None:
        self.boundary = boundary
        self.root_name = root_name

    @classmethod
    def for_data_dir(cls, data_dir: str | Path) -> AttemptLogFiles:
        root = Path(data_dir).resolve(strict=True)
        return cls(
            boundary_for_roots(
                {"data": root}, access="read_write", purpose="attempt log evidence"
            )
        )

    def create(self, *, job_id: str, attempt_id: int, segment: int = 0) -> tuple[AttemptLogFile, int]:
        if not _IDENTITY.fullmatch(job_id) or attempt_id < 1 or segment < 0:
            raise AttemptLogError("attempt log identity is invalid")
        directory = self.boundary.from_key(
            self.root_name, f"jmc3/evidence/logs/{job_id}/{attempt_id}"
        )
        storage_key = f"{directory.key.value}/segment-{segment}.jsonl"
        path = self.boundary.from_key(self.root_name, storage_key)
        with contextlib.suppress(FileExistsError):
            self.boundary.create_directory(directory, parents=True)
        try:
            fd = self.boundary.create_file(path)
        except (OSError, FilesystemBoundaryError) as exc:
            raise AttemptLogError("attempt log file cannot be created safely") from exc
        return AttemptLogFile(self.boundary, directory, path, storage_key), fd

    def ensure_writable(self) -> bool:
        directory = self.boundary.from_key(self.root_name, "jmc3/evidence/logs")
        with contextlib.suppress(FileExistsError):
            self.boundary.create_directory(directory, parents=True)
        temporary, fd = self.boundary.temporary_file(directory, prefix=".readiness-")
        try:
            _write_all(fd, b"ready")
            os.fsync(fd)
        finally:
            os.close(fd)
            self.boundary.delete_file(temporary, missing_ok=True)
        return True

    def existing(self, storage_key: str) -> AttemptLogFile:
        path = self.boundary.from_key(self.root_name, storage_key)
        physical = path.root.resolved() / path.key.value
        classified = self.boundary.classify(
            physical, roots=(self.root_name,), require_file=True, write=True
        )
        directory_key = "/".join(classified.key.parts[:-1])
        directory = self.boundary.classify(
            classified.root.resolved() / directory_key,
            roots=(self.root_name,),
            require_exists=True,
            write=True,
        )
        return AttemptLogFile(self.boundary, directory, classified, storage_key)

    def seal(self, log_file: AttemptLogFile) -> tuple[str, int]:
        checksum, stored, compressed = self._checksum_file(log_file)
        if compressed:
            return checksum, stored
        temporary, temporary_fd = self.boundary.temporary_file(
            log_file.directory, prefix=".log-seal-"
        )
        source_fd = self.boundary.open_read(log_file.path)
        try:
            with os.fdopen(source_fd, "rb", closefd=True) as source, os.fdopen(
                temporary_fd, "wb", closefd=True
            ) as target:
                with gzip.GzipFile(fileobj=target, mode="wb", mtime=0) as compressed:
                    while chunk := source.read(64 * 1024):
                        compressed.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            self.boundary.atomic_replace(temporary, log_file.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(source_fd)
            with contextlib.suppress(OSError):
                os.close(temporary_fd)
            with contextlib.suppress(Exception):
                self.boundary.delete_file(temporary, missing_ok=True)
            raise
        digest = hashlib.sha256()
        stored = 0
        sealed_fd = self.boundary.open_read(log_file.path)
        try:
            while chunk := os.read(sealed_fd, 64 * 1024):
                stored += len(chunk)
                digest.update(chunk)
        finally:
            os.close(sealed_fd)
        return digest.hexdigest(), stored

    def inspect(self, row: JobLog) -> tuple[int, int, int, bool]:
        log_file = self.existing(row.storage_key)
        fd = self.boundary.open_read(log_file.path)
        compressed = _gzip_fd(fd)
        byte_count = 0
        line_count = 0
        last_cursor = 0
        try:
            with os.fdopen(fd, "rb", closefd=True) as raw:
                stream = gzip.GzipFile(fileobj=raw, mode="rb") if compressed else raw
                try:
                    while encoded := stream.readline(24 * 1024 + 1):
                        if len(encoded) > 24 * 1024 or not encoded.endswith(b"\n"):
                            raise AttemptLogError("attempt log contains an invalid stored line")
                        line = AttemptLogLine.model_validate_json(encoded)
                        if line.cursor <= last_cursor:
                            raise AttemptLogError("attempt log cursor order is invalid")
                        last_cursor = line.cursor
                        line_count += 1
                        byte_count += len(encoded)
                finally:
                    if compressed:
                        stream.close()
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        return byte_count, line_count, last_cursor, compressed

    def read_lines(
        self,
        row: JobLog,
        *,
        after: int,
        limit: int,
        source: str | None = None,
        level: str | None = None,
    ) -> tuple[list[AttemptLogLine], int | None, bool]:
        log_file = self.existing(row.storage_key)
        fd = self.boundary.open_read(log_file.path)
        values: list[AttemptLogLine] = []
        compressed = _gzip_fd(fd)
        try:
            with os.fdopen(fd, "rb", closefd=True) as raw:
                stream = gzip.GzipFile(fileobj=raw, mode="rb") if compressed else raw
                try:
                    while encoded := stream.readline(24 * 1024 + 1):
                        if len(encoded) > 24 * 1024 or not encoded.endswith(b"\n"):
                            raise AttemptLogError("attempt log contains an invalid stored line")
                        try:
                            line = AttemptLogLine.model_validate_json(encoded)
                        except ValueError as exc:
                            raise AttemptLogError("attempt log contains invalid JSONL") from exc
                        if line.cursor <= after:
                            continue
                        if source is not None and line.source != source:
                            continue
                        if level is not None and line.level != level:
                            continue
                        values.append(line)
                        if len(values) > limit:
                            break
                finally:
                    if compressed:
                        stream.close()
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        has_more = len(values) > limit
        values = values[:limit]
        return values, values[-1].cursor if has_more and values else None, compressed

    def checksum(self, row: JobLog) -> tuple[str, int, bool]:
        log_file = self.existing(row.storage_key)
        return self._checksum_file(log_file)

    def _checksum_file(self, log_file: AttemptLogFile) -> tuple[str, int, bool]:
        fd = self.boundary.open_read(log_file.path)
        digest = hashlib.sha256()
        size = 0
        compressed = _gzip_fd(fd)
        try:
            while chunk := os.read(fd, 64 * 1024):
                size += len(chunk)
                digest.update(chunk)
        finally:
            os.close(fd)
        return digest.hexdigest(), size, compressed

    def download_response(self, row: JobLog):
        log_file = self.existing(row.storage_key)
        suffix = ".jsonl.gz" if row.compression == "gzip" else ".jsonl"
        response = self.boundary.response(
            log_file.path,
            media_type="application/gzip"
            if row.compression == "gzip"
            else "application/x-ndjson",
            filename=f"job-{row.job_id}-attempt-{row.attempt_id}{suffix}",
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


class AttemptLogSink:
    """Own one open segment and discard promptly after the persisted cap."""

    def __init__(
        self,
        *,
        job_id: str,
        attempt_id: int,
        fence_token: int,
        log_id: int,
        log_file: AttemptLogFile,
        fd: int,
        redactor: CentralRedactor,
        cap_bytes: int,
    ) -> None:
        if cap_bytes < 2 * TRUNCATION_RESERVE_BYTES:
            raise AttemptLogError("attempt log cap is too small for truncation evidence")
        self.job_id = job_id
        self.attempt_id = attempt_id
        self.fence_token = fence_token
        self.log_id = log_id
        self.log_file = log_file
        self._fd = fd
        self._redactor = redactor
        self._cap_bytes = cap_bytes
        self._queue: asyncio.Queue[_Observation | None] = asyncio.Queue(
            maxsize=settings.JOB_LOG_QUEUE_SIZE
        )
        self._task = asyncio.create_task(self._run(), name=f"attempt-log-{attempt_id}")
        self._streams: dict[str, _StreamState] = {}
        self._cursor = 0
        self._byte_count = 0
        self._line_count = 0
        self._truncated = False
        self._accepting = True
        self._python_dropped = 0
        self._redaction_count = 0
        self._capture_failure: str | None = None
        self._loop = asyncio.get_running_loop()

    @classmethod
    async def create(
        cls,
        *,
        job_id: str,
        attempt_id: int,
        fence_token: int,
        data_dir: str | Path,
        redactor: CentralRedactor | None = None,
        cap_bytes: int | None = None,
    ) -> AttemptLogSink:
        files = AttemptLogFiles.for_data_dir(data_dir)
        log_file, fd = files.create(job_id=job_id, attempt_id=attempt_id)
        factory = _get_session_factory()
        try:
            async with factory() as session, session.begin():
                attempt = await session.scalar(
                    select(JobAttempt).where(
                        JobAttempt.id == attempt_id,
                        JobAttempt.job_id == job_id,
                        JobAttempt.fence_token == fence_token,
                        JobAttempt.phase.in_(("admitted", "running")),
                    )
                )
                if attempt is None:
                    raise AttemptLogError("attempt log ownership is stale")
                row = JobLog(
                    job_id=job_id,
                    attempt_id=attempt_id,
                    segment=0,
                    storage_key=log_file.storage_key,
                    format="jsonl",
                    encoding="utf-8",
                    compression="none",
                    redacted=True,
                    seal_status="open",
                    retention_class="standard",
                )
                session.add(row)
                await session.flush((row,))
                log_id = row.id
        except BaseException:
            os.close(fd)
            with contextlib.suppress(Exception):
                files.boundary.delete_file(log_file.path, missing_ok=True)
            raise
        return cls(
            job_id=job_id,
            attempt_id=attempt_id,
            fence_token=fence_token,
            log_id=log_id,
            log_file=log_file,
            fd=fd,
            redactor=redactor or CentralRedactor.configured(),
            cap_bytes=cap_bytes or settings.JOB_LOG_CAP_BYTES,
        )

    @property
    def truncated(self) -> bool:
        return self._truncated

    async def feed_pipe(self, source: str, chunk: bytes, eof: bool = False) -> None:
        if source not in {"stdout", "stderr"}:
            raise AttemptLogError("pipe source is not allowlisted")
        if not self._accepting or self._truncated or self._capture_failure:
            return
        await self._queue.put(_Observation(source=source, data=chunk, eof=eof))

    async def write(
        self,
        *,
        source: Literal["python", "system"],
        message: str,
        level: Literal["debug", "info", "warning", "error"] = "info",
        stage: str | None = None,
        fields: Mapping[str, str | int | float | bool | None] | None = None,
    ) -> None:
        if not self._accepting or self._truncated or self._capture_failure:
            return
        await self._queue.put(
            _Observation(
                source=source,
                data=message,
                level=level,
                stage=stage,
                fields=fields or {},
            )
        )

    def submit_python(self, record: logging.LogRecord) -> None:
        if not self._accepting or self._truncated or self._capture_failure:
            return
        observation = _Observation(
            source="python",
            data=record.getMessage(),
            level=_python_level(record.levelno),
            fields={"logger": record.name[:MAX_LOG_FIELD_CHARS]},
        )

        def enqueue() -> None:
            try:
                self._queue.put_nowait(observation)
            except asyncio.QueueFull:
                self._python_dropped += 1

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is self._loop:
            enqueue()
        else:
            self._loop.call_soon_threadsafe(enqueue)

    @asynccontextmanager
    async def capture_python_logs(self) -> AsyncIterator[None]:
        token = _ATTEMPT_LOG_SINK.set(self)
        _attach_python_handler()
        try:
            yield
        finally:
            _ATTEMPT_LOG_SINK.reset(token)
            _detach_python_handler()

    async def seal(self) -> bool:
        if not self._accepting:
            return False
        self._accepting = False
        for source in tuple(self._streams):
            await self._queue.put(_Observation(source=source, data=b"", eof=True))
        if self._python_dropped:
            await self._queue.put(
                _Observation(
                    source="system",
                    data="Python log records were dropped because the bounded capture queue filled.",
                    level="warning",
                    fields={"dropped_records": self._python_dropped},
                )
        )
        await self._queue.put(None)
        try:
            await self._task
            os.fsync(self._fd)
        except Exception as exc:
            self._capture_failure = type(exc).__name__[:40]
        finally:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1
        if self._capture_failure:
            await self._mark_failed(self._capture_failure)
            return False
        try:
            await self._sync_metadata()
        except Exception as exc:
            await self._mark_failed(type(exc).__name__[:40])
            return False
        files = AttemptLogFiles(self.log_file.boundary)
        try:
            checksum, stored_bytes = await asyncio.to_thread(files.seal, self.log_file)
            closed_at = datetime.now(UTC)
            factory = _get_session_factory()
            async with factory() as session, session.begin():
                row = await session.scalar(
                    select(JobLog).where(JobLog.id == self.log_id).with_for_update()
                )
                if row is None or row.seal_status not in {"open", "failed"}:
                    raise AttemptLogError("attempt log metadata cannot be sealed")
                job = await session.scalar(select(Job).where(Job.id == self.job_id))
                if job is None:
                    raise AttemptLogError("attempt log job disappeared during seal")
                row.compression = "gzip"
                row.checksum = checksum
                row.stored_byte_count = stored_bytes
                row.seal_status = "sealed"
                row.failure_code = None
                row.closed_at = closed_at
                row.expires_at = closed_at + timedelta(days=settings.JOB_LOG_RETENTION_DAYS)
                if (
                    job.current_attempt_id == self.attempt_id
                    and job.fence_token == self.fence_token
                ):
                    await job_event_writer.append(
                        session,
                        job_id=self.job_id,
                        attempt_id=self.attempt_id,
                        event_key="log.available",
                        state=job.phase,
                        message="Attempt log sealed and available",
                        detail={
                            "attempt_id": self.attempt_id,
                            "truncated": self._truncated,
                        },
                        canonical_version=self.fence_token,
                    )
            return True
        except Exception as exc:
            logger.error("Attempt log sealing failed for log %s", self.log_id, exc_info=True)
            factory = _get_session_factory()
            async with factory() as session, session.begin():
                await session.execute(
                    update(JobLog)
                    .where(JobLog.id == self.log_id, JobLog.seal_status == "open")
                    .values(seal_status="failed", failure_code=type(exc).__name__[:40])
                )
            return False

    async def _run(self) -> None:
        while True:
            observation = await self._queue.get()
            try:
                if observation is None:
                    if self._capture_failure:
                        return
                    for source in tuple(self._streams):
                        await self._consume_stream(
                            _Observation(source=source, data=b"", eof=True)
                        )
                    return
                if self._capture_failure:
                    continue
                if observation.source in {"stdout", "stderr"}:
                    await self._consume_stream(observation)
                else:
                    await self._persist_message(observation)
            except Exception as exc:
                self._capture_failure = type(exc).__name__[:40]
            finally:
                self._queue.task_done()

    async def _consume_stream(self, observation: _Observation) -> None:
        state = self._streams.setdefault(
            observation.source,
            _StreamState(codecs.getincrementaldecoder("utf-8")(errors="replace")),
        )
        raw = observation.data
        assert isinstance(raw, bytes)
        state.buffer += state.decoder.decode(raw, final=observation.eof)
        while "\n" in state.buffer:
            line, state.buffer = state.buffer.split("\n", 1)
            await self._persist_text(
                source=observation.source,
                message=line.removesuffix("\r"),
                level=observation.level,
                fields=observation.fields,
            )
        overlap = self._redactor.overlap
        while len(state.buffer) > settings.JOB_LOG_MESSAGE_CHARS + 2 * overlap:
            safe, replacements = self._redactor.redact_text(state.buffer)
            self._redaction_count += replacements
            safe_length = len(safe) - overlap
            prefix = safe[:safe_length]
            state.buffer = safe[safe_length:]
            for start in range(0, len(prefix), settings.JOB_LOG_MESSAGE_CHARS):
                await self._persist_text(
                    source=observation.source,
                    message=prefix[start : start + settings.JOB_LOG_MESSAGE_CHARS],
                    level=observation.level,
                    fields={**observation.fields, "continued": True},
                )
        if observation.eof:
            if state.buffer:
                await self._persist_text(
                    source=observation.source,
                    message=state.buffer.removesuffix("\r"),
                    level=observation.level,
                    fields=observation.fields,
                )
            self._streams.pop(observation.source, None)

    async def _persist_message(self, observation: _Observation) -> None:
        raw = observation.data
        assert isinstance(raw, str)
        for start in range(0, max(len(raw), 1), settings.JOB_LOG_MESSAGE_CHARS):
            await self._persist_text(
                source=observation.source,
                message=raw[start : start + settings.JOB_LOG_MESSAGE_CHARS],
                level=observation.level,
                stage=observation.stage,
                fields=observation.fields,
            )

    async def _persist_text(
        self,
        *,
        source: str,
        message: str,
        level: str,
        stage: str | None = None,
        fields: Mapping[str, str | int | float | bool | None],
    ) -> None:
        if self._truncated:
            return
        safe_message, replacements = self._redactor.redact_text(message)
        safe_fields: dict[str, str | int | float | bool | None] = {}
        for key, value in fields.items():
            if isinstance(value, str):
                value, count = self._redactor.redact_text(value)
                replacements += count
            safe_fields[key] = value
        self._redaction_count += replacements
        next_cursor = self._cursor + 1
        line = AttemptLogLine(
            cursor=next_cursor,
            timestamp=datetime.now(UTC),
            level=level,
            source=source,
            stage=stage,
            message=safe_message,
            fields=safe_fields,
        )
        encoded = _encode_line(line)
        if self._byte_count + len(encoded) > self._cap_bytes - TRUNCATION_RESERVE_BYTES:
            await self._truncate()
            return
        _write_all(self._fd, encoded)
        self._cursor = next_cursor
        self._byte_count += len(encoded)
        self._line_count += 1
        if self._line_count % settings.JOB_LOG_METADATA_LINES == 0:
            await self._sync_metadata()

    async def _truncate(self) -> None:
        if self._truncated:
            return
        line = AttemptLogLine(
            cursor=self._cursor + 1,
            timestamp=datetime.now(UTC),
            level="warning",
            source="system",
            message="Attempt log reached the persisted byte cap; later output was discarded.",
            fields={"cap_bytes": self._cap_bytes, "truncated": True},
        )
        encoded = _encode_line(line)
        if self._byte_count + len(encoded) > self._cap_bytes:
            raise AttemptLogError("reserved truncation record no longer fits the log cap")
        _write_all(self._fd, encoded)
        self._cursor += 1
        self._byte_count += len(encoded)
        self._line_count += 1
        self._truncated = True
        await self._sync_metadata()

    async def _sync_metadata(self) -> None:
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            result = await session.execute(
                update(JobLog)
                .where(JobLog.id == self.log_id, JobLog.seal_status == "open")
                .values(
                    byte_count=self._byte_count,
                    line_count=self._line_count,
                    last_cursor=self._cursor,
                    stored_byte_count=self._byte_count,
                    truncated=self._truncated,
                )
            )
            if result.rowcount != 1:
                raise AttemptLogError("attempt log metadata ownership changed")

    async def _mark_failed(self, code: str) -> None:
        factory = _get_session_factory()
        async with factory() as session, session.begin():
            await session.execute(
                update(JobLog)
                .where(JobLog.id == self.log_id, JobLog.seal_status == "open")
                .values(
                    byte_count=self._byte_count,
                    line_count=self._line_count,
                    last_cursor=self._cursor,
                    stored_byte_count=self._byte_count,
                    truncated=self._truncated,
                    seal_status="failed",
                    failure_code=code[:40],
                )
            )


async def recover_abandoned_attempt_logs(
    *, worker_node: str, data_dir: str | Path, limit: int = 50
) -> dict[str, int]:
    """Seal only finished attempts whose recorded writer process is positively gone."""
    if not worker_node or len(worker_node) > 100 or not 1 <= limit <= 100:
        raise AttemptLogError("attempt log recovery parameters are invalid")
    factory = _get_session_factory()
    claimed: list[tuple[int, int]] = []
    counts = {"recovered": 0, "deferred": 0, "failed": 0}
    async with factory() as session, session.begin():
        rows = (
            await session.execute(
                select(JobLog, JobAttempt)
                .join(JobAttempt, JobAttempt.id == JobLog.attempt_id)
                .where(
                    JobLog.seal_status.in_(("open", "failed", "recovering")),
                    JobAttempt.worker_node_id == worker_node,
                    JobAttempt.phase == "finished",
                )
                .order_by(JobLog.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row, attempt in rows:
            if not _attempt_writer_is_dead(attempt, worker_node=worker_node):
                counts["deferred"] += 1
                continue
            row.seal_status = "recovering"
            row.failure_code = None
            claimed.append((row.id, attempt.fence_token))

    files = AttemptLogFiles.for_data_dir(data_dir)
    for log_id, fence_token in claimed:
        try:
            async with factory() as session:
                row = await session.get(JobLog, log_id)
                if row is None or row.seal_status != "recovering":
                    counts["deferred"] += 1
                    continue
                byte_count, line_count, last_cursor, _ = await asyncio.to_thread(
                    files.inspect, row
                )
                log_file = files.existing(row.storage_key)
                checksum, stored_bytes = await asyncio.to_thread(files.seal, log_file)
            closed_at = datetime.now(UTC)
            async with factory() as session, session.begin():
                row = await session.scalar(
                    select(JobLog)
                    .where(JobLog.id == log_id, JobLog.seal_status == "recovering")
                    .with_for_update()
                )
                if row is None:
                    counts["deferred"] += 1
                    continue
                job = await session.scalar(select(Job).where(Job.id == row.job_id))
                row.byte_count = byte_count
                row.line_count = line_count
                row.last_cursor = last_cursor
                row.stored_byte_count = stored_bytes
                row.compression = "gzip"
                row.checksum = checksum
                row.closed_at = closed_at
                row.expires_at = closed_at + timedelta(days=settings.JOB_LOG_RETENTION_DAYS)
                row.seal_status = "sealed"
                row.failure_code = None
                if (
                    job is not None
                    and job.current_attempt_id == row.attempt_id
                    and job.fence_token == fence_token
                ):
                    await job_event_writer.append(
                        session,
                        job_id=row.job_id,
                        attempt_id=row.attempt_id,
                        event_key="log.available",
                        state=job.phase,
                        message="Abandoned attempt log recovered and sealed",
                        detail={"attempt_id": row.attempt_id, "truncated": row.truncated},
                        canonical_version=fence_token,
                    )
            counts["recovered"] += 1
        except Exception as exc:
            logger.error("Attempt log recovery failed for log %s", log_id, exc_info=True)
            async with factory() as session, session.begin():
                await session.execute(
                    update(JobLog)
                    .where(JobLog.id == log_id, JobLog.seal_status == "recovering")
                    .values(seal_status="failed", failure_code=type(exc).__name__[:40])
                )
            counts["failed"] += 1
    return counts


def _attempt_writer_is_dead(attempt: JobAttempt, *, worker_node: str) -> bool:
    if attempt.worker_node_id != worker_node or attempt.phase != "finished":
        return False
    if attempt.process_id is None:
        return True
    ticks = (attempt.metrics or {}).get("process_start_ticks")
    if (
        attempt.host_boot_id is None
        or attempt.process_group_id is None
        or isinstance(ticks, bool)
        or not isinstance(ticks, int)
    ):
        return False
    identity = ProcessIdentity(
        worker_node=worker_node,
        host_boot_id=attempt.host_boot_id,
        pid=attempt.process_id,
        process_group_id=attempt.process_group_id,
        process_start_ticks=ticks,
        cgroup_path=attempt.cgroup_path,
    )
    return verify_process_identity(identity) in {IdentityStatus.DEAD, IdentityStatus.MISMATCH}


def _encode_line(line: AttemptLogLine) -> bytes:
    return (
        json.dumps(
            line.model_dump(mode="json"),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _gzip_fd(fd: int) -> bool:
    magic = os.read(fd, 2)
    os.lseek(fd, 0, os.SEEK_SET)
    return magic == b"\x1f\x8b"


def _write_all(fd: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("attempt log write made no progress")
        view = view[written:]


def _python_level(level: int) -> Literal["debug", "info", "warning", "error"]:
    if level >= logging.ERROR:
        return "error"
    if level >= logging.WARNING:
        return "warning"
    if level >= logging.INFO:
        return "info"
    return "debug"


_ATTEMPT_LOG_SINK: contextvars.ContextVar[AttemptLogSink | None] = contextvars.ContextVar(
    "attempt_log_sink", default=None
)


class _AttemptPythonHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        sink = _ATTEMPT_LOG_SINK.get()
        if sink is not None:
            with contextlib.suppress(Exception):
                sink.submit_python(record)


_PYTHON_HANDLER = _AttemptPythonHandler()
_PYTHON_HANDLER_LOCK = threading.Lock()
_PYTHON_HANDLER_USERS = 0


def _attach_python_handler() -> None:
    global _PYTHON_HANDLER_USERS
    with _PYTHON_HANDLER_LOCK:
        if _PYTHON_HANDLER_USERS == 0:
            logging.getLogger("marquee").addHandler(_PYTHON_HANDLER)
        _PYTHON_HANDLER_USERS += 1


def _detach_python_handler() -> None:
    global _PYTHON_HANDLER_USERS
    with _PYTHON_HANDLER_LOCK:
        _PYTHON_HANDLER_USERS -= 1
        if _PYTHON_HANDLER_USERS == 0:
            logging.getLogger("marquee").removeHandler(_PYTHON_HANDLER)
