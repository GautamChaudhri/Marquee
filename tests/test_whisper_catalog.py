from __future__ import annotations

from marquee.core.subtitles.whisper_catalog import (
    HardwareGpu,
    HardwareSnapshot,
    per_model_verdicts,
    recommend,
)


def _snapshot(*, gpus, cpu_count=8, ram_gb=16):
    return HardwareSnapshot(gpus=gpus, cpu_count=cpu_count, ram_total=ram_gb * 1024**3)


def test_gpu_recommendation_prefers_turbo_on_8gb():
    hw = _snapshot(gpus=[HardwareGpu(index=0, name="RTX 3070", vram_total=8 * 1024**3, vram_free=6 * 1024**3)])

    recommendation = recommend(hw, "transcribe")
    verdicts = {item["id"]: item for item in per_model_verdicts(hw, "transcribe")}

    assert recommendation["device"] == "cuda"
    assert recommendation["model_id"] == "large-v3-turbo"
    # large-v3 fp16 (4.7 GB) fits the 8 GB budget (8 − 1.5 GB reserve = 6.5 GB),
    # yet turbo stays the transcribe pick (faster, lighter) — the intended policy.
    assert verdicts["large-v3"]["verdict"] == "fits_fp16"


def test_gpu_translate_prefers_large_v3_on_24gb():
    hw = _snapshot(gpus=[HardwareGpu(index=1, name="RTX 4090", vram_total=24 * 1024**3, vram_free=20 * 1024**3)])

    recommendation = recommend(hw, "translate")

    assert recommendation["device"] == "cuda"
    assert recommendation["gpu_index"] == 1
    assert recommendation["model_id"] == "large-v3"
    assert recommendation["compute_type"] == "float16"


def test_small_gpu_marks_large_models_too_big():
    hw = _snapshot(gpus=[HardwareGpu(index=0, name="Laptop GPU", vram_total=4 * 1024**3, vram_free=2 * 1024**3)])
    verdicts = {item["id"]: item for item in per_model_verdicts(hw, "translate")}

    assert verdicts["large-v3"]["verdict"] == "too_big"
    assert verdicts["large-v3-turbo"]["verdict"] == "unavailable_translate"


def test_cpu_only_prefers_small_and_flags_impractical_large_v3():
    hw = _snapshot(gpus=[], cpu_count=4, ram_gb=8)

    recommendation = recommend(hw, "transcribe")
    verdicts = {item["id"]: item for item in per_model_verdicts(hw, "translate")}

    assert recommendation["device"] == "cpu"
    assert recommendation["model_id"] == "small"
    assert verdicts["large-v3"]["verdict"] == "impractical_cpu"
