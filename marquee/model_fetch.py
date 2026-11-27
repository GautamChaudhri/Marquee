"""`marquee models fetch`: build every model the engine can use and record their checksums.

Items, in the order they are built:

=========  ====================================================  ===========================
item       built by                                              lands at
=========  ====================================================  ===========================
clip       ``ml/clip_export.py::export_vision_onnx``             ``CLIP_MODEL_PATH``
dino       ``ml/dino.py::export_dino_onnx``                      ``DINO_MODEL_PATH``
zeroshot   ``ml/zeroshot.py::build_axes``                        ``ZEROSHOT_AXES_PATH``
yolo       the Ultralytics exporter, in its own ``uv`` tool env  ``PERSON_MODEL_PATH``
ocr        one load through ``pipeline/ocr_filter.py::_load_ocr``  ``<models>/paddlex/``
aesthetic  downloaded from LAION, converted to ``.npz``          ``AESTHETIC_MODEL_PATH``
face       downloaded from InsightFace (``buffalo_sc`` pack)     ``FACE_MODEL_PATH``
=========  ====================================================  ===========================

What is present is left alone; what is missing (or named with ``--force``) is built. Every
build writes to a temporary name in its destination folder and is renamed into place only
after its check passes, so a failed export never leaves a file the engine would load. A
download's check is its SHA-256, pinned below. The manifest (``data/models/manifest.json``)
is then written and verified.

``clip``, ``dino``, ``zeroshot``, and ``aesthetic`` (its ``.pth`` to ``.npz`` conversion)
need the export tools (the ``export`` dependency group). No runtime code path imports them.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from multiprocessing import get_context
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np

from marquee.config import pipeline_settings, settings
from marquee.ml import model_manifest
from marquee.ml.model_manifest import Manifest, ManifestEntry, ManifestError

if TYPE_CHECKING:
    import torch

logger = logging.getLogger(__name__)

BUILT_ITEMS = ("clip", "dino", "zeroshot", "yolo", "ocr", "aesthetic", "face")
NEEDS_EXPORT_TOOLS = frozenset({"clip", "dino", "zeroshot", "aesthetic"})
EXPORT_TOOLS = ("torch", "transformers", "onnx")
EXPORT_INSTALL_COMMAND = "uv sync --extra nvidia --group export"

MIN_COSINE = 0.9999
CHECK_BATCH = 4

# The YOLO exporter runs in an isolated uv tool environment: Ultralytics depends on
# opencv-python, which would replace the OpenCV build PaddleOCR requires.
ULTRALYTICS_VERSION = "8.4.216"
YOLO_WEIGHTS = "yolo11n.pt"
YOLO_TOOL_PACKAGES = ("onnx==1.23.2", "onnxslim==0.1.99", "onnxruntime==1.26.0")
YOLO_TOOL_PYTHON = "3.12"
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
YOLO_EXPORT_TIMEOUT_S = 1800
YOLO_INPUT_SIZE = 640
YOLO_OUTPUT_ROWS = 84

# Downloads are pinned by SHA-256. The values are those of the files the engine was tuned
# with, as recorded in the first manifest; each source was checked to serve those bytes.
DOWNLOAD_TIMEOUT_S = 60.0
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024

# LAION's linear aesthetic head for CLIP ViT-B/32, from the repository at a fixed commit.
AESTHETIC_URL = (
    "https://raw.githubusercontent.com/LAION-AI/aesthetic-predictor/"
    "814214009feff106add4bb983807704ccdd5a0db/sa_0_4_vit_b_32_linear.pth"
)
AESTHETIC_SHA256 = "c7b14cead230694acc7b9447974d3cad78003c72da032e402a303b6c2429e85f"
# np.savez stamps the time into the .npz, so the converted file is checked by its values:
# the SHA-256 of the 512 weights and then the bias, as little-endian float32.
AESTHETIC_VALUES_SHA256 = "dba6421aa35c4489c02b02d8d2a3ad9a495d50f85d35eef521fd551619bbb2ac"

# InsightFace's SCRFD 500M detector with keypoints (scrfd_500m_bnkps.onnx). InsightFace
# publishes it as det_500m.onnx inside the buffalo_sc model pack; the bytes are identical.
FACE_ARCHIVE_URL = (
    "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_sc.zip"
)
FACE_ARCHIVE_SHA256 = "57d31b56b6ffa911c8a73cfc1707c73cab76efe7f13b675a05223bf42de47c72"
FACE_ARCHIVE_MEMBER = "det_500m.onnx"
FACE_SHA256 = "5e4447f50245bbd7966bd6c0fa52938c61474a04ec7def48753668a9d8b4ea3a"

FOUND_SOURCE = "found in place; not built by marquee models fetch"

Status = Literal["built", "present", "failed"]


class BuildError(Exception):
    """A build ran but its result failed a check, or a prerequisite is missing."""


@dataclass(frozen=True)
class ModelPaths:
    """Where every model lives. Built from the settings; tests pass their own."""

    data_dir: Path
    manifest: Path
    cache_dir: Path
    ocr_dir: Path
    clip: Path
    dino: Path
    zeroshot: Path
    yolo: Path
    aesthetic: Path
    face: Path

    @classmethod
    def from_settings(cls) -> ModelPaths:
        return cls(
            data_dir=settings.DATA_DIR,
            manifest=settings.model_manifest_path,
            cache_dir=settings.cache_dir,
            ocr_dir=settings.ocr_models_dir,
            clip=Path(pipeline_settings.CLIP_MODEL_PATH),
            dino=Path(pipeline_settings.DINO_MODEL_PATH),
            zeroshot=Path(pipeline_settings.ZEROSHOT_AXES_PATH),
            yolo=Path(pipeline_settings.PERSON_MODEL_PATH),
            aesthetic=Path(pipeline_settings.AESTHETIC_MODEL_PATH),
            face=Path(pipeline_settings.FACE_MODEL_PATH),
        )

    def aesthetic_files(self) -> tuple[Path, Path]:
        """The LAION checkpoint and the numpy sidecar the engine actually loads."""
        npz = (
            self.aesthetic
            if self.aesthetic.suffix == ".npz"
            else self.aesthetic.with_suffix(".npz")
        )
        pth = self.aesthetic.with_suffix(".pth")
        return pth, npz


@dataclass
class ItemResult:
    item: str
    status: Status
    detail: str
    entries: list[ManifestEntry] = field(default_factory=list)
    seconds: float = 0.0


@dataclass(frozen=True)
class Built:
    """What a builder hands back: the manifest entries and a short note for the console."""

    entries: list[ManifestEntry]
    note: str


Builder = Callable[[ModelPaths], Built]


# ── Helpers ────────────────────────────────────────────────────────────────


def export_tools_available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in EXPORT_TOOLS)


def _dist_version(*names: str) -> str:
    for name in names:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "not installed"


def _tool_versions(*names: str) -> dict[str, str]:
    versions = {name: _dist_version(name) for name in names}
    versions["onnxruntime"] = _dist_version("onnxruntime-gpu", "onnxruntime")
    return versions


def _rel(paths: ModelPaths, path: Path) -> str:
    try:
        return model_manifest.relative_to_data(paths.data_dir, path)
    except ValueError:
        return str(path)


def _partial_path(target: Path) -> Path:
    return target.with_name(f".{target.stem}.partial{target.suffix}")


@contextmanager
def staged(target: Path) -> Iterator[Path]:
    """Yield a temporary path beside ``target``; rename it over ``target`` on success.

    On any exception the temporary file is removed and ``target`` is left as it was.
    """
    partial = _partial_path(target)
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.unlink(missing_ok=True)
    try:
        yield partial
        if not partial.is_file():
            raise BuildError(f"the build did not write {partial.name}")
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, target)


def download(url: str, dest: Path) -> None:
    """Stream ``url`` into ``dest``, refusing anything larger than ``MAX_DOWNLOAD_BYTES``.

    Unit tests replace this function; they never touch the network.
    """
    import httpx

    received = 0
    with (
        httpx.stream("GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_S) as response,
        dest.open("wb") as out,
    ):
        response.raise_for_status()
        for chunk in response.iter_bytes():
            received += len(chunk)
            if received > MAX_DOWNLOAD_BYTES:
                raise BuildError(f"{url} is larger than {MAX_DOWNLOAD_BYTES} bytes")
            out.write(chunk)


def check_sha256(path: Path, expected: str, *, name: str) -> None:
    actual = model_manifest.sha256_file(path)
    if actual != expected:
        raise BuildError(f"{name} has SHA-256 {actual}, expected {expected}")


def aesthetic_values_sha256(npz_path: Path) -> str:
    """SHA-256 of the head's 512 weights followed by its bias, as little-endian float32."""
    import hashlib

    with np.load(npz_path) as data:
        weight = np.asarray(data["weight"])
        bias = np.asarray(data["bias"])
    if weight.shape != (512,) or bias.size != 1:
        raise BuildError(f"aesthetic head has shapes {weight.shape} and {bias.shape}")
    values = weight.astype("<f4").tobytes() + bias.astype("<f4").reshape(1).tobytes()
    return hashlib.sha256(values).hexdigest()


