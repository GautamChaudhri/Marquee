"""The gpu kinds' functions, called directly with fakes: no GPU, no model, no network."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from marquee.jobs.handlers import gpu
from marquee.jobs.registry import PermanentJobError
from marquee.library import title_run
from marquee.library.subjects import ResolvedSubject, SubjectLookupError
from marquee.pipeline.runner import ProgressEvent


@dataclass
class _Output:
    status: str = "completed"
    counts: dict[str, int] = field(default_factory=lambda: {"posters_found": 3, "ranked": 2})
    ranked: list[dict] = field(default_factory=lambda: [{}, {}])
    recommendation: dict = field(
        default_factory=lambda: {
            "rank": 1,
            "orig_filename": "a.jpg",
            "final_score": 0.9,
            "stack_label": "A",
        }
    )
    payload: dict = field(
        default_factory=lambda: {
            "diagnostic_ledger": {"candidates": [{"orig_filename": "a.jpg", "stack_rank": 1}]}
        }
    )


async def _fake_run_title(title: str, **kwargs: Any) -> title_run.TitleRun:
    kwargs["progress"](ProgressEvent(stage="ocr", state="start", total=3))
    kwargs["progress"](ProgressEvent(stage="ocr", state="end", survivors=2, elapsed_s=1.5))
    assert kwargs["should_cancel"]() is False
    return title_run.TitleRun(
        subject=ResolvedSubject(title="Die Hard", tmdb_id=562, imdb_id="tt0095016"),
        output=_Output(),  # type: ignore[arg-type]
        run_dir=Path("/runs/20261124T000000Z-die-hard"),
    )


def test_engine_probe_reports_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(title_run, "run_title", _fake_run_title)
    progress: list[tuple] = []

    result = gpu.engine_probe(
        {"title": "Die Hard"}, threading.Event(), lambda *values: progress.append(values)
    )

    assert result["run_folder"] == "20261124T000000Z-die-hard"
    assert result["subject"] == {"title": "Die Hard", "tmdb_id": 562, "imdb_id": "tt0095016"}
    assert result["counts"] == {"posters_found": 3, "ranked": 2}
    assert result["stages"] == [{"stage": "ocr", "survivors": 2, "seconds": 1.5}]
    assert result["ranked"] == 2
    assert result["pick"] == {"rank": 1, "stack": "1A", "score": 0.9, "file": "a.jpg"}
    assert progress == [(None, 3, "ocr start"), (None, None, "ocr end")]


@pytest.mark.parametrize("payload", [{}, {"title": " "}, {"title": "X", "year": "1988"}])
def test_engine_probe_refuses_a_bad_payload(payload: dict) -> None:
    with pytest.raises(PermanentJobError):
        gpu.engine_probe(payload, threading.Event(), lambda *_: None)


def test_a_title_that_leads_nowhere_is_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_such_title(title: str, **kwargs: Any) -> None:
        raise SubjectLookupError("no movie folder titled 'Die Hrd'")

    monkeypatch.setattr(title_run, "run_title", no_such_title)

    with pytest.raises(PermanentJobError, match="no movie folder"):
        gpu.engine_probe({"title": "Die Hrd"}, threading.Event(), lambda *_: None)


class _FakeSession:
    def get_providers(self) -> list[str]:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]


class _FakeEncoder:
    def __init__(self, path: object, session: object) -> None:
        self.session = session

    def encode_batch(self, images: list) -> np.ndarray:
        return np.zeros((len(images), 512), dtype=np.float32)


def test_gpu_selftest_loads_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[object] = []

    def create_session(path: object) -> _FakeSession:
        loads.append(path)
        return _FakeSession()

    monkeypatch.setattr("marquee.ml.hardware.create_onnx_session", create_session)
    monkeypatch.setattr("marquee.ml.embedding.CLIPImageEncoder", _FakeEncoder)
    monkeypatch.setattr(gpu, "_clip", None)

    first = gpu.gpu_selftest({}, threading.Event(), lambda *_: None)
    second = gpu.gpu_selftest({}, threading.Event(), lambda *_: None)

    assert len(loads) == 1
    assert (first["already_loaded"], second["already_loaded"]) == (False, True)
    assert first["provider"] == "CUDAExecutionProvider"
    assert first["batch"] == 8
