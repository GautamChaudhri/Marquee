"""Canonical checksummed media backup artifacts (JMC5B B12).

The legacy backup table recorded a physical path and carried its own
authority.  JMC5B replaces it: a backup is a confined, checksummed, job-linked
artifact addressed by a storage key, and restore consumes that artifact rather
than a stored path.

Creating a backup is idempotent by content: an existing backup with the same
digest is reused, and a key whose bytes disagree with the recorded digest fails
closed rather than being silently overwritten.
"""

from __future__ import annotations

from marquee.core.filesystem import ClassifiedPath, FilesystemBoundary, FilesystemBoundaryError
from marquee.core.jobs.execution_io import ExecutionIO
from marquee.core.jobs.mutation_documents import MutationBackupV1
from marquee.core.jobs.publication import FileSignature, _optional_signature, file_signature

#: Confined root and prefix owned by JMC5B media backups.
BACKUP_ROOT = "data"
BACKUP_PREFIX = "jmc5/media-backups"

DEFAULT_RETENTION = "recoverable_media"


class BackupArtifactError(RuntimeError):
    """A recoverable backup could not be created or proven."""


def signature_text(signature: FileSignature) -> str:
    """Stable source-identity text stored on the typed backup document."""
    return f"sha256:{signature.sha256}:{signature.size}"


def backup_key(subject_key: str, signature: FileSignature, *, suffix: str) -> str:
    """Content-addressed, confined key; never derived from a caller path."""
    if not subject_key or "/" in subject_key or ".." in subject_key:
        raise BackupArtifactError("media backup subject key is invalid")
    clean = suffix.lower().lstrip(".")
    if clean and not clean.isalnum():
        raise BackupArtifactError("media backup suffix is invalid")
    tail = f".{clean}" if clean else ""
    return f"{BACKUP_PREFIX}/{subject_key}/{signature.sha256}{tail}"


def create_media_backup(
    boundary: FilesystemBoundary,
    *,
    source: ClassifiedPath,
    subject_key: str,
    signature: FileSignature,
    suffix: str,
    retention: str = DEFAULT_RETENTION,
) -> MutationBackupV1:
    """Copy the source into confined immutable storage before it is replaced."""
    key = backup_key(subject_key, signature, suffix=suffix)
    directory = boundary.from_key(BACKUP_ROOT, f"{BACKUP_PREFIX}/{subject_key}")
    boundary.create_directory(directory, parents=True)
    destination = boundary.from_key(BACKUP_ROOT, key)
    existing = _optional_signature(boundary, destination)
    if existing is not None:
        if existing.sha256 != signature.sha256:
            raise BackupArtifactError("recorded backup key conflicts with existing bytes")
        stored = existing
    else:
        boundary.copy_file(source, destination)
        boundary.fsync_parent(destination)
        stored = file_signature(boundary, destination)
        if stored.sha256 != signature.sha256:
            boundary.delete_file(destination, missing_ok=True)
            raise BackupArtifactError("backup copy did not match its source digest")
    return MutationBackupV1(
        artifact_key=key,
        checksum=stored.sha256,
        size_bytes=stored.size,
        source_signature=signature_text(signature),
        retention=retention,
        restore_eligible=True,
    )


async def create_execution_media_backup(
    execution_io: ExecutionIO,
    boundary: FilesystemBoundary,
    *,
    source: ClassifiedPath,
    subject_key: str,
    signature: FileSignature,
    suffix: str,
    retention: str = DEFAULT_RETENTION,
) -> MutationBackupV1:
    """Create a media backup through cancellation-aware, fenced chunked I/O."""
    key = backup_key(subject_key, signature, suffix=suffix)
    directory = boundary.from_key(BACKUP_ROOT, f"{BACKUP_PREFIX}/{subject_key}")
    boundary.create_directory(directory, parents=True)
    destination = boundary.from_key(BACKUP_ROOT, key)
    try:
        existing_value = await execution_io.confined_signature(boundary, destination)
    except FilesystemBoundaryError:
        existing_value = None
    if existing_value is not None:
        if existing_value.sha256 != signature.sha256:
            raise BackupArtifactError("recorded backup key conflicts with existing bytes")
        checksum = existing_value.sha256
        size = existing_value.size
    else:
        stored = await execution_io.confined_copy(boundary, source, destination)
        if stored.sha256 != signature.sha256:
            boundary.delete_file(destination, missing_ok=True)
            raise BackupArtifactError("backup copy did not match its source digest")
        checksum = stored.sha256
        size = stored.size
    return MutationBackupV1(
        artifact_key=key,
        checksum=checksum,
        size_bytes=size,
        source_signature=signature_text(signature),
        retention=retention,
        restore_eligible=True,
    )


def verify_media_backup(
    boundary: FilesystemBoundary, backup: MutationBackupV1
) -> ClassifiedPath:
    """Prove a recorded backup still matches its checksum before a restore."""
    if not backup.restore_eligible:
        raise BackupArtifactError("backup is not eligible for restore")
    destination = boundary.from_key(BACKUP_ROOT, backup.artifact_key)
    stored = _optional_signature(boundary, destination)
    if stored is None:
        raise BackupArtifactError("recorded backup artifact is missing")
    if stored.sha256 != backup.checksum or stored.size != backup.size_bytes:
        raise BackupArtifactError("recorded backup artifact failed checksum verification")
    return destination


async def verify_execution_media_backup(
    execution_io: ExecutionIO,
    boundary: FilesystemBoundary,
    backup: MutationBackupV1,
) -> ClassifiedPath:
    """Verify a canonical backup through fenced chunked execution I/O."""
    if not backup.restore_eligible:
        raise BackupArtifactError("backup is not eligible for restore")
    destination = boundary.from_key(BACKUP_ROOT, backup.artifact_key)
    try:
        stored = await execution_io.confined_signature(boundary, destination)
    except FilesystemBoundaryError as exc:
        raise BackupArtifactError("recorded backup artifact is missing") from exc
    if stored.sha256 != backup.checksum or stored.size != backup.size_bytes:
        raise BackupArtifactError("recorded backup artifact failed checksum verification")
    return destination
