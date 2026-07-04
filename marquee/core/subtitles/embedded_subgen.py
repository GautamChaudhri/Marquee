"""Embedded Subgen child-process plumbing.

The embedded deployment vendors upstream ``subgen.py`` but keeps Marquee as the
source of truth for lifecycle, configuration, and health reporting.
"""

from __future__ import annotations

import os
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from marquee.config import settings
from marquee.core.subtitles.config import subtitle_settings
from marquee.core.subtitles.whisper_catalog import HardwareGpu, HardwareSnapshot, recommend

_QUEUE_RE = re.compile(r"Jobs:\s*(?P<processing>\d+)\s+processing,\s*(?P<queued>\d+)\s+queued")
_DOWNLOAD_RE = re.compile(r"download", re.IGNORECASE)
_LOAD_RE = re.compile(r"model|whisper", re.IGNORECASE)
_ACTIVITY_RE = re.compile(r"(WORKER START|Completed|Transcribing|Translating)", re.IGNORECASE)


@dataclass
class EmbeddedSubgenStatus:
    state: str = "disabled"
    queue_processing: int = 0
    queue_queued: int = 0
    last_activity_line: str | None = None
    last_download_line: str | None = None
    last_model_line: str | None = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=500))

    def record(self, line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        self.logs.append(line)
        if match := _QUEUE_RE.search(line):
            self.queue_processing = int(match.group("processing"))
            self.queue_queued = int(match.group("queued"))
        if _DOWNLOAD_RE.search(line):
            self.last_download_line = line
        if _LOAD_RE.search(line):
            self.last_model_line = line
        if _ACTIVITY_RE.search(line):
            self.last_activity_line = line


def hardware_snapshot() -> HardwareSnapshot:
    from marquee.core import system_metrics  # noqa: PLC0415

    gpus = [
        HardwareGpu(
            index=item["index"],
            name=item["name"],
            vram_total=item["vram_total"],
            vram_free=item["vram_free"],
        )
        for item in system_metrics.gpu_inventory()
    ]
    return HardwareSnapshot(
        gpus=gpus,
        cpu_count=psutil.cpu_count(logical=True) or 1,
        ram_total=psutil.virtual_memory().total,
    )


def resolved_model_and_device() -> dict:
    hw = hardware_snapshot()
    english_only = subtitle_settings.effective_preferred_subtitle_languages == ["en"]
    recommendation = recommend(
        hw, subtitle_settings.SUBGEN_MODE, english_only_preferred=english_only
    )
    model = subtitle_settings.SUBGEN_WHISPER_MODEL or recommendation["model_id"]
    device = subtitle_settings.SUBGEN_TRANSCRIBE_DEVICE
    if device == "auto":
        device = recommendation["device"]
    gpu_index = subtitle_settings.SUBGEN_GPU_INDEX
    if device == "cuda" and gpu_index is None:
        gpu_index = recommendation["gpu_index"]
    compute = subtitle_settings.SUBGEN_COMPUTE_TYPE
    if compute == "auto":
        compute = recommendation["compute_type"]
    return {
        "hardware": hw,
        "recommendation": recommendation,
        "model": model,
        "device": device,
        "gpu_index": gpu_index,
        "compute_type": compute,
    }


def ensure_callback_token() -> str:
    if subtitle_settings.SUBGEN_CALLBACK_TOKEN:
        return subtitle_settings.SUBGEN_CALLBACK_TOKEN
    token = os.urandom(24).hex()
    subtitle_settings.SUBGEN_CALLBACK_TOKEN = token
    return token


def build_spawn_spec() -> dict:
    resolved = resolved_model_and_device()
    token = ensure_callback_token()
    env = os.environ.copy()
    env.update(
        {
            "WHISPER_MODEL": resolved["model"],
            "TRANSCRIBE_DEVICE": "cuda" if resolved["device"] == "cuda" else "cpu",
            "COMPUTE_TYPE": resolved["compute_type"],
            "WHISPER_THREADS": str(subtitle_settings.SUBGEN_WHISPER_THREADS),
            "CONCURRENT_TRANSCRIPTIONS": str(
                subtitle_settings.SUBGEN_CONCURRENT_TRANSCRIPTIONS
            ),
            "CLEAR_VRAM_ON_COMPLETE": "True",
            "MODEL_PATH": subtitle_settings.SUBGEN_MODEL_PATH,
            "WEBHOOK_PORT": str(subtitle_settings.SUBGEN_EMBEDDED_PORT),
            "TRANSCRIBE_OR_TRANSLATE": subtitle_settings.SUBGEN_MODE,
            "WEBHOOK_URL_COMPLETED": (
                f"http://127.0.0.1:{settings.PORT}/api/webhooks/subgen?token={token}"
            ),
            "SKIP_STARTUP_SCAN": "False",
            "MONITOR": "False",
            "UPDATE": "False",
            "USE_PATH_MAPPING": "False",
            "SUBTITLE_LANGUAGE_NAMING_TYPE": subtitle_settings.SUBGEN_NAMING_TYPE,
            "SHOW_IN_SUBNAME_SUBGEN": str(subtitle_settings.SUBGEN_NAME_INCLUDES_SUBGEN),
            "SHOW_IN_SUBNAME_MODEL": str(subtitle_settings.SUBGEN_NAME_INCLUDES_MODEL),
        }
    )
    if resolved["device"] == "cuda" and resolved["gpu_index"] is not None:
        env["CUDA_VISIBLE_DEVICES"] = str(resolved["gpu_index"])

    script = Path(__file__).resolve().parents[2] / "vendor" / "subgen" / "subgen.py"
    return {
        "args": [str(script)],
        "env": env,
        "resolved": resolved,
    }
