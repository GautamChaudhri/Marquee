from __future__ import annotations

from marquee.core.subtitles.config import subtitle_settings
from marquee.core.subtitles.embedded_subgen import build_spawn_spec
from marquee.core.subtitles.whisper_catalog import HardwareGpu, HardwareSnapshot


def test_build_spawn_spec_assembles_embedded_env(monkeypatch):
    monkeypatch.setattr(subtitle_settings, "SUBGEN_MODE", "transcribe")
    monkeypatch.setattr(subtitle_settings, "SUBGEN_WHISPER_MODEL", "large-v3-turbo")
    monkeypatch.setattr(subtitle_settings, "SUBGEN_TRANSCRIBE_DEVICE", "cuda")
    monkeypatch.setattr(subtitle_settings, "SUBGEN_GPU_INDEX", 2)
    monkeypatch.setattr(subtitle_settings, "SUBGEN_COMPUTE_TYPE", "float16")
    monkeypatch.setattr(subtitle_settings, "SUBGEN_CALLBACK_TOKEN", "secret-token")
    monkeypatch.setattr(
        "marquee.core.subtitles.embedded_subgen.hardware_snapshot",
        lambda: HardwareSnapshot(
            gpus=[HardwareGpu(index=2, name="RTX 4080", vram_total=16 * 1024**3, vram_free=12 * 1024**3)],
            cpu_count=16,
            ram_total=64 * 1024**3,
        ),
    )

    spec = build_spawn_spec()

    assert spec["args"][-1].endswith("marquee/vendor/subgen/subgen.py")
    assert spec["env"]["WHISPER_MODEL"] == "large-v3-turbo"
    assert spec["env"]["TRANSCRIBE_DEVICE"] == "cuda"
    assert spec["env"]["CUDA_VISIBLE_DEVICES"] == "2"
    assert spec["env"]["WEBHOOK_URL_COMPLETED"].endswith("token=secret-token")
