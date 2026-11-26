"""`marquee doctor`: report whether this machine can run Marquee.

Every check is read-only. Network checks time out after five seconds, media roots are
inspected through the mount's options, the folder's permission bits, and directory
listings only (never by writing to them), and no secret is ever printed: settings are
reported by name and by whether they are set.

What is checked depends on the role (``MARQUEE_ROLE``, or ``--role``): the ``api`` service
runs no GPU work, so the engine's checks are left out, and a read-only library is right for
it; the ``worker`` must be able to write posters. In production (``MARQUEE_ENVIRONMENT`` is
``production``) a fall-back to the CPU, a missing GPU, and a library the worker cannot
write are failures, not warnings.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx

from marquee.config import pipeline_settings, settings
from marquee.config.app import ENV_FILE

Status = Literal["PASS", "WARN", "FAIL"]
Role = Literal["all", "api", "worker"]
ROLES: tuple[Role, ...] = ("all", "api", "worker")

NETWORK_TIMEOUT_S = 5.0
# Media roots live on a network share that mounts on demand; the first listing can be
# slow, and a dead share can hang. Give up and say so rather than wait forever.
MEDIA_LIST_TIMEOUT_S = 15.0
CLIP_PROBE_BATCH = 8
# Past this share of its limit the CUDA driver will soon evict kernels it compiled.
CUDA_CACHE_NEAR_LIMIT = 0.9
MOUNTINFO = Path("/proc/self/mountinfo")


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str


def _scrub(text: str) -> str:
    """``text`` with every configured secret (passwords, tokens, keys) blanked out."""
    for value in settings.secret_values():
        text = text.replace(value, "***")
    return text


def _error_text(exc: BaseException) -> str:
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return _scrub(f"{type(exc).__name__}: {message}" if message else type(exc).__name__)


def _on_cpu_status() -> Status:
    """Engine work on the CPU: a warning in development, a failure in production, where
    Marquee runs on an NVIDIA GPU only."""
    return "FAIL" if settings.production else "WARN"


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
    "API_KEY",
)


def check_settings(role: Role = "all") -> CheckResult:
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
    return CheckResult(
        "Settings",
        "PASS",
        f"{source}; environment {settings.MARQUEE_ENVIRONMENT}, role {role}",
    )


def check_data_dir() -> CheckResult:
    data_dir = settings.DATA_DIR
    if not data_dir.is_dir():
        return CheckResult("Data directory", "FAIL", f"{data_dir} does not exist")
    if not os.access(data_dir, os.W_OK | os.X_OK):
        return CheckResult("Data directory", "FAIL", f"{data_dir} is not writable")
    return CheckResult("Data directory", "PASS", f"{data_dir} exists and is writable")


def _size(n: int) -> str:
    for unit in ("bytes", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    raise AssertionError


def check_backups() -> CheckResult:
    """Where a deploy keeps the posters it replaced: writable, and what it holds."""
    from marquee.artwork import backup

    name = "Backups"
    backups = settings.backups_dir
    if not backups.exists():
        parent = backups.parent
        if parent.is_dir() and os.access(parent, os.W_OK | os.X_OK):
            return CheckResult(
                name, "PASS", f"{backups} does not exist yet; the first deploy creates it"
            )
        return CheckResult(name, "FAIL", f"{backups} does not exist and cannot be created")
    if not backups.is_dir() or not os.access(backups, os.W_OK | os.X_OK):
        return CheckResult(name, "FAIL", f"{backups} is not a writable folder")
    count, size = backup.summary(backups)
    return CheckResult(
        name, "PASS", f"{backups} is writable; {count} poster backup(s), {_size(size)}"
    )


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
    summary = f"{exemplars} exemplars, {dino}; {pipeline_settings.AI_MODEL}"
    registered = _registered_profile()
    if registered is None:  # nothing registered yet, or no database to ask
        return CheckResult("Taste profile", "PASS", summary)
    try:
        from marquee.taste.rebuild import file_sha256

        sha = file_sha256(path)
    except OSError as exc:
        return CheckResult("Taste profile", "FAIL", f"cannot read {path.name}: {_error_text(exc)}")
    active_id, active_sha = registered
    if active_sha is None:
        return CheckResult(
            "Taste profile",
            "WARN",
            f"{summary}; no registered build is active (`marquee taste rebuild`)",
        )
    if sha != active_sha:
        return CheckResult(
            "Taste profile",
            "WARN",
            f"{path.name} is not the registered build (profile {active_id}, "
            f"{active_sha[:12]}); it is {sha[:12]}. `marquee taste rebuild` publishes it again",
        )
    return CheckResult("Taste profile", "PASS", f"{summary}; registered build {active_id}")


def _registered_profile() -> tuple[int | None, str | None] | None:
    """The active registered profile's id and hash, ``(None, None)`` when profiles are
    registered but none is active, and ``None`` when none is registered or the database
    cannot say."""
    try:
        import psycopg

        with psycopg.connect(
            settings.database_url_sync, connect_timeout=int(NETWORK_TIMEOUT_S)
        ) as conn:
            (present,) = conn.execute("select to_regclass('taste_profiles')").fetchone()
            if not present:
                return None
            (rows,) = conn.execute("select count(*) from taste_profiles").fetchone()
            if not rows:
                return None
            active = conn.execute(
                "select id, sha256 from taste_profiles where is_active"
            ).fetchone()
    except Exception:  # noqa: BLE001 - doctor reports, never raises; the Database line says why
        return None
    return (int(active[0]), str(active[1])) if active else (None, None)


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
    status, detail = _provider_verdict(requested, active)
    return CheckResult("ONNX Runtime", status, f"{detail}; {ran}")


def _provider_verdict(requested: str | None, active: str) -> tuple[Status, str]:
    """What ONNX Runtime running on ``active`` means, when ``requested`` was asked for
    (``None``: EXECUTION_PROVIDER=auto)."""
    if active in ("CUDAExecutionProvider", "TensorrtExecutionProvider"):
        return "PASS", f"{active} active"
    if settings.production:
        return "FAIL", f"{active} is active, not CUDA; in production inference runs on the GPU"
    if requested is None:
        return _on_cpu_status(), f"fell back to {active} with EXECUTION_PROVIDER=auto"
    if requested == active:
        return "PASS", f"{active} active as requested"
    return "FAIL", f"{requested} requested but {active} is active"


def _nvidia_gpus() -> list[dict[str, object]]:
    from marquee.host_metrics import gpu_inventory

    return gpu_inventory()


def check_gpu() -> CheckResult:
    gpus = _nvidia_gpus()
    if not gpus:
        needed = "; production needs one" if settings.production else ""
        return CheckResult(
            "GPU", _on_cpu_status(), f"NVML unavailable or no NVIDIA GPU visible{needed}"
        )
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
    reason = "OCR_DEVICE=cpu" if pipeline_settings.OCR_DEVICE == "cpu" else diagnostic
    status, detail = _ocr_verdict(device, bool(_nvidia_gpus()), reason)
    return CheckResult("OCR", status, f"{detail}; {versions}")


def _ocr_verdict(device: str, gpu_visible: bool, reason: str | None) -> tuple[Status, str]:
    """What PaddleOCR loading on ``device`` means; ``reason`` says why it is the CPU."""
    if device != "cpu":
        return "PASS", f"PaddleOCR loaded on {device}"
    why = f" ({reason})" if reason else ""
    if settings.production:
        return "FAIL", f"PaddleOCR loaded on CPU{why}; in production OCR runs on the GPU"
    if gpu_visible:
        return _on_cpu_status(), f"PaddleOCR loaded on CPU on a CUDA host{why}"
    return "PASS", "PaddleOCR loaded on cpu"


def check_backup_tools(role: Role = "all") -> CheckResult:
    """pg_dump, pg_restore, and psql, the server's major version or newer. Missing or old
    tools fail the worker in production: it makes the scheduled database dump."""
    from marquee.maintenance import dumps

    name = "Backup tools"
    missing: Status = "FAIL" if settings.production and role == "worker" else "WARN"
    try:
        server: int | None = dumps.database_state().server_major
    except dumps.DumpError:
        server = None
    try:
        tools = dumps.find_tools(server)
    except dumps.DumpToolsError as exc:
        return CheckResult(name, missing, _scrub(str(exc)))
    found = f"pg_dump, pg_restore, psql {tools.major}"
    if server is None:
        return CheckResult(
            name, "WARN", f"{found}; the database does not answer, so its version is unknown"
        )
    return CheckResult(name, "PASS", f"{found}; the database server is {server}")


def _age(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    return f"{hours:.1f} h" if hours < 48 else f"{hours / 24:.1f} days"


def check_db_dumps(now: datetime | None = None) -> CheckResult:
    """The newest database dump and its age, against DB_BACKUP_INTERVAL_HOURS."""
    from marquee.maintenance import dumps

    name = "Database dumps"
    interval = settings.DB_BACKUP_INTERVAL_HOURS
    folder = settings.db_backups_dir
    try:
        found = dumps.list_dumps()
    except OSError as exc:
        return CheckResult(name, "WARN", f"cannot list {folder}: {_error_text(exc)}")
    off = "the scheduled dump is off (DB_BACKUP_INTERVAL_HOURS=0)"
    if not found:
        if interval <= 0:
            return CheckResult(name, "WARN", f"none in {folder}, and {off}")
        return CheckResult(
            name, "WARN", f"none yet in {folder}; the worker makes one when it starts"
        )
    newest = found[0]
    now = now or datetime.now(UTC)
    detail = (
        f"newest {newest.name}, {_size(newest.size_bytes)}, {_age(now - newest.made_at)} old; "
        f"{len(found)} dump(s) in {folder}"
    )
    if interval <= 0:
        return CheckResult(name, "WARN", f"{detail}; {off}")
    if dumps.stale(newest, interval, now):
        return CheckResult(
            name,
            "WARN",
            f"{detail}; older than two intervals ({2 * interval} h): is the backup job failing?",
        )
    return CheckResult(name, "PASS", f"{detail}; one every {interval} h")


def _tree_size(path: Path) -> int:
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except FileNotFoundError:  # evicted while we walked
                continue
    return total


def check_cuda_kernel_cache() -> CheckResult:
    """The kernels the CUDA driver compiled for this card, against the driver's limit."""
    name = "CUDA kernel cache"
    cache = settings.cuda_kernel_cache_dir
    limit = settings.cuda_kernel_cache_limit
    if not cache.is_dir():
        return CheckResult(
            name,
            "PASS",
            f"{cache} is empty; the first GPU work compiles kernels for this card, "
            "which makes the first run about four times slower",
        )
    try:
        size = _tree_size(cache)
    except OSError as exc:
        return CheckResult(name, "WARN", f"cannot measure {cache}: {_error_text(exc)}")
    used = f"{cache}: {_size(size)} of a {_size(limit)} limit"
    if size >= limit * CUDA_CACHE_NEAR_LIMIT:
        return CheckResult(
            name,
            "WARN",
            f"{used}; past the limit the driver evicts kernels and runs slow down while it "
            "compiles them again (CUDA_CACHE_MAXSIZE raises it, to 4 GiB at most)",
        )
    return CheckResult(name, "PASS", used)


