"""Small generated MKV/MP4 fixtures for JMC5B media mutation tests (§9).

Nothing binary is committed: every fixture is synthesised at test time with
ffmpeg/mkvmerge into a confined temporary directory.  Callers skip automatically
when the native tools are unavailable, so the suite stays runnable without them.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REQUIRED_BINARIES = ("ffmpeg", "ffprobe", "mkvmerge")


def require_media_tools() -> None:
    """Skip a test when the native media tools are not installed."""
    missing = [name for name in REQUIRED_BINARIES if shutil.which(name) is None]
    if missing:
        pytest.skip(f"media fixtures require {', '.join(missing)}")


def _run(args: list[str]) -> None:
    completed = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {completed.stderr.strip()[:400]}")


@dataclass(frozen=True, slots=True)
class AudioSpec:
    language: str
    channels: int
    codec: str
    title: str | None = None
    default: bool = False


@dataclass(frozen=True, slots=True)
class SubtitleSpec:
    language: str
    text: str
    default: bool = False
    forced: bool = False


def _video(directory: Path) -> Path:
    path = directory / "_video.mp4"
    if not path.exists():
        _run(
            [
                "ffmpeg", "-v", "error", "-f", "lavfi",
                "-i", "testsrc=size=64x48:rate=5:duration=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path), "-y",
            ]
        )
    return path


def _audio(directory: Path, spec: AudioSpec, index: int) -> Path:
    suffix = {"ac3": "ac3", "aac": "m4a", "flac": "flac"}[spec.codec]
    path = directory / f"_audio{index}.{suffix}"
    frequency = 220 + index * 110
    _run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi",
            "-i", f"sine=frequency={frequency}:duration=1",
            "-ac", str(spec.channels), "-c:a", spec.codec, str(path), "-y",
        ]
    )
    return path


def _subtitle(directory: Path, spec: SubtitleSpec, index: int) -> Path:
    path = directory / f"_sub{index}.srt"
    path.write_text(f"1\n00:00:00,000 --> 00:00:01,000\n{spec.text}\n\n")
    return path


def build_mkv(
    directory: Path,
    name: str,
    *,
    audio: tuple[AudioSpec, ...] = (),
    subtitles: tuple[SubtitleSpec, ...] = (),
    attachment: bool = False,
    chapters: bool = False,
) -> Path:
    """Build one small multi-track MKV with exact languages/titles/dispositions."""
    require_media_tools()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    args = ["mkvmerge", "-q", "-o", str(destination), str(_video(directory))]

    for index, spec in enumerate(audio):
        source = _audio(directory, spec, index)
        args += ["--language", f"0:{spec.language}"]
        if spec.title:
            args += ["--track-name", f"0:{spec.title}"]
        args += ["--default-track-flag", f"0:{'yes' if spec.default else 'no'}", str(source)]

    for index, spec in enumerate(subtitles):
        source = _subtitle(directory, spec, index)
        args += ["--language", f"0:{spec.language}"]
        args += ["--default-track-flag", f"0:{'yes' if spec.default else 'no'}"]
        if spec.forced:
            args += ["--forced-display-flag", "0:yes"]
        args.append(str(source))

    if chapters:
        chapter_file = directory / "_chapters.txt"
        chapter_file.write_text("CHAPTER01=00:00:00.000\nCHAPTER01NAME=Opening\n")
        args += ["--chapters", str(chapter_file)]

    if attachment:
        note = directory / "_attachment.txt"
        note.write_text("marquee synthetic attachment")
        args += ["--attach-file", str(note)]

    _run(args)
    return destination


def sidecar(directory: Path, name: str, text: str = "hello") -> Path:
    """Write one external subtitle sidecar."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n\n")
    return path


DEFAULT_AUDIO = (
    AudioSpec(language="eng", channels=6, codec="ac3", title="Surround", default=True),
    AudioSpec(language="fra", channels=2, codec="aac", title="Stereo"),
)
DEFAULT_SUBTITLES = (
    SubtitleSpec(language="eng", text="hello", default=True),
    SubtitleSpec(language="fra", text="bonjour", forced=True),
)
DUPLICATE_AUDIO = (
    AudioSpec(language="eng", channels=2, codec="aac"),
    AudioSpec(language="eng", channels=2, codec="aac"),
)
