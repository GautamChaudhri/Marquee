"""Content-addressed poster backups."""

from __future__ import annotations

from pathlib import Path

import pytest

from marquee.artwork import backup


def test_a_backup_is_named_by_its_bytes_and_verified(tmp_path: Path) -> None:
    key = backup.store(b"old poster", 7, tmp_path)

    assert key == f"posters/7/{backup.sha256_of(b'old poster')}.jpg"
    assert (tmp_path / key).read_bytes() == b"old poster"
    assert backup.verify(key, backup.sha256_of(b"old poster"), tmp_path) == b"old poster"
    assert backup.exists(7, backup.sha256_of(b"old poster"), tmp_path)
    assert not list((tmp_path / "posters" / "7").glob(".backup-*"))


def test_storing_the_same_bytes_twice_keeps_one_file(tmp_path: Path) -> None:
    first = backup.store(b"same", 3, tmp_path)
    mtime = (tmp_path / first).stat().st_mtime_ns
    second = backup.store(b"same", 3, tmp_path)

    assert first == second
    assert (tmp_path / first).stat().st_mtime_ns == mtime
    assert len(list((tmp_path / "posters" / "3").iterdir())) == 1


def test_a_backup_with_the_wrong_bytes_is_an_error_not_overwritten(tmp_path: Path) -> None:
    key = backup.backup_key(3, backup.sha256_of(b"expected"))
    (tmp_path / key).parent.mkdir(parents=True)
    (tmp_path / key).write_bytes(b"tampered")

    with pytest.raises(backup.BackupError, match="holds bytes with hash"):
        backup.store(b"expected", 3, tmp_path)
    assert (tmp_path / key).read_bytes() == b"tampered"
    assert not backup.exists(3, backup.sha256_of(b"expected"), tmp_path)


def test_a_missing_backup_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(backup.BackupError, match="is missing"):
        backup.verify(backup.backup_key(1, "a" * 64), "a" * 64, tmp_path)


@pytest.mark.parametrize(
    "key",
    ["../x.jpg", "/abs/posters/1/x.jpg", "posters/1/short.jpg", "posters/0/" + "a" * 64 + ".jpg"],
)
def test_only_backup_names_are_read(tmp_path: Path, key: str) -> None:
    with pytest.raises(backup.BackupError, match="not a backup's name"):
        backup.verify(key, "a" * 64, tmp_path)


def test_a_symlinked_backup_is_refused(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.jpg"
    elsewhere.write_bytes(b"x")
    key = backup.backup_key(2, backup.sha256_of(b"x"))
    (tmp_path / key).parent.mkdir(parents=True)
    (tmp_path / key).symlink_to(elsewhere)
    with pytest.raises(backup.BackupError):
        backup.verify(key, backup.sha256_of(b"x"), tmp_path)


def test_backup_names_need_a_movie_and_a_digest() -> None:
    with pytest.raises(backup.BackupError):
        backup.backup_key(0, "a" * 64)
    with pytest.raises(backup.BackupError):
        backup.backup_key(1, "not-a-digest")


def test_summary_counts_kept_backups(tmp_path: Path) -> None:
    assert backup.summary(tmp_path) == (0, 0)
    backup.store(b"one", 1, tmp_path)
    backup.store(b"three", 2, tmp_path)
    assert backup.summary(tmp_path) == (2, len(b"one") + len(b"three"))
