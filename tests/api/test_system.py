"""The system summary's pieces that need no database: folder sizes, the storage cache, and
the model files the manifest lists."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from marquee.api import system
from marquee.api.schemas.system import StorageInfo
from marquee.ml import model_manifest


def test_folder_size_counts_files_and_follows_no_link(tmp_path: Path) -> None:
    folder = tmp_path / "runs"
    (folder / "a").mkdir(parents=True)
    (folder / "a" / "one.jpg").write_bytes(b"x" * 10)
    (folder / "two.json").write_bytes(b"y" * 5)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"z" * 1000)
    (folder / "link").symlink_to(outside, target_is_directory=True)

    # The link counts as a directory entry that os.walk does not enter: only the two files.
    assert system.folder_size(folder) == 15
    assert system.folder_size(tmp_path / "missing") == 0


async def test_storage_is_measured_at_most_once_a_minute(tmp_path: Path) -> None:
    now = [100.0]
    calls: list[Path] = []

    def measure(data_dir: Path) -> StorageInfo:
        calls.append(data_dir)
        return StorageInfo(runs=len(calls), backups=0, cache=0, ml=0, measured_at=datetime.now(UTC))

    cache = system.StorageCache(ttl_s=60, clock=lambda: now[0], measure=measure)
    first = await cache.get(tmp_path)
    now[0] += 59
    assert (await cache.get(tmp_path)) is first
    now[0] += 2
    assert (await cache.get(tmp_path)).runs == 2
    assert calls == [tmp_path, tmp_path]


def test_storage_measures_four_folders(tmp_path: Path) -> None:
    for name, size in (("runs", 3), ("backups", 5), ("cache", 7), ("ml", 11), ("models", 99)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "f").write_bytes(b"x" * size)
    found = system.measure_storage(tmp_path)
    assert (found.runs, found.backups, found.cache, found.ml) == (3, 5, 7, 11)


def test_model_files_are_stat_ed_not_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models = tmp_path / "models"
    (models / "paddlex").mkdir(parents=True)
    (models / "clip.onnx").write_bytes(b"c" * 8)
    (models / "paddlex" / "w.bin").write_bytes(b"w" * 4)
    entries = [
        model_manifest.describe(tmp_path, models / "clip.onnx", item="clip", source="test"),
        model_manifest.describe(tmp_path, models / "paddlex", item="ocr", source="test"),
    ]
    (models / "gone.onnx").write_bytes(b"g" * 2)
    entries.append(
        model_manifest.describe(tmp_path, models / "gone.onnx", item="dinov2", source="test")
    )
    (models / "gone.onnx").unlink()
    manifest = models / "manifest.json"
    model_manifest.write_manifest(manifest, entries)

    def no_hashing(*args: object, **kwargs: object) -> str:
        raise AssertionError("the summary must not hash a model file")

    monkeypatch.setattr(model_manifest, "sha256_file", no_hashing)
    found = system.models_on_disk(tmp_path, manifest)

    assert found.manifest == "found"
    by_path = {f.path: f for f in found.files}
    assert (by_path["models/clip.onnx"].present, by_path["models/clip.onnx"].size_bytes) == (
        True,
        8,
    )
    assert (by_path["models/paddlex"].kind, by_path["models/paddlex"].size_bytes) == ("folder", 4)
    gone = by_path["models/gone.onnx"]
    assert (gone.present, gone.size_bytes, gone.expected_size_bytes) == (False, None, 2)

    assert system.models_on_disk(tmp_path, tmp_path / "nothing.json").manifest == "missing"
    manifest.write_text("not json")
    assert system.models_on_disk(tmp_path, manifest).manifest == "unreadable"
