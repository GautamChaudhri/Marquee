"""Poster download hardening — content-type and size validation shared by
the pipeline fetch, full-res output download, and restore re-download."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from marquee.core.download_guard import (
    MAX_POSTER_BYTES,
    DownloadRejectedError,
    ensure_image_response,
)
from marquee.core.poster_sources.tmdb import PosterCandidate
from marquee.pipeline.output import _download_original
from marquee.pipeline.types import CandidateScore


def _response(content: bytes, content_type: str | None) -> httpx.Response:
    headers = {"content-type": content_type} if content_type else {}
    return httpx.Response(
        200,
        headers=headers,
        content=content,
        request=httpx.Request("GET", "https://image.tmdb.org/t/p/original/x.jpg"),
    )


def test_accepts_normal_image():
    ensure_image_response(_response(b"\xff\xd8\xff jpeg bytes", "image/jpeg"))


def test_accepts_content_type_with_charset_suffix():
    ensure_image_response(_response(b"png", "image/png; charset=binary"))


@pytest.mark.parametrize("content_type", ["text/html", "application/json", None])
def test_rejects_non_image_content_type(content_type):
    with pytest.raises(DownloadRejectedError):
        ensure_image_response(_response(b"<html>error</html>", content_type))


def test_rejects_oversized_body():
    big = b"x" * (MAX_POSTER_BYTES + 1)
    with pytest.raises(DownloadRejectedError):
        ensure_image_response(_response(big, "image/jpeg"))


def test_rejects_oversized_declared_length():
    # The guard only touches .headers / .content, so a stub suffices for the
    # declared-length branch (httpx recomputes content-length from the body).
    response = SimpleNamespace(
        headers={"content-type": "image/jpeg", "content-length": str(MAX_POSTER_BYTES + 1)},
        content=b"tiny",
    )
    with pytest.raises(DownloadRejectedError):
        ensure_image_response(response)


@pytest.mark.asyncio
async def test_download_original_rejects_html(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")

    candidate = PosterCandidate(
        file_path="/abc.jpg",
        width=2000,
        height=3000,
        aspect_ratio=0.667,
        language=None,
        vote_average=5.0,
        vote_count=10,
    )
    dest: Path = tmp_path / "01_abc.jpg"
    score = CandidateScore(image_path=dest, orig_filename="abc.jpg")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        _, ok, error = await _download_original(score, candidate, client)

    assert ok is False
    assert "image/*" in (error or "")
    assert not dest.exists()
