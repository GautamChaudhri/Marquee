from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_gauntlet_module():
    path = Path(__file__).resolve().parents[1] / "experiments" / "gauntlet_runner.py"
    spec = importlib.util.spec_from_file_location("gauntlet_runner_for_tests", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_taste_liveness_marker_tracks_richer_progress_fields():
    gauntlet = _load_gauntlet_module()
    rebuild = {
        "updated_at": "2026-06-17T01:00:00+00:00",
        "stage": "calibration",
        "substage": "ocr",
        "current_item": "poster.jpg",
        "processed": 9,
        "total": 430,
        "message": "Measuring OCR title geometry for poster.jpg.",
    }

    marker = gauntlet.Gauntlet.taste_liveness_marker(rebuild)

    assert marker == (
        "2026-06-17T01:00:00+00:00",
        "calibration",
        "ocr",
        "poster.jpg",
        9,
        430,
        "Measuring OCR title geometry for poster.jpg.",
    )


def test_default_gauntlet_skips_full_taste_retrain():
    gauntlet = _load_gauntlet_module()

    assert gauntlet.RUN_TASTE_RETRAIN is False
    assert gauntlet.TASTE_RETRAIN_MODE == "skip"
