"""Focused unit tests for the revised GATE-then-RANK pipeline."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

from marquee.config import Settings
from marquee.core.pipeline_config import PipelineSettings
from marquee.core.poster_sources.tmdb import PosterCandidate
from marquee.ml.artifact_codec import unicode_array, unicode_scalar
from marquee.ml.colorfulness import hasler_susstrunk
from marquee.ml.embedding import choose_execution_providers
from marquee.ml.normalize import normalize_features
from marquee.ml.taste_store import NumpyTasteStore
from marquee.pipeline import ocr_filter
from marquee.pipeline.deduper import PosterDeduper
from marquee.pipeline.features import calculate_text_residual
from marquee.pipeline.gate import PosterGate
from marquee.pipeline.output import place_ranked
from marquee.pipeline.runner import _RUNS_WORK_DATA, _clear_generated_outputs
from marquee.pipeline.scorer import WeightedScorer
from marquee.pipeline.types import CandidateScore, FeatureVector, OCRCandidateResult


def test_pipeline_run_root_is_inside_data():
    assert Settings().runs_work_path == _RUNS_WORK_DATA


def test_system_ocr_status_shape(monkeypatch: pytest.MonkeyPatch):
    from marquee.api.routes import system

    monkeypatch.setattr(system.pipeline_settings, "OCR_DEVICE", "auto")
    monkeypatch.setattr(system.pipeline_settings, "OCR_WORKERS", 0)
    monkeypatch.setattr(system, "effective_ocr_workers", lambda: 3)
    monkeypatch.setattr(system, "paddle_cuda_available", lambda: False)
    monkeypatch.setattr(
        system,
        "active_worker_status",
        lambda: {"active": [], "stale_reaped": []},
    )
    plan = {
        "requested": "auto",
        "gpu_build": False,
        "gpus": [],
        "auto_workers": 3,
        "expected_device": "cpu",
        "error": None,
        "confirmed": False,
    }
    monkeypatch.setattr(system, "ocr_device_plan", lambda: plan)

    status = system._ocr_status()

    assert status == {
        "device": "auto",
        "configured_workers": 0,
        "effective_workers": 3,
        "paddle_cuda_available": False,
        "workers": {"active": [], "stale_reaped": []},
        "plan": plan,
    }