def _row_cosines(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    return np.sum(a * b, axis=1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))


def check_against_torch(
    onnx_path: Path, module: torch.nn.Module, *, input_size: int, dim: int
) -> float:
    """Run one fixed batch through PyTorch and the ONNX export; return the minimum cosine.

    The export must accept a batch of 4 and a batch of 1, and every row must agree with
    PyTorch to a cosine of at least ``MIN_COSINE``.
    """
    import onnxruntime as ort
    import torch

    generator = torch.Generator().manual_seed(0)
    batch = torch.randn(CHECK_BATCH, 3, input_size, input_size, generator=generator)
    with torch.no_grad():
        expected = module(batch).numpy()

    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name
    many = session.run(None, {name: batch.numpy()})[0]
    one = session.run(None, {name: batch[:1].numpy()})[0]
    if many.shape != (CHECK_BATCH, dim) or one.shape != (1, dim):
        raise BuildError(
            f"export gave shapes {many.shape} and {one.shape} for batches of "
            f"{CHECK_BATCH} and 1; expected ({CHECK_BATCH}, {dim}) and (1, {dim})"
        )
    cosines = np.concatenate([_row_cosines(many, expected), _row_cosines(one, expected[:1])])
    worst = float(np.min(cosines))
    if not np.all(np.isfinite(cosines)) or worst < MIN_COSINE:
        raise BuildError(f"ONNX output differs from PyTorch: minimum cosine {worst:.6f}")
    return worst


