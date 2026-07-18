"""Coordinator-only validation and same-filesystem atomic publication."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from marquee.core.filesystem import ClassifiedPath, FilesystemBoundary, FilesystemBoundaryError
from marquee.core.jobs.execution_io import ExecutionIO
from marquee.core.jobs.fenced_writer import WriteDisposition


class PublicationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FileSignature:
    device: int
    inode: int
    size: int
    modified_ns: int
    sha256: str


class PublicationFence(Protocol):
    async def record_publish_intent(self, intent: dict[str, Any]) -> object: ...

    async def publish_atomic(self, action: Any, evidence: dict[str, Any]) -> object: ...


def _applied(value: object) -> bool:
    return value is True or value == WriteDisposition.APPLIED or value == "applied"


def file_signature(boundary: FilesystemBoundary, path: ClassifiedPath) -> FileSignature:
    fd = boundary.open_read(path)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise PublicationError("publication input must be one regular unlinked file")
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        return FileSignature(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            size=metadata.st_size,
            modified_ns=metadata.st_mtime_ns,
            sha256=digest.hexdigest(),
        )
    finally:
        os.close(fd)


async def execution_file_signature(
    execution_io: ExecutionIO,
    boundary: FilesystemBoundary,
    path: ClassifiedPath,
) -> FileSignature:
    value = await execution_io.confined_signature(boundary, path)
    return FileSignature(
        device=value.device,
        inode=value.inode,
        size=value.size,
        modified_ns=value.modified_ns,
        sha256=value.sha256,
    )


def _optional_signature(
    boundary: FilesystemBoundary, path: ClassifiedPath
) -> FileSignature | None:
    try:
        return file_signature(boundary, path)
    except FilesystemBoundaryError:
        return None


def _matches_identity(
    boundary: FilesystemBoundary,
    path: ClassifiedPath,
    expected: FileSignature | None,
) -> bool:
    try:
        fd = boundary.open_read(path)
    except FilesystemBoundaryError:
        return expected is None
    try:
        metadata = os.fstat(fd)
        if expected is None:
            return False
        return (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_nlink == 1
            and metadata.st_dev == expected.device
            and metadata.st_ino == expected.inode
            and metadata.st_size == expected.size
            and metadata.st_mtime_ns == expected.modified_ns
        )
    finally:
        os.close(fd)


class PublicationCoordinator:
    def __init__(
        self,
        boundary: FilesystemBoundary,
        *,
        maximum_bytes: int = 64 * 1024 * 1024,
        execution_io: ExecutionIO | None = None,
    ):
        if maximum_bytes < 1 or maximum_bytes > 16 * 1024 * 1024 * 1024 * 1024:
            raise ValueError("publication size bound is invalid")
        self.boundary = boundary
        self.maximum_bytes = maximum_bytes
        self.execution_io = execution_io

    async def _signature(self, path: ClassifiedPath) -> FileSignature:
        if self.execution_io is None:
            return file_signature(self.boundary, path)
        return await execution_file_signature(self.execution_io, self.boundary, path)

    async def _optional_signature(self, path: ClassifiedPath) -> FileSignature | None:
        try:
            return await self._signature(path)
        except FilesystemBoundaryError:
            return None

    async def publish(
        self,
        *,
        staged: ClassifiedPath,
        destination: ClassifiedPath,
        expected_destination: FileSignature | None,
        fence: PublicationFence,
    ) -> FileSignature:
        if staged.root.name != destination.root.name:
            raise PublicationError("publication cannot cross classified roots")
        if staged.key.parts[:-1] != destination.key.parts[:-1]:
            raise PublicationError("staging must be in the destination directory")
        output = await self._signature(staged)
        if output.size > self.maximum_bytes:
            raise PublicationError("staged output exceeds the fixed size limit")
        current_destination = await self._optional_signature(destination)
        if current_destination != expected_destination:
            raise PublicationError("destination identity changed before publication")
        intent = {
            "destination_key": destination.key.value,
            "expected_destination": (
                asdict(expected_destination) if expected_destination is not None else None
            ),
            "output": asdict(output),
        }
        if not _applied(await fence.record_publish_intent(intent)):
            raise PublicationError("stale ownership rejected publication intent")

        def replace() -> None:
            if not _matches_identity(self.boundary, staged, output):
                raise PublicationError("staged output changed after validation")
            if not _matches_identity(self.boundary, destination, expected_destination):
                raise PublicationError("destination changed at publication boundary")
            fd = self.boundary.open_read(staged)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            self.boundary.atomic_replace(staged, destination)
            self.boundary.fsync_parent(destination)

        evidence = {"destination_key": destination.key.value, "output": asdict(output)}
        if not _applied(await fence.publish_atomic(replace, evidence)):
            raise PublicationError("stale ownership rejected atomic publication")
        return output

    async def delete(
        self,
        *,
        destination: ClassifiedPath,
        expected_destination: FileSignature,
        fence: PublicationFence,
    ) -> None:
        """Fence and fsync one confined deletion with the same publication authority."""
        current = await self._optional_signature(destination)
        if current != expected_destination:
            raise PublicationError("destination identity changed before deletion")
        intent = {
            "operation": "delete",
            "destination_key": destination.key.value,
            "expected_destination": asdict(expected_destination),
        }
        if not _applied(await fence.record_publish_intent(intent)):
            raise PublicationError("stale ownership rejected deletion intent")

        def remove() -> None:
            if not _matches_identity(self.boundary, destination, expected_destination):
                raise PublicationError("destination changed at deletion boundary")
            if not self.boundary.delete_file(destination, missing_ok=False):
                raise PublicationError("destination disappeared at deletion boundary")
            self.boundary.fsync_parent(destination)

        evidence = {
            "operation": "delete",
            "destination_key": destination.key.value,
            "deleted": True,
        }
        if not _applied(await fence.publish_atomic(remove, evidence)):
            raise PublicationError("stale ownership rejected atomic deletion")
