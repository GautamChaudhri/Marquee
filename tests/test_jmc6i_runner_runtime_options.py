"""JMC6I runtime options remain bounded across the contained runner boundary."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from marquee.core.filesystem import FilesystemBoundary, RootSpec
from marquee.core.jobs.internal_runner_host import OUTCOME_SUCCEEDED, run_internal_operation
from marquee.core.jobs.process_launcher import ProcessLauncher
from marquee.core.jobs.runner_protocol import ProtocolError, RunnerOperation, RunnerRuntimeOptions
from marquee.core.jobs.runner_runtime import poster_runner_runtime_options
from marquee.core.pipeline_config import pipeline_settings


def _launcher(tmp_path: Path) -> ProcessLauncher:
    work = tmp_path / "work"
    work.mkdir()
    boundary = FilesystemBoundary(
        {"test": RootSpec(name="test", path=tmp_path, purpose="test", access="read_write")}
    )
    return ProcessLauncher(
        worker_node="runtime-options",
        boundary=boundary,
        working_directory=boundary.classify(work),
    )


@pytest.mark.asyncio
async def test_forced_cpu_reaches_contained_runner_without_parent_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCR_DEVICE", "gpu")
    monkeypatch.setenv("OCR_WORKERS", "99")
    monkeypatch.setenv("MARQUEE_TEST_SECRET", "not-for-child")

    outcome = await run_internal_operation(
        _launcher(tmp_path),
        operation=RunnerOperation.NOOP,
        manifest={"params": {"report_runtime_options": True}},
        runtime_options=RunnerRuntimeOptions(
            ocr_device="cpu",
            ocr_workers=1,
            execution_provider="cpu",
            cuda_visible_devices="-1",
        ),
    )

    assert outcome.outcome == OUTCOME_SUCCEEDED, outcome.error
    assert outcome.summary["runtime_options"] == {
        "OCR_DEVICE": "cpu",
        "OCR_WORKERS": "1",
        "EXECUTION_PROVIDER": "cpu",
        "CUDA_VISIBLE_DEVICES": "-1",
    }


def test_snapshot_not_payload_or_parent_environment_controls_poster_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCR_DEVICE", "gpu")
    monkeypatch.setenv("OCR_WORKERS", "15")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,4")
    monkeypatch.setattr(pipeline_settings, "EXECUTION_PROVIDER", "CPUExecutionProvider")
    context = SimpleNamespace(
        configuration={"OCR_DEVICE": "cpu", "OCR_WORKERS": 2},
        payload={"OCR_DEVICE": "gpu", "OCR_WORKERS": 15},
    )

    options = poster_runner_runtime_options(context.configuration)

    assert options == RunnerRuntimeOptions(
        ocr_device="cpu",
        ocr_workers=2,
        execution_provider="CPUExecutionProvider",
        cuda_visible_devices="-1",
    )


def test_gpu_visibility_is_preserved_and_non_allowlisted_environment_is_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,4")
    monkeypatch.setenv("MARQUEE_TEST_SECRET", "not-for-child")
    monkeypatch.setattr(pipeline_settings, "EXECUTION_PROVIDER", "CUDAExecutionProvider")
    options = poster_runner_runtime_options({"OCR_DEVICE": "gpu", "OCR_WORKERS": 3})

    assert options.environment() == {
        "OCR_DEVICE": "gpu",
        "OCR_WORKERS": "3",
        "EXECUTION_PROVIDER": "CUDAExecutionProvider",
        "CUDA_VISIBLE_DEVICES": "2,4",
    }
    assert "MARQUEE_TEST_SECRET" not in options.environment()


@pytest.mark.parametrize("visibility", ["0,,1", "0,-1", "-2", "gpu", "0;1"])
def test_runtime_options_reject_invalid_cuda_visibility(visibility: str) -> None:
    with pytest.raises(ProtocolError, match="CUDA visibility"):
        RunnerRuntimeOptions(cuda_visible_devices=visibility)