def check_database() -> CheckResult:
    target = (
        f"{settings.POSTGRES_USER}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}"
        f"/{settings.POSTGRES_DB}"
    )
    try:
        import psycopg

        from marquee.db import migrate

        head = migrate.head_revision()
        with psycopg.connect(
            settings.database_url_sync, connect_timeout=int(NETWORK_TIMEOUT_S)
        ) as conn:
            (one,) = conn.execute("select 1").fetchone()
            server = conn.info.parameter_status("server_version")
            (versioned,) = conn.execute("select to_regclass('alembic_version')").fetchone()
            revision = (
                conn.execute("select version_num from alembic_version").fetchone()
                if versioned
                else None
            )
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        return CheckResult("Database", "FAIL", f"{target}: {_error_text(exc)}")
    if one != 1:
        return CheckResult("Database", "FAIL", f"{target}: select 1 returned {one!r}")
    current = revision[0] if revision else None
    reached = f"{target}; select 1 ok; PostgreSQL {server}"
    if current != head:
        return CheckResult(
            "Database",
            "WARN",
            f"{reached}; schema at {current or 'no revision'}, newest is {head}; "
            "run `marquee db upgrade`",
        )
    return CheckResult("Database", "PASS", f"{reached}; schema at {current} (newest)")


def check_worker() -> CheckResult:
    """Whether a worker holds the single-worker lock, and what the queue holds."""
    import asyncio

    from sqlalchemy.exc import ProgrammingError

    from marquee.db.engine import create_engine, create_session_factory
    from marquee.jobs.queries import WorkerStatus, worker_status

    async def read() -> WorkerStatus:
        engine = create_engine()
        try:
            async with create_session_factory(engine)() as session:
                return await worker_status(session)
        finally:
            await engine.dispose()

    try:
        status = asyncio.run(read())
    except ProgrammingError:
        return CheckResult("Worker", "WARN", "no job tables; run `marquee db upgrade`")
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        return CheckResult("Worker", "FAIL", f"cannot read the queue: {_error_text(exc)}")
    queue = f"{status.queued} queued, {status.running_jobs} running, {status.failed} failed"
    if not status.running:
        return CheckResult("Worker", "WARN", f"no worker running (`marquee worker`); {queue}")
    since = (
        status.since.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if status.since else "unknown"
    )
    return CheckResult("Worker", "PASS", f"running since {since}; {queue}")


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


