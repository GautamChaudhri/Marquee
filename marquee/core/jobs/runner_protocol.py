"""Bounded, versioned control protocol for the JMC6H internal runner.

The coordinator launches a fixed, code-owned Python runner in the attempt's
process group/cgroup and exchanges only small structured frames with it:

- the coordinator sends one bounded versioned *manifest* on the child's stdin;
- the child streams bounded *control frames* (ready, progress, warning, metric,
  produced-file notice, final result) back on a dedicated control pipe.

Frames are length-prefixed JSON. They are deliberately separate from the child's
ordinary stdout/stderr, which still tee into the redacted attempt log. Large
candidate/model data never travels here: it moves through confined workspace files
and artifact keys, and a frame only references such a key plus its checksum/size.

Oversized or malformed frames fail closed. This module is intentionally dependency
free so the child entrypoint stays cheap to import.
"""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

PROTOCOL_VERSION = 1

# One control/progress/result frame and the inbound manifest are each capped well
# below any real payload; nothing large is ever meant to cross this channel.
MAX_FRAME_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
# A generous but finite ceiling on frames per attempt so a runaway child cannot
# stream unbounded progress into the coordinator.
MAX_FRAMES = 100_000

# Fixed environment key that names the inherited control-pipe file descriptor.
# It carries a small integer only; no path, module, or command ever crosses it.
CONTROL_FD_ENV = "MARQUEE_RUNNER_CONTROL_FD"

_PROVIDER = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_CUDA_VISIBLE = re.compile(r"^(?:-1|[0-9]+(?:,[0-9]+)*)?$")


@dataclass(frozen=True, slots=True)
class RunnerRuntimeOptions:
    """Closed, bounded deployment options for a contained internal runner."""

    ocr_device: str = "auto"
    ocr_workers: int = 0
    execution_provider: str = "auto"
    cuda_visible_devices: str | None = None

    def __post_init__(self) -> None:
        if self.ocr_device not in {"auto", "cpu", "gpu"}:
            raise ProtocolError("runner OCR device is invalid")
        if (
            not isinstance(self.ocr_workers, int)
            or isinstance(self.ocr_workers, bool)
            or not 0 <= self.ocr_workers <= 16
        ):
            raise ProtocolError("runner OCR worker count is invalid")
        if not _PROVIDER.fullmatch(self.execution_provider):
            raise ProtocolError("runner execution provider is invalid")
        if self.cuda_visible_devices is not None and (
            len(self.cuda_visible_devices) > 80
            or not _CUDA_VISIBLE.fullmatch(self.cuda_visible_devices)
        ):
            raise ProtocolError("runner CUDA visibility is invalid")

    def environment(self) -> dict[str, str]:
        environment = {
            "OCR_DEVICE": self.ocr_device,
            "OCR_WORKERS": str(self.ocr_workers),
            "EXECUTION_PROVIDER": self.execution_provider,
        }
        if self.cuda_visible_devices is not None:
            environment["CUDA_VISIBLE_DEVICES"] = self.cuda_visible_devices
        return environment

_HEADER = struct.Struct(">I")


class RunnerOperation(StrEnum):
    """Closed vocabulary of operations the internal runner may perform.

    The coordinator selects exactly one of these; a caller can never submit Python
    code, a module name, a command fragment, or a physical path. Poster/ML members
    are wired to the real, workspace-confined implementations as their family gates
    pass (JMC6H H2/H4); ``NOOP`` exists to certify the transport itself.
    """

    NOOP = "noop"
    POSTER_SINGLE = "poster_single"
    TASTE_PROFILE = "taste_profile"
    TASTE_MAP = "taste_map"
    ENRICHMENT = "enrichment"
    RANKING_RESIDUAL = "ranking_residual"


class ProtocolError(RuntimeError):
    """A control frame or manifest violated the fixed protocol bounds."""


def encode_frame(obj: dict[str, Any]) -> bytes:
    """Encode one typed frame as a length-prefixed, canonical JSON payload."""
    if not isinstance(obj, dict) or obj.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("frame must be a versioned object")
    if not isinstance(obj.get("type"), str):
        raise ProtocolError("frame must carry a string type")
    payload = json.dumps(obj, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError("control frame exceeds the fixed bound")
    return _HEADER.pack(len(payload)) + payload


def decode_payload(payload: bytes) -> dict[str, Any]:
    """Validate and decode one frame payload into a versioned typed object."""
    try:
        obj = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError("control frame is not valid JSON") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("control frame is not an object")
    if obj.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("control frame has an unsupported protocol version")
    if not isinstance(obj.get("type"), str):
        raise ProtocolError("control frame has no string type")
    return obj


def encode_manifest(manifest: dict[str, Any]) -> bytes:
    """Encode the inbound operation manifest as one length-prefixed frame."""
    payload = json.dumps(
        manifest, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ProtocolError("operation manifest exceeds the fixed bound")
    return _HEADER.pack(len(payload)) + payload


def decode_manifest(payload: bytes) -> dict[str, Any]:
    """Validate and decode the inbound operation manifest."""
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ProtocolError("operation manifest exceeds the fixed bound")
    try:
        manifest = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError("operation manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ProtocolError("operation manifest is not an object")
    if manifest.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("operation manifest has an unsupported protocol version")
    if not isinstance(manifest.get("operation"), str):
        raise ProtocolError("operation manifest names no operation")
    return manifest


class ControlWriter:
    """Child-side writer: emit bounded control frames on the inherited pipe fd."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._count = 0

    def emit(self, obj: dict[str, Any]) -> None:
        self._count += 1
        if self._count > MAX_FRAMES:
            raise ProtocolError("control frame count exceeded the fixed bound")
        frame = encode_frame(obj)
        view = memoryview(frame)
        while view:
            written = os.write(self._fd, view)
            view = view[written:]

    def close(self) -> None:
        os.close(self._fd)


def read_manifest_from_stdin(stdin_fd: int) -> dict[str, Any]:
    """Read exactly one length-prefixed manifest frame from the child's stdin."""
    header = _read_exact_fd(stdin_fd, 4)
    if header is None:
        raise ProtocolError("no operation manifest was delivered")
    (length,) = _HEADER.unpack(header)
    if length > MAX_MANIFEST_BYTES:
        raise ProtocolError("operation manifest exceeds the fixed bound")
    payload = _read_exact_fd(stdin_fd, length)
    if payload is None:
        raise ProtocolError("operation manifest was truncated")
    return decode_manifest(payload)


def _read_exact_fd(fd: int, count: int) -> bytes | None:
    """Read exactly ``count`` bytes; ``None`` only on a clean EOF before any byte."""
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = os.read(fd, remaining)
        if not chunk:
            return None if remaining == count else _raise_truncated()
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _raise_truncated() -> bytes:
    raise ProtocolError("stream ended mid-frame")
