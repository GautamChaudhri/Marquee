"""Post-write output validation (design §23.5).

Before a remuxed temp file is allowed to replace the original, re-probe it and
confirm the invariants that matter: video survives, audio is unchanged, duration
matches within tolerance, and the subtitle delta is what we asked for. A failure
here means the temp file is discarded and the original is never touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from marquee.core.subtitles import probe


@dataclass
class ValidationResult:
    ok: bool
    problems: list[str]


def validate_output(
    source: probe.ProbeResult,
    out_path: Path | str,
    *,
    expected_subtitle_delta: int = 0,
    duration_tolerance_s: float = 2.0,
) -> ValidationResult:
    """Validate a remuxed output against the source probe."""
    problems: list[str] = []
    out = probe.probe_container(out_path)
    if out is None:
        return ValidationResult(False, ["output not probeable"])

    if out.video_count < 1:
        problems.append("output has no video stream")
    if out.video_count != source.video_count:
        problems.append(
            f"video stream count changed: {source.video_count} -> {out.video_count}"
        )
    if len(out.audio_streams) != len(source.audio_streams):
        problems.append(
            f"audio stream count changed: {len(source.audio_streams)} -> {len(out.audio_streams)}"
        )
    if (
        source.duration_seconds is not None
        and out.duration_seconds is not None
        and abs(out.duration_seconds - source.duration_seconds) > duration_tolerance_s
    ):
        problems.append(
            f"duration drifted: {source.duration_seconds:.1f}s -> {out.duration_seconds:.1f}s"
        )

    expected_subs = len(source.subtitles) + expected_subtitle_delta
    if len(out.subtitles) != expected_subs:
        problems.append(
            f"subtitle count mismatch: expected {expected_subs}, got {len(out.subtitles)}"
        )

    return ValidationResult(not problems, problems)
