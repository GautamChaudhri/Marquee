"""Resolve and run the external media binaries (ffmpeg / mkvtoolnix).

A thin, checked wrapper over ``subprocess`` with three jobs:

  - Resolve a configured binary name/path to an executable (``shutil.which``),
    cached for the process lifetime.
  - Report which binaries are present so the API can surface an actionable
    "install mkvtoolnix" banner instead of failing opaquely mid-scan.
  - Run a command with a timeout and captured output, raising a typed error on
    a missing binary, non-zero exit, or timeout.

No binary is invoked at import time — availability is probed lazily and cached.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from marquee.config import settings

logger = logging.getLogger(__name__)

# Logical name -> settings attribute holding the configured binary path/name.
_BINARY_SETTINGS = {
    "ffmpeg": "LETTERBOX_FFMPEG",
    "ffprobe": "LETTERBOX_FFPROBE",
    "mkvpropedit": "LETTERBOX_MKVPROPEDIT",
    "mkvmerge": "LETTERBOX_MKVMERGE",
    "convert": "LETTERBOX_CONVERT",
    "dovi_tool": "LETTERBOX_DOVI_TOOL",
}


class BinaryError(RuntimeError):
    """A media binary is missing, failed, or timed out."""


class BinaryMissingError(BinaryError):
    """A required binary could not be found on PATH."""


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@cache
def resolve(name: str) -> str | None:
    """Absolute path to *name*'s executable, or None if not found.

    Cached: call ``reset_cache()`` if the environment changes at runtime.
    """
    configured = getattr(settings, _BINARY_SETTINGS.get(name, ""), name) or name
    return shutil.which(configured)


def reset_cache() -> None:
    """Drop the cached binary resolutions (e.g. after a PATH change in tests)."""
    resolve.cache_clear()


def availability() -> dict[str, bool]:
    """Map each known binary name to whether it is resolvable on PATH."""
    return {name: resolve(name) is not None for name in _BINARY_SETTINGS}


def require(*names: str) -> None:
    """Raise ``BinaryMissingError`` if any of *names* is unavailable."""
    missing = [name for name in names if resolve(name) is None]
    if missing:
        raise BinaryMissingError(
            f"Required media tool(s) not found on PATH: {', '.join(missing)}"
        )


def run(name: str, args: list[str], *, timeout: float = 120.0) -> CommandResult:
    """Run *name* with *args*; return captured output.

    Raises:
        BinaryMissingError: the binary is not on PATH.
        BinaryError: the command timed out (the process is killed first).

    A non-zero exit is NOT raised — callers inspect ``result.returncode`` /
    ``result.ok`` because ffmpeg writes useful detection output to stderr even
    when it exits non-zero (e.g. ``-f null`` pipelines).
    """
    binary = resolve(name)
    if binary is None:
        raise BinaryMissingError(f"Binary not found on PATH: {name}")
    try:
        completed = subprocess.run(  # noqa: S603 — args are constructed, not shell
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BinaryError(f"{name} timed out after {timeout}s") from exc
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def safe_media_path(path: str | Path) -> str:
    """Return ``str(path)`` for use as a media-tool argument, guaranteed safe.

    Media paths are always absolute (validated Radarr/Sonarr roots, or temp
    files we create), so a leading ``-`` — which a tool could misread as an
    option (argument injection) — cannot occur. Reject anything relative as
    defence-in-depth, and pair this with a literal ``--`` separator wherever
    the file is the trailing positional argument.
    """
    p = Path(path)
    if not p.is_absolute():
        raise BinaryError(f"Refusing non-absolute media path: {str(path)!r}")
    return str(p)
