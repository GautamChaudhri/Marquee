from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from marquee.jobs.registry import (
    CLASS_LIMITS,
    GpuNotAllowedError,
    JobContext,
    JobKind,
    PermanentJobError,
    default_registry,
    free_classes,
)


async def _noop(ctx: JobContext) -> None:
    return None


def test_class_limits() -> None:
    assert dict(CLASS_LIMITS) == {"gpu": 1, "network": 4, "media_read": 2, "media_write": 1}


def test_free_classes() -> None:
    assert free_classes({}) == ["gpu", "network", "media_read", "media_write"]
    assert free_classes({"gpu": 1, "network": 3}) == ["network", "media_read", "media_write"]
    assert free_classes({"network": 4, "media_read": 2}) == ["gpu", "media_write"]


def test_a_kind_is_checked() -> None:
    JobKind("net", "network", handler=_noop, max_attempts=3, timeout_s=60)
    JobKind("gpu", "gpu", gpu_function="module:function")
    JobKind("gpu_handler", "gpu", handler=_noop)

    with pytest.raises(ValueError, match="unknown class"):
        JobKind("x", "batch", handler=_noop)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="either a gpu_function or a handler"):
        JobKind("x", "gpu", handler=_noop, gpu_function="module:function")
    with pytest.raises(ValueError, match="either a gpu_function or a handler"):
        JobKind("x", "gpu")
    with pytest.raises(ValueError, match="needs a handler"):
        JobKind("x", "network", gpu_function="module:function")
    with pytest.raises(ValueError, match="must be positive"):
        JobKind("x", "network", handler=_noop, max_attempts=0)
    with pytest.raises(ValueError, match="must be positive"):
        JobKind("x", "network", handler=_noop, timeout_s=0)


def test_the_kinds_of_this_milestone() -> None:
    kinds = default_registry()

    assert {
        name: (k.job_class, k.max_attempts, k.timeout_s, k.dedupe_key) for name, k in kinds.items()
    } == {
        "library_sync": ("network", 3, 900, "library_sync"),
        "gpu_selftest": ("gpu", 1, 300, None),
        "poster_run": ("gpu", 2, 600, "poster_run:{movie_id}"),
        "poster_deploy": ("media_write", 3, 300, "poster_write:{movie_id}"),
        "poster_restore": ("media_write", 3, 300, "poster_write:{movie_id}"),
        "taste_rebuild": ("gpu", 2, 900, "taste_rebuild:{revision}"),
    }
    assert kinds["gpu_selftest"].gpu_function == "marquee.jobs.handlers.gpu:gpu_selftest"
    assert kinds["taste_rebuild"].handler is not None
    assert kinds["poster_run"].handler is not None
    assert kinds["poster_run"].gpu_function is None


def test_a_dedupe_key_may_name_payload_fields() -> None:
    fixed = JobKind("x", "network", handler=_noop, dedupe_key="x")
    per_movie = JobKind("y", "gpu", handler=_noop, dedupe_key="y:{movie_id}")
    none = JobKind("z", "network", handler=_noop)

    assert fixed.dedupe_key_for({"movie_id": 3}) == "x"
    assert per_movie.dedupe_key_for({"movie_id": 3}) == "y:3"
    assert none.dedupe_key_for({"movie_id": 3}) is None
    with pytest.raises(ValueError, match="does not fill its dedupe key"):
        per_movie.dedupe_key_for({})


async def test_only_a_context_given_a_gpu_runner_reaches_the_child() -> None:
    calls: list[tuple[str, dict]] = []

    async def runner(function: str, payload: dict) -> dict:
        calls.append((function, payload))
        return {"ok": True}

    with_gpu = JobContext(1, {}, threading.Event(), lambda *_: None, gpu_runner=runner)
    assert await with_gpu.gpu("module:function", {"n": 1}) == {"ok": True}
    assert calls == [("module:function", {"n": 1})]

    without = JobContext(2, {}, threading.Event(), lambda *_: None)
    with pytest.raises(GpuNotAllowedError, match="not of class gpu"):
        await without.gpu("module:function", {})
    assert issubclass(GpuNotAllowedError, PermanentJobError)


def test_the_worker_imports_no_inference_library() -> None:
    """Models load only in the GPU child; the worker process must not even import them."""
    code = (
        "import sys\n"
        "from marquee.jobs.registry import default_registry\n"
        "import marquee.jobs.worker\n"
        "default_registry()\n"
        "loaded = {'onnxruntime', 'paddle', 'paddleocr', 'cv2'} & set(sys.modules)\n"
        "assert not loaded, loaded\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)
