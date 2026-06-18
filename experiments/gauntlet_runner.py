#!/usr/bin/env python3
"""Compatibility wrapper for the real gauntlet runner.

The actual integration harness lives in ``experiments/gauntlet-output`` so the
root import path stays tiny for tests and callers, while the executable runner
still has the full HTTP client, API-key handling, and reporting logic.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_IMPL_PATH = Path(__file__).resolve().parent / "gauntlet-output" / "gauntlet_runner.py"
_SPEC = spec_from_file_location("marquee_gauntlet_runner_impl", _IMPL_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - hard failure
    raise ImportError(f"Unable to load gauntlet runner implementation from {_IMPL_PATH}")

_IMPL = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPL
_SPEC.loader.exec_module(_IMPL)

for _name in dir(_IMPL):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_IMPL, _name)

__all__ = [name for name in dir(_IMPL) if not name.startswith("_")]


def __getattr__(name: str):
    return getattr(_IMPL, name)


def __dir__() -> list[str]:
    return sorted(set(__all__) | {"__all__", "__doc__", "__name__", "__package__", "__spec__"})


if __name__ == "__main__":
    sys.exit(_IMPL.main())
