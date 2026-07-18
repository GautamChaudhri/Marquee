"""Cancellation-aware, fenced, chunked file I/O for job execution paths."""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from marquee.core.filesystem import ClassifiedPath, FilesystemBoundary

ProgressCallback = Callable[[int, int], Awaitable[None]]
FenceCheck = Callable[[], Awaitable[bool]]
CancellationCheck = Callable[[], bool]


class ExecutionIOError(RuntimeError):
    """Chunked execution I/O could not preserve its safety contract."""


class ExecutionIOCancelledError(ExecutionIOError):
    """Cancellation or attempt-ownership loss stopped I/O before publication."""


@dataclass(frozen=True, slots=True)
class ExecutionIOResult:
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ConfinedFileSignature:
    device: int
    inode: int
    size: int
    modified_ns: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ExecutionIOReadResult:
    payload: bytes
    sha256: str


class ExecutionIO:
    """Perform bounded file work without blocking the worker event loop."""

    def __init__(
        self,
        *,
        cancelled: CancellationCheck,
        owns_fence: FenceCheck,
        progress: ProgressCallback | None = None,
        chunk_bytes: int = 1024 * 1024,
    ) -> None:
        if not 64 * 1024 <= chunk_bytes <= 8 * 1024 * 1024:
            raise ValueError("execution I/O chunk size must be between 64 KiB and 8 MiB")
        self._cancelled = cancelled
        self._owns_fence = owns_fence
        self._progress = progress
        self.chunk_bytes = chunk_bytes

    async def _checkpoint(self) -> None:
        if self._cancelled():
            raise ExecutionIOCancelledError("execution I/O cancelled")
        if not await self._owns_fence():
            raise ExecutionIOCancelledError("execution I/O fence ownership changed")
        await asyncio.sleep(0)

    @staticmethod
    def _regular_file(path: Path) -> os.stat_result:
        value = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(value.st_mode) or path.is_symlink():
            raise ExecutionIOError("execution I/O source is not a regular file")
        return value

    async def checksum(self, source: Path) -> ExecutionIOResult:
        before = await asyncio.to_thread(self._regular_file, source)
        digest = hashlib.sha256()
        processed = 0
        stream = await asyncio.to_thread(source.open, "rb")
        try:
            while True:
                await self._checkpoint()
                chunk = await asyncio.to_thread(stream.read, self.chunk_bytes)
                if not chunk:
                    break
                digest.update(chunk)
                processed += len(chunk)
                if self._progress is not None:
                    await self._progress(processed, before.st_size)
        finally:
            await asyncio.to_thread(stream.close)
        after = await asyncio.to_thread(source.stat, follow_symlinks=False)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ExecutionIOError("execution I/O source changed while checksummed")
        return ExecutionIOResult(size=processed, sha256=digest.hexdigest())

    async def confined_signature(
        self, boundary: FilesystemBoundary, source: ClassifiedPath
    ) -> ConfinedFileSignature:
        fd = await asyncio.to_thread(boundary.open_read, source)
        digest = hashlib.sha256()
        try:
            before = await asyncio.to_thread(os.fstat, fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ExecutionIOError("confined execution input is not one regular file")
            processed = 0
            while True:
                await self._checkpoint()
                chunk = await asyncio.to_thread(os.read, fd, self.chunk_bytes)
                if not chunk:
                    break
                digest.update(chunk)
                processed += len(chunk)
                if self._progress is not None:
                    await self._progress(processed, before.st_size)
            after = await asyncio.to_thread(os.fstat, fd)
        finally:
            await asyncio.to_thread(os.close, fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ExecutionIOError("confined execution input changed while checksummed")
        return ConfinedFileSignature(
            device=before.st_dev,
            inode=before.st_ino,
            size=before.st_size,
            modified_ns=before.st_mtime_ns,
            sha256=digest.hexdigest(),
        )

    async def confined_copy_to_fd(
        self,
        boundary: FilesystemBoundary,
        source: ClassifiedPath,
        destination_fd: int,
    ) -> ExecutionIOResult:
        source_fd: int | None = None
        digest = hashlib.sha256()
        processed = 0
        try:
            source_fd = await asyncio.to_thread(boundary.open_read, source)
            before = await asyncio.to_thread(os.fstat, source_fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ExecutionIOError("confined execution input is not one regular file")
            while True:
                await self._checkpoint()
                chunk = await asyncio.to_thread(os.read, source_fd, self.chunk_bytes)
                if not chunk:
                    break
                offset = 0
                while offset < len(chunk):
                    written = await asyncio.to_thread(os.write, destination_fd, chunk[offset:])
                    if written <= 0:
                        raise ExecutionIOError("confined execution write made no progress")
                    offset += written
                digest.update(chunk)
                processed += len(chunk)
                if self._progress is not None:
                    await self._progress(processed, before.st_size)
            await asyncio.to_thread(os.fsync, destination_fd)
            after = await asyncio.to_thread(os.fstat, source_fd)
        finally:
            if source_fd is not None:
                await asyncio.to_thread(os.close, source_fd)
            await asyncio.to_thread(os.close, destination_fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ExecutionIOError("confined execution input changed while copied")
        return ExecutionIOResult(size=processed, sha256=digest.hexdigest())

    async def confined_copy(
        self,
        boundary: FilesystemBoundary,
        source: ClassifiedPath,
        destination: ClassifiedPath,
    ) -> ExecutionIOResult:
        destination_fd = await asyncio.to_thread(boundary.create_file, destination)
        try:
            result = await self.confined_copy_to_fd(
                boundary,
                source,
                destination_fd,
            )
            await asyncio.to_thread(boundary.fsync_parent, destination)
            return result
        except BaseException:
            await asyncio.to_thread(boundary.delete_file, destination, missing_ok=True)
            raise

    async def read(
        self, source: Path, *, maximum_bytes: int
    ) -> ExecutionIOReadResult:
        before = await asyncio.to_thread(self._regular_file, source)
        if before.st_size > maximum_bytes:
            raise ExecutionIOError("execution I/O source exceeds its read bound")
        digest = hashlib.sha256()
        payload = bytearray()
        stream = await asyncio.to_thread(source.open, "rb")
        try:
            while True:
                await self._checkpoint()
                chunk = await asyncio.to_thread(stream.read, self.chunk_bytes)
                if not chunk:
                    break
                payload.extend(chunk)
                digest.update(chunk)
                if self._progress is not None:
                    await self._progress(len(payload), before.st_size)
        finally:
            await asyncio.to_thread(stream.close)
        after = await asyncio.to_thread(source.stat, follow_symlinks=False)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ExecutionIOError("execution I/O source changed while read")
        return ExecutionIOReadResult(payload=bytes(payload), sha256=digest.hexdigest())

    async def copy(self, source: Path, destination: Path) -> ExecutionIOResult:
        before = await asyncio.to_thread(self._regular_file, source)
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        digest = hashlib.sha256()
        processed = 0
        source_stream = await asyncio.to_thread(source.open, "rb")
        destination_stream = await asyncio.to_thread(destination.open, "xb")
        try:
            while True:
                await self._checkpoint()
                chunk = await asyncio.to_thread(source_stream.read, self.chunk_bytes)
                if not chunk:
                    break
                await asyncio.to_thread(destination_stream.write, chunk)
                digest.update(chunk)
                processed += len(chunk)
                if self._progress is not None:
                    await self._progress(processed, before.st_size)
            await asyncio.to_thread(destination_stream.flush)
            await asyncio.to_thread(os.fsync, destination_stream.fileno())
        except BaseException:
            await asyncio.to_thread(destination_stream.close)
            await asyncio.to_thread(source_stream.close)
            await asyncio.to_thread(destination.unlink, missing_ok=True)
            raise
        else:
            await asyncio.to_thread(destination_stream.close)
            await asyncio.to_thread(source_stream.close)
        after = await asyncio.to_thread(source.stat, follow_symlinks=False)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            await asyncio.to_thread(destination.unlink, missing_ok=True)
            raise ExecutionIOError("execution I/O source changed while copied")
        return ExecutionIOResult(size=processed, sha256=digest.hexdigest())

    async def write(self, payload: bytes, destination: Path) -> ExecutionIOResult:
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        digest = hashlib.sha256()
        processed = 0
        stream = await asyncio.to_thread(destination.open, "xb")
        try:
            for offset in range(0, len(payload), self.chunk_bytes):
                await self._checkpoint()
                chunk = payload[offset : offset + self.chunk_bytes]
                await asyncio.to_thread(stream.write, chunk)
                digest.update(chunk)
                processed += len(chunk)
                if self._progress is not None:
                    await self._progress(processed, len(payload))
            await asyncio.to_thread(stream.flush)
            await asyncio.to_thread(os.fsync, stream.fileno())
        except BaseException:
            await asyncio.to_thread(stream.close)
            await asyncio.to_thread(destination.unlink, missing_ok=True)
            raise
        else:
            await asyncio.to_thread(stream.close)
        return ExecutionIOResult(size=processed, sha256=digest.hexdigest())
