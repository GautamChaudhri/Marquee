from __future__ import annotations

import pytest

from marquee.jobs.registry import CLASS_LIMITS, JobContext, JobKind, free_classes


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

    with pytest.raises(ValueError, match="unknown class"):
        JobKind("x", "batch", handler=_noop)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="names only a gpu_function"):
        JobKind("x", "gpu", handler=_noop)
    with pytest.raises(ValueError, match="needs a handler"):
        JobKind("x", "network", gpu_function="module:function")
    with pytest.raises(ValueError, match="must be positive"):
        JobKind("x", "network", handler=_noop, max_attempts=0)
    with pytest.raises(ValueError, match="must be positive"):
        JobKind("x", "network", handler=_noop, timeout_s=0)
