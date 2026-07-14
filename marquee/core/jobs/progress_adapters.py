from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolProgressSample:
    mode: str
    completed: float | None = None
    total: float | None = None
    speed: float | None = None
    fps: float | None = None
    bytes_processed: int | None = None
    warning: bool = False
    error: bool = False
    finished: bool = False
    exit_code: int | None = None


def _finite(value: str) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


class FFmpegProgressAdapter:
    """Pure incremental parser for FFmpeg's machine-readable `-progress` packets."""

    def __init__(self, *, duration_seconds: float | None) -> None:
        self.duration = (
            duration_seconds
            if duration_seconds is not None
            and math.isfinite(duration_seconds)
            and duration_seconds > 0
            else None
        )
        self._buffer = ""
        self._packet: dict[str, str] = {}
        self._last_time = 0.0

    def feed(self, chunk: str | bytes) -> tuple[ToolProgressSample, ...]:
        text = chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
        self._buffer += text
        if len(self._buffer) > 65536:
            self._buffer = self._buffer[-65536:]
            self._packet.clear()
        samples: list[ToolProgressSample] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip("\r")
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if not key or len(key) > 80 or len(value) > 500:
                continue
            self._packet[key] = value
            if key == "progress":
                sample = self._finish_packet(value)
                self._packet = {}
                if sample is not None:
                    samples.append(sample)
        return tuple(samples)

    def _finish_packet(self, state: str) -> ToolProgressSample | None:
        processed_us = _finite(self._packet.get("out_time_us", ""))
        processed = processed_us / 1_000_000 if processed_us is not None else None
        if processed is not None and processed < self._last_time:
            return None
        if processed is not None:
            self._last_time = processed
        speed_text = self._packet.get("speed", "").removesuffix("x")
        speed = _finite(speed_text)
        fps = _finite(self._packet.get("fps", ""))
        size = _finite(self._packet.get("total_size", ""))
        finished = state == "end"
        if self.duration is None or processed is None:
            return ToolProgressSample(
                mode="indeterminate",
                speed=speed,
                fps=fps,
                bytes_processed=int(size) if size is not None else None,
                finished=finished,
            )
        return ToolProgressSample(
            mode="determinate",
            completed=min(processed, self.duration),
            total=self.duration,
            speed=speed,
            fps=fps,
            bytes_processed=int(size) if size is not None else None,
            finished=finished,
        )


class MkvmergeProgressAdapter:
    """Pure incremental parser for mkvmerge `--gui-mode` records only."""

    _PROGRESS = re.compile(r"^#GUI#progress\s+(\d+(?:\.\d+)?)%$")
    _EXIT = re.compile(r"^#GUI#exit\s+(-?\d+)$")

    def __init__(self) -> None:
        self._buffer = ""
        self._last_percent = 0.0

    def feed(self, chunk: str | bytes) -> tuple[ToolProgressSample, ...]:
        text = chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
        self._buffer += text
        if len(self._buffer) > 65536:
            self._buffer = self._buffer[-65536:]
        samples: list[ToolProgressSample] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip("\r")
            if match := self._PROGRESS.fullmatch(line):
                percent = float(match.group(1))
                if self._last_percent <= percent <= 100:
                    self._last_percent = percent
                    samples.append(
                        ToolProgressSample(
                            mode="determinate", completed=percent, total=100
                        )
                    )
            elif line.startswith("#GUI#error"):
                samples.append(ToolProgressSample(mode="indeterminate", error=True))
            elif line.startswith("#GUI#warning"):
                samples.append(ToolProgressSample(mode="indeterminate", warning=True))
            elif match := self._EXIT.fullmatch(line):
                code = int(match.group(1))
                samples.append(
                    ToolProgressSample(
                        mode="indeterminate",
                        finished=True,
                        error=code != 0,
                        exit_code=code,
                    )
                )
        return tuple(samples)


class IndeterminateProgressAdapter:
    """Bounded liveness samples for opaque work; never invents a denominator."""

    def __init__(self) -> None:
        self._ordinal = 0

    def tick(self) -> ToolProgressSample:
        self._ordinal += 1
        return ToolProgressSample(mode="indeterminate")
