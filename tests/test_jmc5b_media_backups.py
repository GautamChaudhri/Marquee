"""B1 canonical checksummed media backup artifacts (JMC5B B12).

The legacy ``MediaBackup`` row stored a physical path.  These prove the
replacement: a confined, content-addressed, checksummed artifact that is
idempotent by content, fails closed on a conflicting key, and is verified before
any restore consumes it.  All fixtures are confined synthetic roots.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marquee.core.filesystem import FilesystemBoundary, RootSpec
from marquee.core.jobs.media_backups import (
    BACKUP_PREFIX,
    BackupArtifactError,
    backup_key,
    create_media_backup,
    verify_media_backup,
)
from marquee.core.jobs.publication import file_signature


def _boundary(tmp_path: Path) -> FilesystemBoundary:
    data = tmp_path / "data"
    source = tmp_path / "media"
    data.mkdir()
    source.mkdir()
    return FilesystemBoundary(
        {
            "data": RootSpec(
                name="data",
                path=data,
                purpose="jmc5b media backups",
                access="read_write",
                allow_symlinks=False,
            ),
            "source": RootSpec(
                name="source",
                path=source,
                purpose="synthetic source media",
                access="read_write",
                allow_symlinks=False,
            ),
        }
    )


def _physical(classified) -> Path:
    """Resolve a confined key to its synthetic on-disk path for assertions only."""
    return classified.root.resolved() / classified.key.value


def _write_source(boundary: FilesystemBoundary, key: str, payload: bytes):
    classified = boundary.from_key("source", key)
    path = _physical(classified)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return classified


def test_backup_is_content_addressed_confined_and_checksummed(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    source = _write_source(boundary, "show.mkv", b"matroska-payload")
    signature = file_signature(boundary, source)

    backup = create_media_backup(
        boundary,
        source=source,
        subject_key="media-file-7",
        signature=signature,
        suffix="mkv",
    )

    assert backup.artifact_key == f"{BACKUP_PREFIX}/media-file-7/{signature.sha256}.mkv"
    assert backup.checksum == signature.sha256
    assert backup.size_bytes == len(b"matroska-payload")
    assert backup.source_signature == f"sha256:{signature.sha256}:{signature.size}"
    assert backup.restore_eligible is True
    # The document exposes a storage key, never a physical path.
    assert str(tmp_path) not in backup.artifact_key

    stored = boundary.from_key("data", backup.artifact_key)
    assert _physical(stored).read_bytes() == b"matroska-payload"


def test_repeated_backup_of_identical_content_is_idempotent(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    source = _write_source(boundary, "show.mkv", b"same-bytes")
    signature = file_signature(boundary, source)

    first = create_media_backup(
        boundary, source=source, subject_key="mf-1", signature=signature, suffix="mkv"
    )
    second = create_media_backup(
        boundary, source=source, subject_key="mf-1", signature=signature, suffix="mkv"
    )
    assert first == second
    parent = _physical(boundary.from_key("data", f"{BACKUP_PREFIX}/mf-1"))
    assert len(list(parent.iterdir())) == 1


def test_conflicting_bytes_at_a_recorded_key_fail_closed(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    source = _write_source(boundary, "show.mkv", b"original")
    signature = file_signature(boundary, source)
    backup = create_media_backup(
        boundary, source=source, subject_key="mf-2", signature=signature, suffix="mkv"
    )

    # Someone replaced the stored backup bytes behind our back.
    stored = boundary.from_key("data", backup.artifact_key)
    _physical(stored).write_bytes(b"tampered")

    with pytest.raises(BackupArtifactError, match="conflicts with existing bytes"):
        create_media_backup(
            boundary, source=source, subject_key="mf-2", signature=signature, suffix="mkv"
        )


def test_verify_rejects_tampered_or_missing_backup(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    source = _write_source(boundary, "show.mkv", b"restore-me")
    signature = file_signature(boundary, source)
    backup = create_media_backup(
        boundary, source=source, subject_key="mf-3", signature=signature, suffix="mkv"
    )

    assert _physical(verify_media_backup(boundary, backup)).read_bytes() == b"restore-me"

    stored = boundary.from_key("data", backup.artifact_key)
    _physical(stored).write_bytes(b"tampered-restore")
    with pytest.raises(BackupArtifactError, match="failed checksum verification"):
        verify_media_backup(boundary, backup)

    _physical(stored).unlink()
    with pytest.raises(BackupArtifactError, match="missing"):
        verify_media_backup(boundary, backup)


def test_verify_refuses_an_ineligible_backup(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    source = _write_source(boundary, "show.mkv", b"payload")
    signature = file_signature(boundary, source)
    backup = create_media_backup(
        boundary, source=source, subject_key="mf-4", signature=signature, suffix="mkv"
    )
    with pytest.raises(BackupArtifactError, match="not eligible"):
        verify_media_backup(boundary, backup.model_copy(update={"restore_eligible": False}))


def test_backup_key_rejects_traversal_and_unsafe_suffix(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    source = _write_source(boundary, "show.mkv", b"payload")
    signature = file_signature(boundary, source)

    for subject_key in ("../escape", "nested/key", ""):
        with pytest.raises(BackupArtifactError, match="subject key is invalid"):
            backup_key(subject_key, signature, suffix="mkv")
    with pytest.raises(BackupArtifactError, match="suffix is invalid"):
        backup_key("mf-5", signature, suffix="../mkv")


def test_hardlinked_source_is_refused_before_backup(tmp_path: Path) -> None:
    """A hardlinked source cannot be proven unshared, so it must not be signed."""
    boundary = _boundary(tmp_path)
    source = _write_source(boundary, "show.mkv", b"linked")
    link = boundary.from_key("source", "hardlink.mkv")
    _physical(link).hardlink_to(_physical(source))

    with pytest.raises(Exception, match="one regular unlinked file"):
        file_signature(boundary, source)