def check_axes(path: Path) -> None:
    """Three unit-length, finite rows of 512 values, named as ``AXIS_PROMPTS`` names them."""
    from marquee.ml.zeroshot import AXIS_PROMPTS, ZeroShotAxes

    axes = ZeroShotAxes.load(path)
    if axes is None:
        raise BuildError(f"{path.name} did not load as a zero-shot axes artifact")
    expected = list(AXIS_PROMPTS)
    if axes.axis_names != expected:
        raise BuildError(f"axes are named {axes.axis_names}, expected {expected}")
    directions = axes.directions
    if directions.shape != (len(expected), 512) or not np.all(np.isfinite(directions)):
        raise BuildError(f"axes have shape {directions.shape} or non-finite values")
    norms = np.linalg.norm(directions, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-4):
        raise BuildError(f"axes are not unit length: norms {norms.tolist()}")


def check_yolo(path: Path) -> tuple[int, ...]:
    """A 640x640 input must give an output shaped ``(1, 84, N)``."""
    import onnxruntime as ort

    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name
    zeros = np.zeros((1, 3, YOLO_INPUT_SIZE, YOLO_INPUT_SIZE), dtype=np.float32)
    output = session.run(None, {name: zeros})[0]
    if output.ndim != 3 or output.shape[:2] != (1, YOLO_OUTPUT_ROWS) or output.shape[2] < 1:
        raise BuildError(f"YOLO output has shape {output.shape}; expected (1, 84, N)")
    return tuple(int(n) for n in output.shape)


# ── Builders ───────────────────────────────────────────────────────────────


