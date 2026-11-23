from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from marquee.ml.model_manifest import (
    ManifestError,
    ManifestProblem,
    describe,
    folder_digest,
    load_manifest,
    verify,
    write_manifest,
)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    models = tmp_path / "models"
    models.mkdir()
    (models / "clip.onnx").write_bytes(b"clip weights")
    ocr = models / "paddlex" / "official_models" / "det"
    ocr.mkdir(parents=True)
    (ocr / "inference.json").write_text("{}")
    (ocr / "inference.pdiparams").write_bytes(b"\x00\x01")
    return tmp_path


def test_describe_file(data_dir: Path) -> None:
    entry = describe(
        data_dir,
        data_dir / "models" / "clip.onnx",
        item="clip",
        source="exported",
        tools={"torch": "2.15.0+cpu"},
    )

    assert entry.path == "models/clip.onnx"
    assert entry.kind == "file"
    assert entry.size == len(b"clip weights")
    assert entry.sha256 == hashlib.sha256(b"clip weights").hexdigest()
    assert entry.tools == {"torch": "2.15.0+cpu"}
    assert entry.built_at.endswith("+00:00")


def test_describe_folder_digests_relative_paths_and_hashes(data_dir: Path) -> None:
    folder = data_dir / "models" / "paddlex" / "official_models" / "det"

    entry = describe(data_dir, folder, item="ocr", source="paddlex")

    lines = "".join(
        f"{name}\0{hashlib.sha256(content).hexdigest()}\n"
        for name, content in [("inference.json", b"{}"), ("inference.pdiparams", b"\x00\x01")]
    )
    assert entry.kind == "folder"
    assert entry.path == "models/paddlex/official_models/det"
    assert entry.sha256 == hashlib.sha256(lines.encode()).hexdigest()
    assert entry.size == 4


def test_folder_digest_changes_on_rename(data_dir: Path) -> None:
    folder = data_dir / "models" / "paddlex" / "official_models" / "det"
    before, _ = folder_digest(folder)

    (folder / "inference.json").rename(folder / "renamed.json")

    after, _ = folder_digest(folder)
    assert after != before


def test_folder_digest_ignores_hidden_bookkeeping(data_dir: Path) -> None:
    folder = data_dir / "models" / "paddlex" / "official_models" / "det"
    before = folder_digest(folder)

    (folder / ".gitattributes").write_text("*.safetensors filter=lfs")
    (folder / ".cache" / "huggingface" / "download").mkdir(parents=True)
    (folder / ".cache" / "huggingface" / "download" / "x.lock").write_text("")

    assert folder_digest(folder) == before


def test_describe_missing_path_raises(data_dir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        describe(data_dir, data_dir / "models" / "absent.onnx", item="x", source="y")


def test_write_then_load_round_trips(data_dir: Path) -> None:
    entries = [
        describe(data_dir, data_dir / "models" / "clip.onnx", item="clip", source="s"),
        describe(
            data_dir,
            data_dir / "models" / "paddlex" / "official_models" / "det",
            item="ocr",
            source="s",
            details={"note": "n"},
        ),
    ]
    path = data_dir / "models" / "manifest.json"

    written = write_manifest(path, entries)
    loaded = load_manifest(path)

    assert loaded == written
    assert [e.item for e in loaded.entries] == ["clip", "ocr"]
    assert loaded.for_item("ocr")[0].details == {"note": "n"}
    assert json.loads(path.read_text())["format"] == 1
    assert path.stat().st_mode & 0o777 == 0o644
    # Atomic write: no temporary file is left behind.
    assert sorted(p.name for p in path.parent.iterdir() if p.is_file()) == [
        "clip.onnx",
        "manifest.json",
    ]


def test_write_rejects_duplicate_paths(data_dir: Path) -> None:
    entry = describe(data_dir, data_dir / "models" / "clip.onnx", item="clip", source="s")

    with pytest.raises(ValueError, match="unique"):
        write_manifest(data_dir / "models" / "manifest.json", [entry, entry])


def test_load_absent_manifest_is_none(tmp_path: Path) -> None:
    assert load_manifest(tmp_path / "manifest.json") is None


def test_load_corrupt_manifest_raises(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{not json")

    with pytest.raises(ManifestError):
        load_manifest(path)


def test_verify_clean(data_dir: Path) -> None:
    manifest = write_manifest(
        data_dir / "models" / "manifest.json",
        [
            describe(data_dir, data_dir / "models" / "clip.onnx", item="clip", source="s"),
            describe(
                data_dir,
                data_dir / "models" / "paddlex" / "official_models" / "det",
                item="ocr",
                source="s",
            ),
        ],
    )

    assert verify(manifest, data_dir) == []


def test_verify_reports_missing_and_changed(data_dir: Path) -> None:
    folder = data_dir / "models" / "paddlex" / "official_models" / "det"
    manifest = write_manifest(
        data_dir / "models" / "manifest.json",
        [
            describe(data_dir, data_dir / "models" / "clip.onnx", item="clip", source="s"),
            describe(data_dir, folder, item="ocr", source="s"),
        ],
    )
    (data_dir / "models" / "clip.onnx").unlink()
    (folder / "inference.json").write_text("[]")  # same size, different content

    assert verify(manifest, data_dir) == [
        ManifestProblem("models/clip.onnx", "missing"),
        ManifestProblem("models/paddlex/official_models/det", "checksum mismatch"),
    ]


def test_verify_reports_size_change(data_dir: Path) -> None:
    manifest = write_manifest(
        data_dir / "models" / "manifest.json",
        [describe(data_dir, data_dir / "models" / "clip.onnx", item="clip", source="s")],
    )
    (data_dir / "models" / "clip.onnx").write_bytes(b"different length")

    assert verify(manifest, data_dir) == [ManifestProblem("models/clip.onnx", "size mismatch")]
