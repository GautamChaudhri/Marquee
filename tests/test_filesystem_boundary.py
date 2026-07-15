from __future__ import annotations

import errno
import io
import os
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from marquee.core.filesystem import (
    ConfinedKey,
    FilesystemBoundaryError,
    boundary_for_roots,
)


@pytest.mark.parametrize(
    "value",
    ["", ".", "..", "a/../b", "/absolute", "C:/drive", "a\\b", "a//b", "a\0b"],
)
def test_confined_key_rejects_ambiguous_or_escaping_values(value: str) -> None:
    with pytest.raises(FilesystemBoundaryError):
        ConfinedKey.parse(value)


def test_classification_rejects_sibling_prefix_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "media"
    sibling = tmp_path / "media-old"
    root.mkdir()
    sibling.mkdir()
    (sibling / "secret").write_text("no", encoding="utf-8")
    boundary = boundary_for_roots({"media": root})

    with pytest.raises(FilesystemBoundaryError):
        boundary.classify(sibling / "secret", require_file=True)

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("no", encoding="utf-8")
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(FilesystemBoundaryError):
        boundary.classify(root / "link" / "secret", require_file=True)


def test_open_read_binds_validation_to_descriptor(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    path = root / "safe.txt"
    path.write_text("safe", encoding="utf-8")
    boundary = boundary_for_roots({"data": root})
    classified = boundary.classify(path, require_file=True)
    fd = boundary.open_read(classified)
    try:
        path.unlink()
        path.write_text("replacement", encoding="utf-8")
        assert os.read(fd, 16) == b"safe"
    finally:
        os.close(fd)


def test_delete_revalidates_and_never_follows_replaced_symlink(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    victim = root / "victim"
    victim.write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.write_text("keep", encoding="utf-8")
    boundary = boundary_for_roots({"data": root}, access="read_write")
    classified = boundary.classify(victim, require_file=True, write=True)
    victim.unlink()
    victim.symlink_to(outside)

    with pytest.raises(FilesystemBoundaryError):
        boundary.delete_file(classified)
    assert outside.read_text(encoding="utf-8") == "keep"


def test_archive_extraction_rejects_traversal(tmp_path: Path) -> None:
    root = tmp_path / "data"
    destination = root / "restore"
    root.mkdir()
    destination.mkdir()
    archive_path = root / "unsafe.tar"
    with tarfile.open(archive_path, "w") as archive:
        payload = b"escape"
        member = tarfile.TarInfo("../outside.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    boundary = boundary_for_roots({"data": root}, access="read_write")
    archive = boundary.classify(archive_path, require_file=True)
    target = boundary.classify(destination, write=True)

    with pytest.raises(FilesystemBoundaryError):
        boundary.extract_tar(archive, target)
    assert not (tmp_path / "outside.txt").exists()


def test_atomic_replace_rejects_cross_device_without_copy_fallback(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    staged_path = root / ".staged"
    destination_path = root / "movie.mkv"
    staged_path.write_bytes(b"new")
    destination_path.write_bytes(b"old")
    boundary = boundary_for_roots({"media": root}, access="read_write")
    staged = boundary.classify(staged_path, require_file=True, write=True)
    destination = boundary.classify(destination_path, require_file=True, write=True)

    with (
        patch("marquee.core.filesystem.os.replace", side_effect=OSError(errno.EXDEV, "cross")),
        pytest.raises(FilesystemBoundaryError, match="cross-device"),
    ):
        boundary.atomic_replace(staged, destination)

    assert staged_path.read_bytes() == b"new"
    assert destination_path.read_bytes() == b"old"


def test_destination_root_staging_and_parent_fsync_are_confined(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    boundary = boundary_for_roots({"media": root}, access="read_write")

    staged, fd = boundary.temporary_root_file("media", prefix=".poster-stage-")
    _write = b"validated"
    try:
        os.write(fd, _write)
        os.fsync(fd)
    finally:
        os.close(fd)

    assert staged.root.name == "media"
    assert staged.key.value.startswith(".poster-stage-")
    assert (root / staged.key.value).read_bytes() == _write
    boundary.fsync_parent(staged)


def test_destination_root_staging_rejects_unsafe_prefix(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    boundary = boundary_for_roots({"media": root}, access="read_write")
    with pytest.raises(FilesystemBoundaryError, match="prefix is unsafe"):
        boundary.temporary_root_file("media", prefix="../escape")
