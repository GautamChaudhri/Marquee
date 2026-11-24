from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from marquee.api.files import etag_matches, file_response
from marquee.fs.boundary import FilesystemBoundaryError, boundary_for_roots


@pytest.mark.parametrize(
    ("if_none_match", "matches"),
    [
        ("*", True),
        ('"abc"', True),
        ('W/"abc"', True),
        ('"zzz", "abc"', True),
        ('"zzz"', False),
        ("", False),
    ],
)
def test_etag_matching_follows_weak_comparison(if_none_match: str, matches: bool) -> None:
    assert etag_matches(if_none_match, '"abc"') is matches


def test_response_carries_validators_and_content_length(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    (root / "poster.jpg").write_bytes(b"\xff\xd8\xffcontent")
    boundary = boundary_for_roots({"data": root})
    classified = boundary.classify(root / "poster.jpg", require_file=True)

    response = file_response(boundary, classified, media_type="image/jpeg", etag="tok")
    assert response.headers["etag"] == '"tok"'
    assert response.headers["content-length"] == "10"
    assert "last-modified" in response.headers
    assert "cache-control" not in response.headers

    weak = file_response(boundary, classified)
    assert weak.headers["etag"].startswith('W/"')


def test_response_304_closes_descriptor_and_keeps_cache_headers(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    (root / "poster.jpg").write_bytes(b"\xff\xd8\xffcontent")
    boundary = boundary_for_roots({"data": root})
    classified = boundary.classify(root / "poster.jpg", require_file=True)

    opened: list[int] = []
    original_open_read = boundary.open_read

    def tracking_open_read(target):
        fd = original_open_read(target)
        opened.append(fd)
        return fd

    with patch.object(boundary, "open_read", tracking_open_read):
        response = file_response(
            boundary,
            classified,
            etag="tok",
            if_none_match='"tok"',
            cache_control="public, no-cache",
        )
    assert response.status_code == 304
    assert response.headers["etag"] == '"tok"'
    assert response.headers["cache-control"] == "public, no-cache"
    assert "content-length" not in response.headers
    (fd,) = opened
    with pytest.raises(OSError):
        os.fstat(fd)  # the fd must already be closed on the 304 path


def test_response_refuses_size_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    (root / "poster.jpg").write_bytes(b"\xff\xd8\xffcontent")
    boundary = boundary_for_roots({"data": root})
    classified = boundary.classify(root / "poster.jpg", require_file=True)

    with pytest.raises(FilesystemBoundaryError, match="size does not match"):
        file_response(boundary, classified, expected_size=999)