def _unescape_mount_path(field: str) -> str:
    """mountinfo writes a space, tab, newline, or backslash in a path as an octal escape."""
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), field)


def _mount_read_only(path: Path, mountinfo: Path = MOUNTINFO) -> bool | None:
    """Whether the mount ``path`` is on is read-only, from this process's mount table; ``None``
    when the table cannot say. Reads the table only: nothing is written to find out."""
    try:
        lines = mountinfo.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    target = os.path.realpath(path)
    found: tuple[str, bool] | None = None
    for line in lines:
        fields = line.split(" ")
        if "-" not in fields[6:] or len(fields) < 10:
            continue
        mount_point = _unescape_mount_path(fields[4])
        inside = (
            mount_point == "/"
            or target == mount_point
            or target.startswith(mount_point.rstrip("/") + "/")
        )
        if not inside:
            continue
        separator = fields.index("-", 6)
        options = set(fields[5].split(","))
        if len(fields) > separator + 3:
            options |= set(fields[separator + 3].split(","))
        # The deepest mount holds the path; of two at the same point, the later one is on top.
        if found is None or len(mount_point) >= len(found[0]):
            found = (mount_point, "ro" in options)
    return None if found is None else found[1]


def _write_blocker(root: Path) -> str | None:
    """Why this process could not write in ``root``, judged without writing: first the
    mount's options, then the folder's permission bits. ``None`` when it could."""
    if _mount_read_only(root):
        return "mounted read-only"
    if not os.access(root, os.W_OK | os.X_OK):
        return f"its permission bits do not let user {os.geteuid()} write"
    return None


