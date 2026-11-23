"""Hardware detection and execution-provider selection for every deployment tier.

One module decides how the ONNX models run on the current host, covering the
whole homelab spectrum with automatic fallthrough and zero per-platform code:

  - NVIDIA GPUs (RTX 3070 and up)      -> CUDAExecutionProvider
  - Intel iGPU / Arc dGPU (A310, UHD)  -> OpenVINOExecutionProvider (GPU device)
  - Intel hosts without a usable GPU   -> OpenVINOExecutionProvider (CPU device)
  - Apple Silicon (M-series)           -> CoreMLExecutionProvider
  - Anything else (Intel N150, ...)    -> CPUExecutionProvider

Selection never raises on a missing provider in "auto" mode — it falls through
to the next tier, ending at CPU which is always available.  The resolved
profile also carries tier-appropriate performance defaults (CLIP batch size,
OCR worker count) used wherever the config leaves them on 0/auto.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import onnxruntime as ort

from marquee.core.pipeline_config import pipeline_settings

logger = logging.getLogger(__name__)

# PaddlePaddle's default GPU allocator pre-reserves a large fraction of the
# card (observed: 7.65 GB reserved while only 93 MB in use on an 8 GB 3070),
# which starves a second Paddle process — e.g. the OCR workers when a taste
# rebuild is also resident. ``auto_growth`` makes Paddle allocate on demand
# instead, so jobs coexist on a small card. Must be set before paddle is
# imported anywhere; this module is imported early (via embedding/hardware)
# and re-imported in spawned OCR workers, so it lands ahead of every import.
os.environ.setdefault("FLAGS_allocator_strategy", "auto_growth")

# Friendly aliases accepted in EXECUTION_PROVIDER, beyond raw ORT names.
_PROVIDER_ALIASES: dict[str, str] = {
    "cuda": "CUDAExecutionProvider",
    "nvidia": "CUDAExecutionProvider",
    "tensorrt": "TensorrtExecutionProvider",
    "openvino": "OpenVINOExecutionProvider",
    "intel": "OpenVINOExecutionProvider",
    "openvino-cpu": "OpenVINOExecutionProvider",
    "coreml": "CoreMLExecutionProvider",
    "apple": "CoreMLExecutionProvider",
    "cpu": "CPUExecutionProvider",
}

# Auto-detection priority. TensorRT is deliberately excluded from auto mode
# (engine build times are minutes-long); request it explicitly if wanted.
_AUTO_PRIORITY = (
    "CUDAExecutionProvider",
    "OpenVINOExecutionProvider",
    "CoreMLExecutionProvider",
    "CPUExecutionProvider",
)

_TIER_BY_PROVIDER = {
    "TensorrtExecutionProvider": "cuda",
    "CUDAExecutionProvider": "cuda",
    "CoreMLExecutionProvider": "coreml",
    "CPUExecutionProvider": "cpu",
}

# CLIP images per ONNX run when CLIP_BATCH_SIZE is 0/auto.
_CLIP_BATCH_BY_TIER = {
    "cuda": 32,
    "openvino-gpu": 16,
    "openvino-cpu": 8,
    "coreml": 16,
    "cpu": 8,
}


@dataclass(frozen=True)
class HardwareProfile:
    """The resolved execution plan plus tier-appropriate perf defaults."""

    tier: str  # "cuda" | "openvino-gpu" | "openvino-cpu" | "coreml" | "cpu"
    providers: list[str]
    provider_options: list[dict[str, str]] = field(default_factory=list)
    clip_batch_size: int = 8
    ocr_workers: int = 1
    ocr_omp_threads: int = 1

    def session_providers(self) -> list[str | tuple[str, dict[str, str]]]:
        """Providers in the (name, options) form InferenceSession accepts."""
        merged: list[str | tuple[str, dict[str, str]]] = []
        for name, options in zip(self.providers, self.provider_options, strict=True):
            merged.append((name, options) if options else name)
        return merged


def _openvino_has_gpu() -> bool:
    """Check whether OpenVINO can see an Intel GPU (iGPU or Arc dGPU)."""
    try:
        from openvino import Core  # bundled with onnxruntime-openvino

        return any(d.startswith("GPU") for d in Core().available_devices)
    except Exception:  # noqa: BLE001 - any failure means "no usable GPU"
        return False


@lru_cache(maxsize=1)
def _preload_cuda_libraries() -> bool:
    """Load CUDA runtime libs from pip-installed nvidia packages (Linux).

    onnxruntime-gpu does not bundle CUDA; it dlopen()s libcublasLt/libcudnn
    at session creation and fails if they are not on the loader path. The
    ``nvidia-*-cu12`` pip packages (pulled in by torch, or installable
    standalone) ship those libs inside site-packages where the loader never
    looks. Loading them here with RTLD_GLOBAL makes the CUDA provider work
    with a plain ``pip install`` — no LD_LIBRARY_PATH gymnastics required.
    """
    import ctypes

    try:
        import nvidia  # namespace package created by any nvidia-*-cu12 wheel
    except ImportError:
        return False

    loaded_any = False
    lib_globs = (
        "cuda_runtime/lib/libcudart.so.*",
        "cublas/lib/libcublasLt.so.*",
        "cublas/lib/libcublas.so.*",
        "cudnn/lib/libcudnn.so.*",
        "cufft/lib/libcufft.so.*",
        "curand/lib/libcurand.so.*",
    )
    for package_root in nvidia.__path__:
        for pattern in lib_globs:
            for lib_path in sorted(Path(package_root).glob(pattern)):
                try:
                    ctypes.CDLL(str(lib_path), mode=ctypes.RTLD_GLOBAL)
                    loaded_any = True
                except OSError:  # missing transitive dep — skip, ORT will say so
                    logger.debug("Could not preload %s", lib_path)
    if loaded_any:
        logger.info("Preloaded CUDA libraries from pip-installed nvidia packages")
    return loaded_any


def _auto_ocr_workers(tier: str, cpu_count: int) -> tuple[int, int]:
    """Return (workers, omp_threads_per_worker) for the PaddleOCR pool.

    Worker processes each run intra-op threaded Paddle kernels, so the pool
    must not oversubscribe the cores: a fixed worker count thrashes an Intel
    N150 (4 cores) and underuses a 13600KF (20 threads).
    """
    # On CUDA hosts GPU paddle replicates model weights into VRAM per worker,
    # so the pool is capped lower; if paddle turns out to be CPU-only there,
    # the smaller pool with more threads per worker is still a fast default.
    workers = max(1, min(3, cpu_count // 4)) if tier == "cuda" else max(1, min(6, cpu_count // 2))
    omp_threads = max(1, cpu_count // workers)
    return workers, omp_threads


def resolve_provider_request(requested: str) -> str | None:
    """Map a friendly alias or raw name to an ORT provider name.

    Returns None for "auto"/empty.
    """
    if not requested or requested.lower() == "auto":
        return None
    return _PROVIDER_ALIASES.get(requested.lower(), requested)


@lru_cache(maxsize=8)
def detect_hardware(override: str | None = None) -> HardwareProfile:
    """Resolve the execution plan for this host.

    Args:
        override: provider request (alias or raw ORT name). None/"auto" picks
            the best available tier. Unavailable explicit requests raise;
            auto mode never raises.
    """
    available = ort.get_available_providers()
    requested = resolve_provider_request(
        override if override is not None else pipeline_settings.EXECUTION_PROVIDER
    )
    cpu_count = os.cpu_count() or 4

    if requested is not None:
        if requested not in available:
            raise RuntimeError(
                f"Requested ONNX execution provider {requested!r} is unavailable; "
                f"available providers: {available}"
            )
        chain = [requested]
        if requested != "CPUExecutionProvider":
            chain.append("CPUExecutionProvider")
    else:
        chain = [name for name in _AUTO_PRIORITY if name in available]
        if not chain:  # pathological build without even a CPU EP
            chain = ["CPUExecutionProvider"]

    primary = chain[0]
    options: list[dict[str, str]] = [{} for _ in chain]

    if primary in ("CUDAExecutionProvider", "TensorrtExecutionProvider"):
        _preload_cuda_libraries()

    if primary == "OpenVINOExecutionProvider":
        # "openvino-cpu" alias forces the CPU device even when a GPU exists
        # (useful when the iGPU is busy with Plex/QSV transcodes).
        force_cpu = (override or pipeline_settings.EXECUTION_PROVIDER).lower() == "openvino-cpu"
        use_gpu = not force_cpu and _openvino_has_gpu()
        options[0] = {"device_type": "GPU" if use_gpu else "CPU"}
        tier = "openvino-gpu" if use_gpu else "openvino-cpu"
    else:
        tier = _TIER_BY_PROVIDER.get(primary, "cpu")

    workers, omp_threads = _auto_ocr_workers(tier, cpu_count)
    profile = HardwareProfile(
        tier=tier,
        providers=chain,
        provider_options=options,
        clip_batch_size=_CLIP_BATCH_BY_TIER.get(tier, 8),
        ocr_workers=workers,
        ocr_omp_threads=omp_threads,
    )
    logger.info(
        "Hardware profile: tier=%s providers=%s clip_batch=%d ocr_workers=%d",
        profile.tier,
        profile.providers,
        profile.clip_batch_size,
        profile.ocr_workers,
    )
    return profile


def choose_execution_providers(override: str | None = None) -> list[str]:
    """Provider names only — kept for callers that don't need options."""
    return list(detect_hardware(override).providers)