def build_clip(paths: ModelPaths) -> Built:
    from marquee.ml.clip_export import export_vision_onnx

    with staged(paths.clip) as partial:
        module = export_vision_onnx(partial)
        cosine = check_against_torch(partial, module, input_size=224, dim=512)
    entry = model_manifest.describe(
        paths.data_dir,
        paths.clip,
        item="clip",
        source="Hugging Face openai/clip-vit-base-patch32, image tower exported to ONNX "
        "(opset 14, dynamic batch axis)",
        tools=_tool_versions("torch", "transformers", "onnx"),
        details={"min_cosine_vs_pytorch": f"{cosine:.6f}"},
    )
    return Built([entry], f"ONNX matches PyTorch (min cosine {cosine:.6f}); batches of 1 and 4")


def _dino_weights_sha256() -> str | None:
    import torch

    weights = Path(torch.hub.get_dir()) / "checkpoints" / "dinov2_vits14_pretrain.pth"
    return model_manifest.sha256_file(weights) if weights.is_file() else None


def build_dino(paths: ModelPaths) -> Built:
    from marquee.ml.dino import DINO_EMBED_DIM, DINO_INPUT_SIZE, export_dino_onnx

    with staged(paths.dino) as partial:
        module = export_dino_onnx(partial)
        cosine = check_against_torch(
            partial, module, input_size=DINO_INPUT_SIZE, dim=DINO_EMBED_DIM
        )
    details = {"min_cosine_vs_pytorch": f"{cosine:.6f}"}
    weights_sha = _dino_weights_sha256()
    if weights_sha:
        details["dinov2_vits14_pretrain.pth sha256"] = weights_sha
    entry = model_manifest.describe(
        paths.data_dir,
        paths.dino,
        item="dino",
        source="torch.hub facebookresearch/dinov2 dinov2_vits14, exported to ONNX "
        "(opset 17, dynamic batch axis)",
        tools=_tool_versions("torch", "onnx"),
        details=details,
    )
    return Built([entry], f"ONNX matches PyTorch (min cosine {cosine:.6f}); batches of 1 and 4")


def build_zeroshot(paths: ModelPaths) -> Built:
    from marquee.ml.zeroshot import AXIS_PROMPTS, build_axes

    with staged(paths.zeroshot) as partial:
        build_axes(partial)
        check_axes(partial)
    entry = model_manifest.describe(
        paths.data_dir,
        paths.zeroshot,
        item="zeroshot",
        source="CLIP text tower (Hugging Face openai/clip-vit-base-patch32) over AXIS_PROMPTS",
        tools=_tool_versions("torch", "transformers"),
        details={"axes": ", ".join(AXIS_PROMPTS)},
    )
    return Built([entry], f"{len(AXIS_PROMPTS)} unit-length axes of 512 values")


def _uv_version(uv: str) -> str:
    result = subprocess.run([uv, "--version"], capture_output=True, text=True, check=False)
    return result.stdout.strip() or "unknown"


def build_yolo(paths: ModelPaths) -> Built:
    uv = shutil.which("uv")
    if uv is None:
        raise BuildError("needs uv on the PATH to run the Ultralytics exporter in its own env")
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="yolo-export-", dir=paths.cache_dir))
    command = [
        uv,
        "tool",
        "run",
        "--python",
        YOLO_TOOL_PYTHON,
        "--index",
        PYTORCH_CPU_INDEX,
        "--from",
        f"ultralytics=={ULTRALYTICS_VERSION}",
        *(arg for package in YOLO_TOOL_PACKAGES for arg in ("--with", package)),
        "yolo",
        "export",
        f"model={YOLO_WEIGHTS}",
        "format=onnx",
        "dynamic=True",
    ]
    # Keep Ultralytics' settings file inside the scratch folder, not the home folder.
    env = dict(os.environ)
    env["YOLO_CONFIG_DIR"] = str(scratch / "config")
    try:
        logger.info("YOLO | %s", " ".join(command))
        result = subprocess.run(
            command,
            cwd=scratch,
            env=env,
            capture_output=True,
            text=True,
            timeout=YOLO_EXPORT_TIMEOUT_S,
            check=False,
        )
        for line in (result.stdout + result.stderr).splitlines():
            logger.info("YOLO | %s", line)
        exported = scratch / Path(YOLO_WEIGHTS).with_suffix(".onnx").name
        if result.returncode != 0 or not exported.is_file():
            tail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
            raise BuildError(f"Ultralytics export exited {result.returncode}: {tail[0]}")
        shape = check_yolo(exported)
        weights_sha = model_manifest.sha256_file(scratch / YOLO_WEIGHTS)
        with staged(paths.yolo) as partial:
            shutil.copyfile(exported, partial)
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"Ultralytics export took longer than {exc.timeout:.0f} s") from exc
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    entry = model_manifest.describe(
        paths.data_dir,
        paths.yolo,
        item="yolo",
        source=f"Ultralytics {YOLO_WEIGHTS} exported to ONNX (dynamic=True, no NMS) in an "
        "isolated uv tool environment with the CPU build of PyTorch",
        tools={
            "ultralytics": ULTRALYTICS_VERSION,
            **dict(package.split("==") for package in YOLO_TOOL_PACKAGES),
            "uv": _uv_version(uv),
        },
        details={f"{YOLO_WEIGHTS} sha256": weights_sha},
    )
    return Built([entry], f"640x640 input gives output {shape}")


