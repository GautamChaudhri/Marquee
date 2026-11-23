from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import numpy as np
import pytest

from marquee import cli, model_fetch
from marquee.ml import model_manifest
from marquee.ml.artifact_codec import unicode_array, unicode_scalar
from marquee.ml.zeroshot import AXIS_PROMPTS
from marquee.model_fetch import BuildError, Built, ModelPaths, fetch, staged


@pytest.fixture
def paths(tmp_path: Path) -> ModelPaths:
    models = tmp_path / "models"
    return ModelPaths(
        data_dir=tmp_path,
        manifest=models / "manifest.json",
        cache_dir=tmp_path / "cache",
        ocr_dir=models / "paddlex",
        clip=models / "clip-vit-b-32.onnx",
        dino=models / "dinov2-vits14.onnx",
        zeroshot=tmp_path / "ml" / "zeroshot_axes.clip-vit-b-32.npz",
        yolo=models / "yolo11n.onnx",
        aesthetic=models / "sa_0_4_vit_b_32_linear.pth",
        face=models / "scrfd_500m_bnkps.onnx",
    )


def _fake_file_builder(item: str, calls: list[str]):
    def build(paths: ModelPaths) -> Built:
        calls.append(item)
        target = getattr(paths, item)
        with staged(target) as partial:
            partial.write_bytes(f"{item} weights".encode())
        entry = model_manifest.describe(paths.data_dir, target, item=item, source="fake")
        return Built([entry], "fake check passed")

    return build


def _fake_aesthetic_builder(calls: list[str]):
    def build(paths: ModelPaths) -> Built:
        calls.append("aesthetic")
        entries = []
        for target in paths.aesthetic_files():
            with staged(target) as partial:
                partial.write_bytes(f"aesthetic {target.suffix}".encode())
            entries.append(
                model_manifest.describe(paths.data_dir, target, item="aesthetic", source="fake")
            )
        return Built(entries, "fake check passed")

    return build


def _fake_ocr_builder(calls: list[str]):
    def build(paths: ModelPaths) -> Built:
        calls.append("ocr")
        folder = paths.ocr_dir / "official_models" / "FAKE_det"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "model.safetensors").write_bytes(b"det")
        entry = model_manifest.describe(paths.data_dir, folder, item="ocr", source="fake")
        return Built([entry], "loaded on gpu:0")

    return build


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    made: list[str] = []
    builders = {
        item: _fake_file_builder(item, made)
        for item in ("clip", "dino", "zeroshot", "yolo", "face")
    }
    builders["ocr"] = _fake_ocr_builder(made)
    builders["aesthetic"] = _fake_aesthetic_builder(made)
    monkeypatch.setattr(model_fetch, "BUILDERS", builders)
    monkeypatch.setattr(model_fetch, "export_tools_available", lambda: True)
    return made


def _run(paths: ModelPaths, *force: str) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = fetch(force=force, paths=paths, echo=lines.append)
    return code, lines


def _status(lines: list[str], item: str) -> str:
    (line,) = [line for line in lines if line.split()[0] == item]
    return line.split()[1]


ALL_ITEMS = ["clip", "dino", "zeroshot", "yolo", "ocr", "aesthetic", "face"]


def test_builds_everything_missing_then_leaves_it_alone(
    paths: ModelPaths, calls: list[str]
) -> None:
    code, lines = _run(paths)

    assert code == 0
    assert calls == ALL_ITEMS
    assert all(_status(lines, item) == "built" for item in model_fetch.BUILT_ITEMS)
    manifest = model_manifest.load_manifest(paths.manifest)
    assert {entry.item for entry in manifest.entries} == {
        "clip",
        "dino",
        "zeroshot",
        "yolo",
        "ocr",
        "aesthetic",
        "face",
    }
    assert {e.path for e in manifest.for_item("aesthetic")} == {
        "models/sa_0_4_vit_b_32_linear.pth",
        "models/sa_0_4_vit_b_32_linear.npz",
    }
    assert any(line.endswith("entries, verified") for line in lines)

    code, lines = _run(paths)

    assert code == 0
    assert calls == ALL_ITEMS  # nothing rebuilt
    assert all(_status(lines, item) == "present" for item in model_fetch.BUILT_ITEMS)
    assert model_manifest.load_manifest(paths.manifest).entries == manifest.entries