def create_onnx_session(
    model_path: str | Path,
    *,
    execution_provider: str | None = None,
) -> ort.InferenceSession:
    """Create an InferenceSession on the resolved hardware tier.

    Used for every ONNX model in the pipeline (CLIP, face detector) so all of
    them land on the same device with the same fallback behaviour.
    """
    path = Path(model_path)
    if not path.exists():
        raise FileNotFoundError(f"ONNX model not found: {path}")
    profile = detect_hardware(execution_provider)
    session = ort.InferenceSession(
        str(path),
        providers=profile.session_providers(),
    )
    logger.info("ONNX session for %s: providers=%s", path.name, session.get_providers())
    return session


def effective_clip_batch_size() -> int:
    """CLIP_BATCH_SIZE from config, or the hardware tier default when 0."""
    configured = pipeline_settings.CLIP_BATCH_SIZE
    if configured > 0:
        return configured
    return detect_hardware().clip_batch_size


def effective_ocr_workers() -> int:
    """OCR_WORKERS from config, or the hardware tier default when 0."""
    configured = pipeline_settings.OCR_WORKERS
    if configured > 0:
        return min(configured, 16)  # sane upper bound to guard against typos
    return detect_hardware().ocr_workers


def effective_ocr_omp_threads(workers: int) -> int:
    """OMP threads per OCR worker so workers x threads ~= core count."""
    cpu_count = os.cpu_count() or 4
    return max(1, cpu_count // max(workers, 1))