# A first OCR load downloads the weights through huggingface_hub, whose HTTP client logs
# every request at INFO; PaddlePaddle then sets the probe's root logger to INFO with its own
# handler, so they would all reach the console. httpx2 and httpcore2 are the second copy of
# httpx that huggingface_hub uses.
DOWNLOAD_LOGGERS = ("httpx", "httpcore", "httpx2", "httpcore2", "huggingface_hub")


def _quiet_download_logging() -> None:
    for name in DOWNLOAD_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _ocr_probe() -> dict[str, object]:
    """Load PaddleOCR once and report the device and the model folders it opened.

    Runs in a spawned process, so PaddlePaddle never shares a process with PyTorch, and
    so the audit hook that watches file opens does not outlive the probe.
    """
    import warnings

    warnings.simplefilter("ignore")
    _quiet_download_logging()
    # ocr_filter imports marquee.ml.hardware, which points PaddleX at the data directory.
    from marquee.pipeline import ocr_filter

    opened: set[str] = set()

    def watch_opens(event: str, args: tuple[object, ...]) -> None:
        if event == "open" and args and isinstance(args[0], str | bytes | os.PathLike):
            opened.add(os.fsdecode(args[0]))

    cuda_ok, diagnostic = ocr_filter._paddle_cuda_probe(allow_import=True)
    device = ocr_filter._resolve_ocr_device(
        paddle_cuda_available=cuda_ok, cuda_diagnostic=diagnostic
    )
    import paddleocr

    logging.getLogger("paddlex").setLevel(logging.ERROR)
    sys.addaudithook(watch_opens)
    ocr_filter._load_ocr()

    from paddlex.utils.cache import CACHE_DIR

    official = (Path(CACHE_DIR) / "official_models").resolve()
    folders = set()
    for name in opened:
        path = Path(name)
        if not path.is_absolute():
            continue
        try:
            folders.add(path.resolve().relative_to(official).parts[0])
        except (ValueError, IndexError):
            continue
    return {
        "device": device,
        "cuda_diagnostic": diagnostic,
        "cache_dir": str(Path(CACHE_DIR).resolve()),
        "folders": sorted(folders),
        "versions": {
            "paddleocr": paddleocr.__version__,
            "paddlex": _dist_version("paddlex"),
            "paddlepaddle": _dist_version("paddlepaddle-gpu", "paddlepaddle"),
        },
    }


def build_ocr(paths: ModelPaths) -> Built:
    with ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn")) as pool:
        probe = pool.submit(_ocr_probe).result()
    cache_dir = Path(str(probe["cache_dir"]))
    if cache_dir != paths.ocr_dir.resolve():
        raise BuildError(
            f"PaddleX used {cache_dir}, not {paths.ocr_dir}; is PADDLE_PDX_CACHE_HOME set "
            "to another folder?"
        )
    folders = list(probe["folders"])  # type: ignore[call-overload]
    if not folders:
        raise BuildError("could not tell which model folders PaddleOCR opened")
    versions = dict(probe["versions"])  # type: ignore[call-overload]
    device = str(probe["device"])
    entries = [
        model_manifest.describe(
            paths.data_dir,
            cache_dir / "official_models" / folder,
            item="ocr",
            source="PaddleX official model, downloaded by PaddleOCR on first load "
            "(pipeline/ocr_filter.py::_load_ocr)",
            tools=versions,
            details={"device": device},
        )
        for folder in folders
    ]
    return Built(entries, f"loaded on {device}; {len(folders)} model folders: {', '.join(folders)}")