def test_force_rebuilds_one_item(paths: ModelPaths, calls: list[str]) -> None:
    _run(paths)

    code, lines = _run(paths, "dino")

    assert code == 0
    assert calls[-1] == "dino"
    assert _status(lines, "dino") == "built"
    assert _status(lines, "clip") == "present"


def test_a_file_already_in_place_is_recorded_as_found(paths: ModelPaths, calls: list[str]) -> None:
    paths.clip.parent.mkdir(parents=True)
    paths.clip.write_bytes(b"older export")
    paths.face.write_bytes(b"copied by hand")

    code, lines = _run(paths)

    assert code == 0
    assert "clip" not in calls
    assert "face" not in calls
    manifest = model_manifest.load_manifest(paths.manifest)
    for item in ("clip", "face"):
        assert _status(lines, item) == "present"
        (entry,) = manifest.for_item(item)
        assert entry.source == model_fetch.FOUND_SOURCE


def test_without_export_tools_it_builds_the_rest_and_says_how_to_install(
    paths: ModelPaths, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_fetch, "export_tools_available", lambda: False)

    code, lines = _run(paths)

    assert code == 1
    assert calls == ["yolo", "ocr", "face"]
    for item in ("clip", "dino", "zeroshot", "aesthetic"):
        assert _status(lines, item) == "failed"
    assert _status(lines, "yolo") == "built"
    assert _status(lines, "face") == "built"
    assert f"Install them with: {model_fetch.EXPORT_INSTALL_COMMAND}" in lines[-2]
    assert not paths.clip.exists()


