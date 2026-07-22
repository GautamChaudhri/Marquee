"""Trusted, bounded runtime options for contained internal runners."""

from __future__ import annotations

import os
from collections.abc import Mapping

from marquee.core.jobs.runner_protocol import RunnerRuntimeOptions
from marquee.core.pipeline_config import pipeline_settings


def poster_runner_runtime_options(configuration: Mapping[str, object]) -> RunnerRuntimeOptions:
    """Build poster OCR options from its immutable job configuration snapshot.

    The deployment, rather than the job request, owns the inference provider and
    GPU visibility. CPU work deliberately hides CUDA from the contained runner.
    """
    ocr_device = configuration.get("OCR_DEVICE", "auto")
    workers = configuration.get("OCR_WORKERS", 0)
    if not isinstance(ocr_device, str) or not isinstance(workers, int) or isinstance(workers, bool):
        raise ValueError("job OCR configuration snapshot is invalid")
    cuda_visible_devices = "-1" if ocr_device == "cpu" else os.environ.get("CUDA_VISIBLE_DEVICES")
    return RunnerRuntimeOptions(
        ocr_device=ocr_device,
        ocr_workers=workers,
        execution_provider=pipeline_settings.EXECUTION_PROVIDER,
        cuda_visible_devices=cuda_visible_devices,
    )
