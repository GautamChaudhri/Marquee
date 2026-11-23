"""`marquee doctor`: report whether this machine can run Marquee.

Every check is read-only. Network checks time out after five seconds, media roots are
inspected with ``os.access`` and directory listings only, and no secret is ever printed:
settings are reported by name and by whether they are set.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import httpx

from marquee.config import pipeline_settings, settings
from marquee.config.app import ENV_FILE

Status = Literal["PASS", "WARN", "FAIL"]

NETWORK_TIMEOUT_S = 5.0
# Media roots live on a network share that mounts on demand; the first listing can be
# slow, and a dead share can hang. Give up and say so rather than wait forever.
MEDIA_LIST_TIMEOUT_S = 15.0
CLIP_PROBE_BATCH = 8


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str


def _secret_values() -> list[str]:
    """Every secret value currently configured, so error text can be scrubbed."""
    values = []
    for field in (
        "POSTGRES_PASSWORD",
        "TMDB_READ_ACCESS_TOKEN",
        "RADARR_API_KEY",
    ):
        secret = getattr(settings, field)
        if secret is not None and secret.get_secret_value():
            values.append(secret.get_secret_value())
    return values


def _scrub(text: str) -> str:
    for value in _secret_values():
        text = text.replace(value, "***")
    return text


def _error_text(exc: BaseException) -> str:
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return _scrub(f"{type(exc).__name__}: {message}" if message else type(exc).__name__)


# ── Individual checks ─────────────────────────────────────────────────────


def check_python() -> CheckResult:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info[:2] == (3, 12):
        return CheckResult("Python", "PASS", version)
    return CheckResult("Python", "FAIL", f"{version}; Marquee needs 3.12 (run through uv)")


_OPTIONAL_SETTINGS = (
    "POSTGRES_PASSWORD",
    "MOVIE_ROOTS",
    "TMDB_READ_ACCESS_TOKEN",
    "RADARR_URL",
    "RADARR_API_KEY",
)


def check_settings() -> CheckResult:
    # An empty MARQUEE_ENV_FILE deliberately selects no file, as in a container, where
    # Compose passes every setting through the environment. A named file that is
    # missing is a mistake.
    if ENV_FILE is None:
        source = "settings come from the environment (MARQUEE_ENV_FILE is empty)"
    elif ENV_FILE.is_file():
        source = ".env found"
    else:
        return CheckResult("Settings", "FAIL", f".env missing: {ENV_FILE}")
    unset = [name for name in _OPTIONAL_SETTINGS if not getattr(settings, name)]
    if unset:
        return CheckResult("Settings", "WARN", f"{source}; unset: {', '.join(unset)}")
    return CheckResult("Settings", "PASS", f"{source}; environment {settings.MARQUEE_ENVIRONMENT}")


def check_data_dir() -> CheckResult:
    data_dir = settings.DATA_DIR
    if not data_dir.is_dir():
        return CheckResult("Data directory", "FAIL", f"{data_dir} does not exist")
    if not os.access(data_dir, os.W_OK | os.X_OK):
        return CheckResult("Data directory", "FAIL", f"{data_dir} is not writable")
    return CheckResult("Data directory", "PASS", f"{data_dir} exists and is writable")


def _aesthetic_head_present() -> bool:
    path = Path(pipeline_settings.AESTHETIC_MODEL_PATH)
    return path.is_file() or path.with_suffix(".npz").is_file()


def check_core_models() -> CheckResult:
    present = {
        "CLIP": Path(pipeline_settings.CLIP_MODEL_PATH).is_file(),
        "aesthetic head": _aesthetic_head_present(),
        "face detector": Path(pipeline_settings.FACE_MODEL_PATH).is_file(),
    }
    missing = [name for name, ok in present.items() if not ok]
    if missing:
        return CheckResult(
            "Core models", "FAIL", f"missing under {settings.models_dir}: {', '.join(missing)}"
        )
    return CheckResult("Core models", "PASS", "CLIP, aesthetic head, face detector present")


def check_style_models() -> CheckResult:
    present = {
        "DINOv2": Path(pipeline_settings.DINO_MODEL_PATH).is_file(),
        "YOLO person": Path(pipeline_settings.PERSON_MODEL_PATH).is_file(),
        "zero-shot axes": Path(pipeline_settings.ZEROSHOT_AXES_PATH).is_file(),
    }
    missing = [name for name, ok in present.items() if not ok]
    if missing:
        return CheckResult(
            "Style models",
            "WARN",
            f"missing: {', '.join(missing)}; build them with `marquee models fetch`",
        )
    return CheckResult("Style models", "PASS", "DINOv2, YOLO person, zero-shot axes present")


def check_model_manifest() -> CheckResult:
    from marquee.ml import model_manifest

    path = settings.model_manifest_path
    try:
        manifest = model_manifest.load_manifest(path)
    except model_manifest.ManifestError as exc:
        return CheckResult("Model manifest", "FAIL", _error_text(exc))
    if manifest is None:
        return CheckResult(
            "Model manifest", "WARN", f"no {path.name}; write it with `marquee models fetch`"
        )
    problems = model_manifest.verify(manifest, settings.DATA_DIR)
    if problems:
        listed = "; ".join(f"{problem.path}: {problem.reason}" for problem in problems)
        return CheckResult("Model manifest", "FAIL", listed)
    return CheckResult(
        "Model manifest", "PASS", f"{len(manifest.entries)} entries match their checksums"
    )


def check_taste_profile() -> CheckResult:
    from marquee.ml.taste_store import NumpyTasteStore

    path = Path(pipeline_settings.TASTE_PROFILE_PATH)
    if not path.is_file():
        return CheckResult(
            "Taste profile", "WARN", f"no {path.name}; build it with `marquee taste seed`"
        )
    try:
        store = NumpyTasteStore(path, expected_model_name=pipeline_settings.AI_MODEL)
        exemplars = store.size
        dino_model = store.dino_model_name if store.has_dino else None
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        return CheckResult("Taste profile", "FAIL", f"cannot load {path.name}: {_error_text(exc)}")
    expected_dino = Path(pipeline_settings.DINO_MODEL_PATH).stem
    if dino_model is not None and dino_model != expected_dino:
        return CheckResult(
            "Taste profile",
            "FAIL",
            f"DINOv2 embeddings were built with {dino_model}, not {expected_dino}; "
            "rebuild with `marquee taste seed --reuse`",
        )
    dino = f"DINOv2 embeddings ({dino_model})" if dino_model else "no DINOv2 embeddings"
    return CheckResult(
        "Taste profile", "PASS", f"{exemplars} exemplars, {dino}; {pipeline_settings.AI_MODEL}"
    )


def check_onnx_runtime() -> CheckResult:
    try:
        import numpy as np
        import onnxruntime as ort

        from marquee.ml import hardware
        from marquee.ml.embedding import CLIPImageEncoder

        ort.set_default_logger_severity(3)
        requested = hardware.resolve_provider_request(pipeline_settings.EXECUTION_PROVIDER)
        session = hardware.create_onnx_session(pipeline_settings.CLIP_MODEL_PATH)
        active = session.get_providers()[0]
        # Through the engine's own entry point, which loops when the export has a
        # fixed batch size of one.
        encoder = CLIPImageEncoder(pipeline_settings.CLIP_MODEL_PATH, session=session)
        rng = np.random.default_rng(0)
        images = [
            rng.standard_normal((1, 3, 224, 224)).astype(np.float32)
            for _ in range(CLIP_PROBE_BATCH)
        ]
        encoder.encode_batch(images)  # warm-up
        start = time.perf_counter()
        vectors = encoder.encode_batch(images)
        elapsed_ms = (time.perf_counter() - start) * 1000
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        return CheckResult("ONNX Runtime", "FAIL", f"cannot run CLIP: {_error_text(exc)}")

    if vectors.shape != (CLIP_PROBE_BATCH, 512) or not np.all(np.isfinite(vectors)):
        return CheckResult("ONNX Runtime", "FAIL", f"CLIP returned shape {vectors.shape}")
    ran = f"CLIP batch of {CLIP_PROBE_BATCH} in {elapsed_ms:.1f} ms; onnxruntime {ort.__version__}"
    if active in ("CUDAExecutionProvider", "TensorrtExecutionProvider"):
        return CheckResult("ONNX Runtime", "PASS", f"{active} active; {ran}")
    if requested is None:
        return CheckResult(
            "ONNX Runtime",
            "WARN",
            f"fell back to {active} with EXECUTION_PROVIDER=auto; {ran}",
        )
    if requested == active:
        return CheckResult("ONNX Runtime", "PASS", f"{active} active as requested; {ran}")
    return CheckResult(
        "ONNX Runtime", "FAIL", f"{requested} requested but {active} is active; {ran}"
    )


def _nvidia_gpus() -> list[dict[str, object]]:
    from marquee.host_metrics import gpu_inventory

    return gpu_inventory()


def check_gpu() -> CheckResult:
    gpus = _nvidia_gpus()
    if not gpus:
        return CheckResult("GPU", "WARN", "NVML unavailable or no NVIDIA GPU visible")
    described = ", ".join(
        f"{gpu['name']} ({int(gpu['vram_total']) / 1024**3:.1f} GiB, "
        f"{int(gpu['vram_free']) / 1024**3:.1f} GiB free)"
        for gpu in gpus
    )
    return CheckResult("GPU", "PASS", described)


def check_ocr() -> CheckResult:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # ocr_filter imports marquee.ml.hardware, which points PaddleX at the data
            # directory's model folder; that must happen before paddleocr is imported.
            from marquee.pipeline import ocr_filter

            cuda_ok, diagnostic = ocr_filter._paddle_cuda_probe(allow_import=True)
            device = ocr_filter._resolve_ocr_device(
                paddle_cuda_available=cuda_ok, cuda_diagnostic=diagnostic
            )
            import paddle
            import paddleocr

            # Importing paddleocr sets up PaddleX's own logger, which narrates every
            # model load; only its errors belong in the doctor's output.
            logging.getLogger("paddlex").setLevel(logging.ERROR)
            ocr_filter._load_ocr()
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        return CheckResult("OCR", "FAIL", f"PaddleOCR cannot load: {_error_text(exc)}")

    versions = f"paddlepaddle {paddle.__version__}, paddleocr {paddleocr.__version__}"
    if device == "cpu" and _nvidia_gpus():
        reason = "OCR_DEVICE=cpu" if pipeline_settings.OCR_DEVICE == "cpu" else diagnostic
        return CheckResult(
            "OCR", "WARN", f"PaddleOCR loaded on CPU on a CUDA host ({reason}); {versions}"
        )
    return CheckResult("OCR", "PASS", f"PaddleOCR loaded on {device}; {versions}")


def check_database() -> CheckResult:
    target = (
        f"{settings.POSTGRES_USER}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}"
        f"/{settings.POSTGRES_DB}"
    )
    try:
        import psycopg

        with psycopg.connect(
            settings.database_url_sync, connect_timeout=int(NETWORK_TIMEOUT_S)
        ) as conn:
            (one,) = conn.execute("select 1").fetchone()
            server = conn.info.parameter_status("server_version")
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        return CheckResult("Database", "FAIL", f"{target}: {_error_text(exc)}")
    if one != 1:
        return CheckResult("Database", "FAIL", f"{target}: select 1 returned {one!r}")
    return CheckResult("Database", "PASS", f"{target}; select 1 ok; PostgreSQL {server}")


def _run_with_timeout[T](func: Callable[[], T], timeout: float) -> T:
    """Run ``func`` in a daemon thread so a hung network mount cannot hang doctor."""
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            outcome["value"] = func()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"no answer after {timeout:.0f} s")
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome["value"]  # type: ignore[return-value]


def _subdirectories(path: Path) -> list[Path]:
    """Visible subdirectories; hidden ones (such as app state folders) are not titles."""
    with os.scandir(path) as entries:
        return sorted(
            Path(entry.path)
            for entry in entries
            if entry.is_dir() and not entry.name.startswith(".")
        )


def _describe_root(root: Path) -> tuple[Status, str]:
    if not root.exists():
        return "FAIL", f"{root}: missing (is the share mounted?)"
    if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
        return "FAIL", f"{root}: not a readable directory"
    groups = _subdirectories(root)
    if not groups:
        return "FAIL", f"{root}: empty (is the share mounted?)"
    counts = {group.name: len(_subdirectories(group)) for group in groups}
    summary = ", ".join(f"{name} {count}" for name, count in counts.items())
    detail = f"{root}: {summary} ({sum(counts.values())} folders)"
    if not os.access(root, os.W_OK):
        return "WARN", f"{detail}; readable but not writable"
    return "PASS", detail


def check_media_roots() -> CheckResult:
    roots = settings.MOVIE_ROOTS
    if not roots:
        return CheckResult("Media roots", "FAIL", "MOVIE_ROOTS is unset")
    statuses: list[Status] = []
    details: list[str] = []
    for root in roots:
        try:
            status, detail = _run_with_timeout(
                lambda root=root: _describe_root(root), MEDIA_LIST_TIMEOUT_S
            )
        except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
            status, detail = "FAIL", f"{root}: {_error_text(exc)} (share unreachable?)"
        statuses.append(status)
        details.append(detail)
    worst: Status = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    return CheckResult("Media roots", worst, "; ".join(details))


def _http_check(
    name: str,
    configured: bool,
    request: Callable[[httpx.Client], httpx.Response],
    describe: Callable[[httpx.Response], str],
) -> CheckResult:
    if not configured:
        return CheckResult(name, "WARN", "not configured")
    try:
        with httpx.Client(timeout=NETWORK_TIMEOUT_S, follow_redirects=False) as client:
            response = request(client)
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        return CheckResult(name, "FAIL", f"unreachable: {_error_text(exc)}")
    if response.status_code in (401, 403):
        return CheckResult(name, "FAIL", f"refused: HTTP {response.status_code}")
    if response.status_code != 200:
        return CheckResult(name, "FAIL", f"unexpected HTTP {response.status_code}")
    return CheckResult(name, "PASS", describe(response))


def check_tmdb() -> CheckResult:
    token = settings.TMDB_READ_ACCESS_TOKEN
    return _http_check(
        "TMDB",
        token is not None,
        lambda client: client.get(
            "https://api.themoviedb.org/3/authentication",
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},  # type: ignore[union-attr]
        ),
        lambda _response: "authenticated",
    )


def _arr_check(name: str, url: str | None, api_key: object) -> CheckResult:
    def request(client: httpx.Client) -> httpx.Response:
        return client.get(
            f"{url.rstrip('/')}/api/v3/system/status",  # type: ignore[union-attr]
            headers={"X-Api-Key": api_key.get_secret_value()},  # type: ignore[attr-defined]
        )

    def describe(response: httpx.Response) -> str:
        version = response.json().get("version", "unknown")
        return f"authenticated; {name} {version}"

    return _http_check(name, bool(url) and api_key is not None, request, describe)


def check_radarr() -> CheckResult:
    return _arr_check("Radarr", settings.RADARR_URL, settings.RADARR_API_KEY)


CHECKS: tuple[Callable[[], CheckResult], ...] = (
    check_python,
    check_settings,
    check_data_dir,
    check_core_models,
    check_style_models,
    check_model_manifest,
    check_taste_profile,
    check_onnx_runtime,
    check_gpu,
    check_ocr,
    check_database,
    check_media_roots,
    check_tmdb,
    check_radarr,
)


def run_checks() -> list[CheckResult]:
    results = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # noqa: BLE001 - one broken check must not hide the rest
            results.append(
                CheckResult(check.__name__, "FAIL", f"check crashed: {_error_text(exc)}")
            )
    return results


def render_table(results: list[CheckResult]) -> str:
    width = max(len(result.name) for result in results)
    lines = [f"{'Check'.ljust(width)}  Status  Detail", f"{'-' * width}  ------  ------"]
    lines += [f"{r.name.ljust(width)}  {r.status:<6}  {r.detail}" for r in results]
    return "\n".join(lines)


def main(*, as_json: bool = False) -> int:
    """Run every check, print the report, and return the process exit code."""
    results = run_checks()
    if as_json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        print(render_table(results))
    return 1 if any(result.status == "FAIL" for result in results) else 0