def test_a_failed_build_leaves_no_partial_file(
    paths: ModelPaths, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(model_paths: ModelPaths) -> Built:
        with staged(model_paths.dino) as partial:
            partial.write_bytes(b"half")
            raise BuildError("ONNX output differs from PyTorch: minimum cosine 0.9")

    monkeypatch.setitem(model_fetch.BUILDERS, "dino", broken)

    code, lines = _run(paths)

    assert code == 1
    assert _status(lines, "dino") == "failed"
    assert "minimum cosine 0.9" in next(line for line in lines if line.startswith("dino"))
    assert not paths.dino.exists()
    assert list(paths.dino.parent.glob(".*partial*")) == []


def test_a_failed_rebuild_keeps_the_old_file_and_entry(
    paths: ModelPaths, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(paths)
    before = paths.dino.read_bytes()

    def broken(model_paths: ModelPaths) -> Built:
        raise BuildError("export crashed")

    monkeypatch.setitem(model_fetch.BUILDERS, "dino", broken)
    code, _ = _run(paths, "dino")

    assert code == 1
    assert paths.dino.read_bytes() == before
    assert model_manifest.load_manifest(paths.manifest).for_item("dino")


def test_a_present_file_that_no_longer_matches_fails(paths: ModelPaths, calls: list[str]) -> None:
    _run(paths)
    paths.yolo.write_bytes(b"tampered, and longer")

    code, lines = _run(paths)

    assert code == 1
    yolo_line = next(line for line in lines if line.startswith("yolo"))
    assert "size mismatch" in yolo_line
    assert "--force yolo" in yolo_line


def test_unknown_force_item_is_rejected(paths: ModelPaths) -> None:
    with pytest.raises(ValueError, match="cannot force"):
        fetch(force=["bogus"], paths=paths, echo=lambda _line: None)


def test_staged_renames_only_on_success(tmp_path: Path) -> None:
    target = tmp_path / "model.onnx"
    target.write_bytes(b"old")

    with pytest.raises(RuntimeError), staged(target) as partial:
        partial.write_bytes(b"new")
        raise RuntimeError("boom")

    assert target.read_bytes() == b"old"
    assert not partial.exists()

    with staged(target) as partial:
        partial.write_bytes(b"new")

    assert target.read_bytes() == b"new"
    assert not partial.exists()


def _axes_file(path: Path, names: list[str], directions: np.ndarray) -> Path:
    np.savez(
        path,
        axis_names=unicode_array(names),
        directions=directions.astype(np.float32),
        model_name=unicode_scalar("clip-vit-b-32"),
    )
    return path


def test_check_axes_accepts_three_unit_rows(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    directions = rng.standard_normal((3, 512))
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)

    model_fetch.check_axes(_axes_file(tmp_path / "axes.npz", list(AXIS_PROMPTS), directions))


@pytest.mark.parametrize(
    ("names", "directions", "message"),
    [
        (["a", "b", "c"], np.eye(3, 512), "named"),
        (list(AXIS_PROMPTS), np.eye(3, 512) * 2, "unit length"),
        (list(AXIS_PROMPTS)[:2], np.eye(2, 512), "named"),
    ],
)
def test_check_axes_rejects_bad_artifacts(
    tmp_path: Path, names: list[str], directions: np.ndarray, message: str
) -> None:
    with pytest.raises(BuildError, match=message):
        model_fetch.check_axes(_axes_file(tmp_path / "axes.npz", names, directions))


def test_cli_parses_repeated_force() -> None:
    args = cli._build_parser().parse_args(["models", "fetch", "--force", "clip", "--force", "ocr"])
    assert args.force == ["clip", "ocr"]
    assert cli.MODEL_ITEMS == model_fetch.BUILT_ITEMS


def test_cli_rejects_an_item_it_cannot_build(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli._build_parser().parse_args(["models", "fetch", "--force", "bogus"])
    assert exc.value.code == 2


# ── Downloaded models: the aesthetic head and the face detector ───────────


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """What the fake download serves, by URL. Unit tests never touch the network."""
    files: dict[str, bytes] = {}

    def fake_download(url: str, dest: Path) -> None:
        dest.write_bytes(files[url])

    monkeypatch.setattr(model_fetch, "download", fake_download)
    return files


HEAD_WEIGHT = np.linspace(-1.0, 1.0, 512, dtype=np.float32)
HEAD_BIAS = np.float32(3.5)


@pytest.fixture
def laion(served: dict[str, bytes], monkeypatch: pytest.MonkeyPatch) -> bytes:
    """A fake LAION checkpoint, its pinned checksums, and a converter that needs no torch."""
    from marquee.ml import aesthetic

    checkpoint = b"fake LAION checkpoint"
    served[model_fetch.AESTHETIC_URL] = checkpoint
    monkeypatch.setattr(model_fetch, "AESTHETIC_SHA256", _sha256(checkpoint))
    values = HEAD_WEIGHT.astype("<f4").tobytes() + np.array([HEAD_BIAS], "<f4").tobytes()
    monkeypatch.setattr(model_fetch, "AESTHETIC_VALUES_SHA256", _sha256(values))

    def convert(pth_path: Path, npz_path: Path) -> None:
        assert pth_path.read_bytes() == checkpoint
        np.savez(npz_path, weight=HEAD_WEIGHT, bias=HEAD_BIAS)

    monkeypatch.setattr(aesthetic, "convert_pth_to_npz", convert)
    return checkpoint


def _model_pack(member: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as pack:
        pack.writestr(model_fetch.FACE_ARCHIVE_MEMBER, member)
        pack.writestr("w600k_mbf.onnx", b"recognition model")
    return buffer.getvalue()


@pytest.fixture
def insightface(served: dict[str, bytes], monkeypatch: pytest.MonkeyPatch) -> bytes:
    """A fake buffalo_sc pack and its pinned checksums."""
    detector = b"fake SCRFD detector"
    pack = _model_pack(detector)
    served[model_fetch.FACE_ARCHIVE_URL] = pack
    monkeypatch.setattr(model_fetch, "FACE_ARCHIVE_SHA256", _sha256(pack))
    monkeypatch.setattr(model_fetch, "FACE_SHA256", _sha256(detector))
    return detector


def _no_partials(folder: Path) -> bool:
    return not folder.exists() or list(folder.glob(".*partial*")) == []


def test_fetches_the_aesthetic_head_and_the_face_detector(
    paths: ModelPaths,
    calls: list[str],
    laion: bytes,
    insightface: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(model_fetch.BUILDERS, "aesthetic", model_fetch.build_aesthetic)
    monkeypatch.setitem(model_fetch.BUILDERS, "face", model_fetch.build_face)

    code, lines = _run(paths)

    assert code == 0
    assert _status(lines, "aesthetic") == "built"
    assert _status(lines, "face") == "built"
    pth, npz = paths.aesthetic_files()
    assert pth.read_bytes() == laion
    with np.load(npz) as head:
        assert np.array_equal(head["weight"], HEAD_WEIGHT)
    assert paths.face.read_bytes() == insightface
    assert list(paths.cache_dir.iterdir()) == []  # the downloaded pack is gone
    manifest = model_manifest.load_manifest(paths.manifest)
    assert {entry.path for entry in manifest.for_item("aesthetic")} == {
        "models/sa_0_4_vit_b_32_linear.pth",
        "models/sa_0_4_vit_b_32_linear.npz",
    }
    (face,) = manifest.for_item("face")
    assert face.sha256 == model_fetch.FACE_SHA256
    assert model_fetch.FACE_ARCHIVE_URL in face.source
    assert any(line.endswith("entries, verified") for line in lines)


def test_an_aesthetic_head_with_the_wrong_checksum_is_not_kept(
    paths: ModelPaths, laion: bytes, served: dict[str, bytes]
) -> None:
    served[model_fetch.AESTHETIC_URL] = b"something else"

    with pytest.raises(BuildError, match="SHA-256"):
        model_fetch.build_aesthetic(paths)

    pth, npz = paths.aesthetic_files()
    assert not pth.exists()
    assert not npz.exists()
    assert _no_partials(pth.parent)


def test_a_converted_head_with_the_wrong_values_is_not_kept(
    paths: ModelPaths, laion: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_fetch, "AESTHETIC_VALUES_SHA256", "0" * 64)

    with pytest.raises(BuildError, match="values"):
        model_fetch.build_aesthetic(paths)

    assert not paths.aesthetic_files()[1].exists()
    assert _no_partials(paths.aesthetic.parent)


@pytest.mark.parametrize("pinned", ["FACE_ARCHIVE_SHA256", "FACE_SHA256"])
def test_a_face_detector_with_the_wrong_checksum_is_not_kept(
    paths: ModelPaths, insightface: bytes, monkeypatch: pytest.MonkeyPatch, pinned: str
) -> None:
    monkeypatch.setattr(model_fetch, pinned, "0" * 64)

    with pytest.raises(BuildError, match="SHA-256"):
        model_fetch.build_face(paths)

    assert not paths.face.exists()
    assert _no_partials(paths.face.parent)
    assert list(paths.cache_dir.iterdir()) == []


def test_aesthetic_values_checksum_reads_weights_then_bias(tmp_path: Path) -> None:
    npz = tmp_path / "head.npz"
    np.savez(npz, weight=HEAD_WEIGHT, bias=HEAD_BIAS)
    expected = _sha256(HEAD_WEIGHT.tobytes() + np.array([HEAD_BIAS], "<f4").tobytes())
    assert model_fetch.aesthetic_values_sha256(npz) == expected

    np.savez(npz, weight=HEAD_WEIGHT[:10], bias=HEAD_BIAS)
    with pytest.raises(BuildError, match="shapes"):
        model_fetch.aesthetic_values_sha256(npz)
