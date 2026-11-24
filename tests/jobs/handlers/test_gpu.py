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
from marquee.pipeline.runner import ProgressEvent


@dataclass
class _Output:
    run_id: str = "e" * 32
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


# ── poster_run, in the child ──────────────────────────────────────────────


def _run_payload(**values: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_folder": "00000007-die-hard",
        "movie_id": 7,
        "title": "Die Hard",
        "tmdb_id": 562,
        "director": "John McTiernan",
        "production_companies": ["Twentieth Century Fox", "Gordon Company"],
        "tagline": "Twelve terrorists. One cop.",
    }
    payload.update(values)
    return payload


@pytest.fixture
def child_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """A taste profile and a TMDB token, and a fake engine that records what it was given."""
    from pydantic import SecretStr

    from marquee.config import pipeline_settings, settings

    profile = tmp_path / "profile.npz"
    profile.write_bytes(b"a profile")
    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_PATH", profile)
    monkeypatch.setattr(settings, "TMDB_READ_ACCESS_TOKEN", SecretStr("token"))
    seen: dict[str, Any] = {"profile": profile}

    async def fake_run(**kwargs: Any) -> _Output:
        seen.update(kwargs)
        kwargs["progress"](ProgressEvent(stage="fetch", state="end", survivors=3, elapsed_s=0.5))
        assert kwargs["should_cancel"]() is False
        return _Output()

    monkeypatch.setattr("marquee.pipeline.single_run.run_single_subject", fake_run)
    return seen


def test_poster_run_hands_the_engine_the_gate_fields_by_its_names(
    child_setup: dict[str, Any],
) -> None:
    import hashlib

    from marquee.config import settings

    progress: list[tuple] = []
    result = gpu.poster_run(_run_payload(), threading.Event(), lambda *v: progress.append(v))

    subject = child_setup["subject"]
    assert (subject.title, subject.tmdb_id, subject.movie_id) == ("Die Hard", 562, 7)
    assert child_setup["run_dir"] == settings.runs_dir / "00000007-die-hard"
    gate = child_setup["ocr_gate"]
    assert gate.director == "John McTiernan"
    assert gate.studios == ["Twentieth Century Fox", "Gordon Company"]
    assert gate.tagline == "Twelve terrorists. One cop."
    assert result["pick_file"] == "a.jpg"
    assert result["status"] == "completed"
    assert result["stages"] == [{"stage": "fetch", "survivors": 3, "seconds": 0.5}]
    assert result["profile_sha256"] == hashlib.sha256(b"a profile").hexdigest()
    assert progress == [(None, None, "fetch end")]


def test_poster_run_gives_no_studios_as_an_empty_list(child_setup: dict[str, Any]) -> None:
    # TMDB's details give [] when a movie lists no studio; the engine must see the same.
    gpu.poster_run(_run_payload(production_companies=None), threading.Event(), lambda *_: None)
    assert child_setup["ocr_gate"].studios == []


@pytest.mark.parametrize(
    "values",
    [
        {"run_folder": "../escape"},
        {"run_folder": ""},
        {"movie_id": None},
        {"tmdb_id": "562"},
        {"title": " "},
    ],
)
def test_poster_run_refuses_a_bad_payload(child_setup: dict[str, Any], values: dict) -> None:
    with pytest.raises(PermanentJobError):
        gpu.poster_run(_run_payload(**values), threading.Event(), lambda *_: None)


def test_poster_run_needs_a_taste_profile(
    child_setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from marquee.config import pipeline_settings

    monkeypatch.setattr(
        pipeline_settings, "TASTE_PROFILE_PATH", child_setup["profile"].parent / "none"
    )
    with pytest.raises(PermanentJobError, match="taste seed"):
        gpu.poster_run(_run_payload(), threading.Event(), lambda *_: None)
