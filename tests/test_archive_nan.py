"""Run archives must never contain NaN/Infinity — Starlette renders responses
with ``allow_nan=False``, so a single archived NaN would 500 every endpoint
that replays the archive (/api/pipeline/runs/{id}, /rescore, feedback)."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from marquee.pipeline.extractor_runtime import extractor_runtime
from marquee.pipeline.runner import write_run_json


def _strict_loads(text: str) -> dict:
    def _reject(constant: str) -> None:
        raise AssertionError(f"archive contains non-finite literal {constant!r}")

    return json.loads(text, parse_constant=_reject)


def test_write_run_json_strips_nonfinite(tmp_path):
    payload = {
        "plain_nan": float("nan"),
        "plain_inf": float("inf"),
        "np_nan": np.float64("nan"),
        "np_value": np.float32(0.5),
        "array": np.array([1.0, np.nan, -np.inf]),
        "nested": {"deep": [{"x": float("nan")}, 2.0]},
        "fine": 1.25,
        "text": "NaN",  # strings must survive untouched
    }
    path = tmp_path / "run.json"
    write_run_json(path, payload)

    data = _strict_loads(path.read_text())
    assert data["plain_nan"] is None
    assert data["plain_inf"] is None
    assert data["np_nan"] is None
    assert data["np_value"] == pytest.approx(0.5)
    assert data["array"] == [1.0, None, None]
    assert data["nested"]["deep"][0]["x"] is None
    assert data["nested"]["deep"][1] == 2.0
    assert data["fine"] == 1.25
    assert data["text"] == "NaN"


def test_load_archive_tolerates_legacy_nan_literals(tmp_path):
    legacy = tmp_path / "legacy-run.json"
    legacy.write_text('{"score": NaN, "bound": Infinity, "ok": 3.5}', encoding="utf-8")

    data = extractor_runtime.load_archive("legacy-run", archive_path=str(legacy))
    assert data is not None
    assert data["score"] is None
    assert data["bound"] is None
    assert data["ok"] == 3.5


def test_load_archive_roundtrip_has_no_nan(tmp_path):
    path = tmp_path / "roundtrip.json"
    write_run_json(path, {"candidates": [{"raw_features": {"title_area": np.nan}}]})
    data = extractor_runtime.load_archive("roundtrip", archive_path=str(path))
    assert data is not None
    value = data["candidates"][0]["raw_features"]["title_area"]
    assert value is None or math.isfinite(value)