def build_aesthetic(paths: ModelPaths) -> Built:
    from marquee.ml import aesthetic

    pth, npz = paths.aesthetic_files()
    with staged(pth) as partial:
        download(AESTHETIC_URL, partial)
        check_sha256(partial, AESTHETIC_SHA256, name=pth.name)
    with staged(npz) as partial:
        aesthetic.convert_pth_to_npz(pth, partial)
        values = aesthetic_values_sha256(partial)
        if values != AESTHETIC_VALUES_SHA256:
            raise BuildError(
                f"{npz.name} values have SHA-256 {values}, expected {AESTHETIC_VALUES_SHA256}"
            )
    source = f"LAION-AI/aesthetic-predictor, {AESTHETIC_URL}"
    entries = [
        model_manifest.describe(
            paths.data_dir,
            pth,
            item="aesthetic",
            source=source,
            details={"pinned sha256": AESTHETIC_SHA256},
        ),
        model_manifest.describe(
            paths.data_dir,
            npz,
            item="aesthetic",
            source=f"converted from {pth.name} (ml/aesthetic.py::convert_pth_to_npz)",
            tools=_tool_versions("torch"),
            details={"values sha256": AESTHETIC_VALUES_SHA256},
        ),
    ]
    return Built(entries, ".pth matches its pinned SHA-256; .npz values match theirs")


def build_face(paths: ModelPaths) -> Built:
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="face-fetch-", dir=paths.cache_dir))
    try:
        archive = scratch / Path(FACE_ARCHIVE_URL).name
        download(FACE_ARCHIVE_URL, archive)
        # Verified before it is opened, so nothing unchecked is ever unpacked.
        check_sha256(archive, FACE_ARCHIVE_SHA256, name=archive.name)
        with zipfile.ZipFile(archive) as pack, staged(paths.face) as partial:
            with pack.open(FACE_ARCHIVE_MEMBER) as member, partial.open("wb") as out:
                shutil.copyfileobj(member, out)
            check_sha256(partial, FACE_SHA256, name=f"{archive.name}:{FACE_ARCHIVE_MEMBER}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    entry = model_manifest.describe(
        paths.data_dir,
        paths.face,
        item="face",
        source=f"InsightFace SCRFD 500M with keypoints: {FACE_ARCHIVE_MEMBER} from "
        f"{FACE_ARCHIVE_URL}",
        details={
            "pinned sha256": FACE_SHA256,
            f"{Path(FACE_ARCHIVE_URL).name} sha256": FACE_ARCHIVE_SHA256,
        },
    )
    return Built([entry], f"{FACE_ARCHIVE_MEMBER} matches its pinned SHA-256")


BUILDERS: dict[str, Builder] = {
    "clip": build_clip,
    "dino": build_dino,
    "zeroshot": build_zeroshot,
    "yolo": build_yolo,
    "ocr": build_ocr,
    "aesthetic": build_aesthetic,
    "face": build_face,
}


# ── The command ────────────────────────────────────────────────────────────


def _item_targets(item: str, paths: ModelPaths, previous: Manifest | None) -> list[Path]:
    if item == "ocr":
        recorded = previous.for_item("ocr") if previous else []
        return [paths.data_dir / entry.path for entry in recorded]
    if item == "aesthetic":
        return list(paths.aesthetic_files())
    return [getattr(paths, item)]


def _reuse_or_describe(
    paths: ModelPaths, item: str, target: Path, previous: Manifest | None, source: str
) -> ManifestEntry:
    """The manifest entry for a file that is already in place.

    An existing entry is kept when the file still matches it; a file the manifest does not
    know is recorded as found. A file that no longer matches its entry is an error.
    """
    recorded = previous.by_path().get(_rel(paths, target)) if previous else None
    if recorded is not None:
        problem = model_manifest.verify_entry(paths.data_dir, recorded)
        if problem is not None:
            raise BuildError(f"{recorded.path} {problem.reason} against the manifest")
        return recorded
    return model_manifest.describe(paths.data_dir, target, item=item, source=source)


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{size / 1024:.1f} KB"