def _describe_root(root: Path, role: Role = "all") -> tuple[Status, str]:
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
    blocker = _write_blocker(root)
    if blocker is None:
        return "PASS", f"{detail}; writable" if role == "worker" else detail
    if role == "api":
        return "PASS", f"{detail}; {blocker}, as the api needs (it only reads)"
    if role == "worker":
        status: Status = "FAIL" if settings.production else "WARN"
        return status, f"{detail}; not writable ({blocker}), so the worker cannot deploy"
    return "WARN", f"{detail}; readable but not writable ({blocker})"


def check_media_roots(role: Role = "all") -> CheckResult:
    roots = settings.MOVIE_ROOTS
    if not roots:
        return CheckResult("Media roots", "FAIL", "MOVIE_ROOTS is unset")
    statuses: list[Status] = []
    details: list[str] = []
    for root in roots:
        try:
            status, detail = _run_with_timeout(
                lambda root=root: _describe_root(root, role), MEDIA_LIST_TIMEOUT_S
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


def _radarr_root_folders() -> object:
    """Radarr's root folders, as Radarr reports them."""
    with httpx.Client(timeout=NETWORK_TIMEOUT_S, follow_redirects=False) as client:
        response = client.get(
            f"{settings.RADARR_URL.rstrip('/')}/api/v3/rootfolder",  # type: ignore[union-attr]
            headers={"X-Api-Key": settings.RADARR_API_KEY.get_secret_value()},  # type: ignore[union-attr]
        )
    response.raise_for_status()
    return response.json()


def check_path_mapping() -> CheckResult:
    """Every root folder Radarr reports must map, through RADARR_MOVIE_ROOTS, to a folder here."""
    from marquee.fs.paths import PathMapper, PathMappingError
    from marquee.library.sync import SyncPathError, check_root_folders

    name = "Path mapping"
    if not settings.RADARR_URL or settings.RADARR_API_KEY is None:
        return CheckResult(name, "WARN", "Radarr not configured")
    try:
        mapper = PathMapper.from_settings(settings)
    except PathMappingError as exc:
        return CheckResult(name, "FAIL", _error_text(exc))
    try:
        root_folders = _radarr_root_folders()
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        return CheckResult(name, "FAIL", f"cannot list Radarr's root folders: {_error_text(exc)}")
    try:
        checked = _run_with_timeout(
            lambda: check_root_folders(root_folders, mapper), MEDIA_LIST_TIMEOUT_S
        )
    except SyncPathError as exc:
        return CheckResult(name, "FAIL", _scrub(str(exc)))
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        return CheckResult(name, "FAIL", f"{_error_text(exc)} (share unreachable?)")
    return CheckResult(name, "PASS", f"{checked} Radarr root folders map to folders here")


CHECKS: tuple[Callable[[], CheckResult], ...] = (
    check_python,
    check_settings,
    check_data_dir,
    check_backups,
    check_core_models,
    check_style_models,
    check_model_manifest,
    check_taste_profile,
    check_onnx_runtime,
    check_gpu,
    check_ocr,
    check_cuda_kernel_cache,
    check_database,
    check_backup_tools,
    check_db_dumps,
    check_worker,
    check_media_roots,
    check_tmdb,
    check_radarr,
    check_path_mapping,
)
# The engine's checks; the api service runs no GPU work and has no GPU.
ENGINE_CHECKS = frozenset({check_onnx_runtime, check_gpu, check_ocr, check_cuda_kernel_cache})
# Checks whose answer depends on the role.
ROLE_CHECKS = frozenset({check_settings, check_media_roots, check_backup_tools})


def run_checks(role: Role | None = None) -> list[CheckResult]:
    """Every check for ``role`` (default: ``MARQUEE_ROLE``)."""
    role = role or settings.MARQUEE_ROLE
    results = []
    for check in CHECKS:
        if role == "api" and check in ENGINE_CHECKS:
            continue
        try:
            results.append(check(role) if check in ROLE_CHECKS else check())  # type: ignore[call-arg]
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


def main(*, as_json: bool = False, role: Role | None = None) -> int:
    """Run every check, print the report, and return the process exit code."""
    results = run_checks(role)
    if as_json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        print(render_table(results))
    return 1 if any(result.status == "FAIL" for result in results) else 0
