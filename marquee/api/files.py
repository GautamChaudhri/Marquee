"""HTTP responses for files the filesystem boundary has classified."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from email.utils import formatdate
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from fastapi.responses import Response, StreamingResponse

from marquee.fs.boundary import ClassifiedPath, FilesystemBoundary, FilesystemBoundaryError


def etag_matches(if_none_match: str, etag: str) -> bool:
    """RFC 9110 If-None-Match: weak comparison against a list or ``*``."""
    if if_none_match.strip() == "*":
        return True
    opaque = etag.removeprefix("W/")
    for candidate in if_none_match.split(","):
        if candidate.strip().removeprefix("W/") == opaque:
            return True
    return False


def file_response(
    boundary: FilesystemBoundary,
    classified: ClassifiedPath,
    *,
    media_type: str | None = None,
    filename: str | None = None,
    validator: Callable[[BinaryIO], None] | None = None,
    cache_control: str | None = None,
    etag: str | None = None,
    if_none_match: str | None = None,
    expected_size: int | None = None,
) -> Response:
    """Stream a classified file, optionally as a cacheable conditional response.

    ``etag`` is the bare validator value (quoted here); without one, a weak
    ETag is derived from the file's size and mtime so every response stays
    revalidatable. ``expected_size`` is a cheap integrity check against the
    caller's stored record — a mismatch refuses to serve rather than stream
    bytes that no longer match what the record certified.
    """
    fd = boundary.open_read(classified)
    try:
        stat_result = os.fstat(fd)
        if expected_size is not None and stat_result.st_size != expected_size:
            raise FilesystemBoundaryError("served object size does not match its record")
        if validator is not None:
            with os.fdopen(os.dup(fd), "rb") as probe:
                validator(probe)
            os.lseek(fd, 0, os.SEEK_SET)
    except BaseException:
        os.close(fd)
        raise

    strong_etag = (
        f'"{etag}"' if etag else f'W/"{stat_result.st_size:x}-{int(stat_result.st_mtime):x}"'
    )
    headers: dict[str, str] = {
        "etag": strong_etag,
        "last-modified": formatdate(stat_result.st_mtime, usegmt=True),
    }
    if cache_control:
        headers["cache-control"] = cache_control
    if filename:
        safe_name = Path(filename).name
        headers["content-disposition"] = f"attachment; filename*=UTF-8''{quote(safe_name)}"

    if if_none_match is not None and etag_matches(if_none_match, strong_etag):
        os.close(fd)
        return Response(status_code=304, headers=headers)

    headers["content-length"] = str(stat_result.st_size)

    def chunks() -> Iterator[bytes]:
        try:
            while chunk := os.read(fd, 64 * 1024):
                yield chunk
        finally:
            os.close(fd)

    return StreamingResponse(chunks(), media_type=media_type, headers=headers)