def _describe_present(entries: list[ManifestEntry]) -> str:
    size = sum(entry.size for entry in entries)
    names = ", ".join(entry.path for entry in entries)
    return f"{names} ({_format_size(size)})"


def fetch_built_item(
    item: str,
    paths: ModelPaths,
    previous: Manifest | None,
    *,
    force: bool,
    tools_available: bool,
) -> ItemResult:
    started = time.perf_counter()
    kept = previous.for_item(item) if previous else []
    targets = _item_targets(item, paths, previous)
    present = bool(targets) and all(target.exists() for target in targets)

    if present and not force:
        try:
            entries = [
                _reuse_or_describe(paths, item, target, previous, FOUND_SOURCE)
                for target in targets
            ]
        except (BuildError, OSError, ValueError) as exc:
            return ItemResult(item, "failed", f"{exc}; rebuild with --force {item}", kept)
        return ItemResult(item, "present", _describe_present(entries), entries)

    if item in NEEDS_EXPORT_TOOLS and not tools_available:
        return ItemResult(item, "failed", "needs the export tools, which are not installed", kept)

    try:
        built = BUILDERS[item](paths)
    except Exception as exc:  # noqa: BLE001 - one failed item must not stop the others
        logger.exception("%s build failed", item)
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        detail = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
        return ItemResult(item, "failed", detail, kept, time.perf_counter() - started)
    detail = f"{_describe_present(built.entries)}; {built.note}"
    return ItemResult(item, "built", detail, built.entries, time.perf_counter() - started)


def _format_result(result: ItemResult) -> str:
    took = f" in {result.seconds:.0f} s" if result.status == "built" else ""
    return f"{result.item:<9}  {result.status:<7}  {result.detail}{took}"


def fetch(
    force: Sequence[str] = (),
    *,
    paths: ModelPaths | None = None,
    echo: Callable[[str], None] = print,
) -> int:
    """Build what is missing, record what is present, write and verify the manifest.

    Returns the process exit code: 0 when every item is present or built and the manifest
    verifies clean, 1 otherwise.
    """
    paths = paths or ModelPaths.from_settings()
    unknown = sorted(set(force) - set(BUILT_ITEMS))
    if unknown:
        raise ValueError(f"cannot force {', '.join(unknown)}; choose from {', '.join(BUILT_ITEMS)}")
    started = time.perf_counter()
    for folder in (paths.data_dir, paths.manifest.parent, paths.cache_dir):
        folder.mkdir(parents=True, exist_ok=True)

    try:
        previous = model_manifest.load_manifest(paths.manifest)
    except ManifestError as exc:
        echo(f"warning: {exc}; writing a new manifest")
        previous = None

    tools_available = export_tools_available()
    results: list[ItemResult] = []
    for item in BUILT_ITEMS:
        result = fetch_built_item(
            item, paths, previous, force=item in force, tools_available=tools_available
        )
        results.append(result)
        echo(_format_result(result))

    manifest = model_manifest.write_manifest(
        paths.manifest, [entry for result in results for entry in result.entries]
    )
    problems = model_manifest.verify(manifest, paths.data_dir)
    where = _rel(paths, paths.manifest)
    if problems:
        listed = "; ".join(f"{problem.path} {problem.reason}" for problem in problems)
        echo(f"manifest   {where}: {len(manifest.entries)} entries; does not verify: {listed}")
    else:
        echo(f"manifest   {where}: {len(manifest.entries)} entries, verified")

    needed_tools = any(
        result.status == "failed" and result.item in NEEDS_EXPORT_TOOLS and not tools_available
        for result in results
    )
    if needed_tools:
        echo(f"The export tools are not installed. Install them with: {EXPORT_INSTALL_COMMAND}")
    echo(f"took {time.perf_counter() - started:.0f} s")
    failed = any(result.status == "failed" for result in results)
    return 1 if failed or problems else 0
