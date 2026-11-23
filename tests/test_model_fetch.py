from __future__ import annotations

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


def _carried(paths: ModelPaths) -> None:
    paths.aesthetic.parent.mkdir(parents=True, exist_ok=True)
    paths.aesthetic.write_bytes(b"pth")
    paths.aesthetic.with_suffix(".npz").write_bytes(b"npz")
    paths.face.write_bytes(b"face")


def _fake_file_builder(item: str, calls: list[str]):
    def build(paths: ModelPaths) -> Built:
        calls.append(item)
        target = getattr(paths, item)
        with staged(target) as partial:
            partial.write_bytes(f"{item} weights".encode())
        entry = model_manifest.describe(paths.data_dir, target, item=item, source="fake")
        return Built([entry], "fake check passed")

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
        item: _fake_file_builder(item, made) for item in ("clip", "dino", "zeroshot", "yolo")
    }
    builders["ocr"] = _fake_ocr_builder(made)
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


def test_builds_everything_missing_then_leaves_it_alone(
    paths: ModelPaths, calls: list[str]
) -> None:
    _carried(paths)

    code, lines = _run(paths)

    assert code == 0
    assert calls == ["clip", "dino", "zeroshot", "yolo", "ocr"]
    assert all(_status(lines, item) == "built" for item in model_fetch.BUILT_ITEMS)
    assert _status(lines, "aesthetic") == "present"
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
    assert manifest.for_item("face")[0].source == model_fetch.CARRIED_SOURCE
    assert any(line.endswith("entries, verified") for line in lines)

    code, lines = _run(paths)

    assert code == 0
    assert calls == ["clip", "dino", "zeroshot", "yolo", "ocr"]  # nothing rebuilt
    assert all(_status(lines, item) == "present" for item in model_fetch.BUILT_ITEMS)
    assert model_manifest.load_manifest(paths.manifest).entries == manifest.entries


def test_force_rebuilds_one_item(paths: ModelPaths, calls: list[str]) -> None:
    _carried(paths)
    _run(paths)

    code, lines = _run(paths, "dino")

    assert code == 0
    assert calls[-1] == "dino"
    assert _status(lines, "dino") == "built"
    assert _status(lines, "clip") == "present"


def test_a_file_already_in_place_is_recorded_as_found(paths: ModelPaths, calls: list[str]) -> None:
    _carried(paths)
    paths.clip.write_bytes(b"older export")

    code, lines = _run(paths)

    assert code == 0
    assert "clip" not in calls
    assert _status(lines, "clip") == "present"
    (entry,) = model_manifest.load_manifest(paths.manifest).for_item("clip")
    assert entry.source == model_fetch.FOUND_SOURCE


def test_without_export_tools_it_builds_the_rest_and_says_how_to_install(
    paths: ModelPaths, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _carried(paths)
    monkeypatch.setattr(model_fetch, "export_tools_available", lambda: False)

    code, lines = _run(paths)

    assert code == 1
    assert calls == ["yolo", "ocr"]
    for item in ("clip", "dino", "zeroshot"):
        assert _status(lines, item) == "failed"
    assert _status(lines, "yolo") == "built"
    assert f"Install them with: {model_fetch.EXPORT_INSTALL_COMMAND}" in lines[-2]
    assert not paths.clip.exists()


def test_missing_carried_models_are_reported_and_fail(paths: ModelPaths, calls: list[str]) -> None:
    code, lines = _run(paths)

    assert code == 1
    assert _status(lines, "aesthetic") == "missing"
    assert _status(lines, "face") == "missing"
    face_line = next(line for line in lines if line.startswith("face"))
    assert "cannot be fetched yet" in face_line
    assert "scrfd_500m_bnkps.onnx" in face_line
    assert calls == ["clip", "dino", "zeroshot", "yolo", "ocr"]


def test_a_failed_build_leaves_no_partial_file(
    paths: ModelPaths, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _carried(paths)

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
    _carried(paths)
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
    _carried(paths)
    _run(paths)
    paths.yolo.write_bytes(b"tampered, and longer")

    code, lines = _run(paths)

    assert code == 1
    yolo_line = next(line for line in lines if line.startswith("yolo"))
    assert "size mismatch" in yolo_line
    assert "--force yolo" in yolo_line


def test_unknown_force_item_is_rejected(paths: ModelPaths) -> None:
    with pytest.raises(ValueError, match="cannot force"):
        fetch(force=["face"], paths=paths, echo=lambda _line: None)


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


def test_cli_rejects_an_item_it_cannot_build(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli._build_parser().parse_args(["models", "fetch", "--force", "face"])
    assert exc.value.code == 2
