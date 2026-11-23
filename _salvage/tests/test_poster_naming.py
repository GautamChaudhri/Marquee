from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from marquee.core.path_utils import PathValidationError
from marquee.core.poster_files import sanitize_poster_filename, verify_jpeg_file
from marquee.core.poster_naming import normalize_poster_template, render_poster_filename


def test_templates_normalize_missing_jpeg_suffixes():
    assert normalize_poster_template("movie", "{movie_basename}-poster") == (
        "{movie_basename}-poster.jpg"
    )
    assert normalize_poster_template("series", "show") == "show.jpg"
    assert normalize_poster_template("season", "season{season:02d}") == ("season{season:02d}.jpg")


@pytest.mark.parametrize(
    ("kind", "template"),
    [
        ("movie", "{movie_basename.__class__}.jpg"),
        ("movie", "{movie_basename!r}.jpg"),
        ("movie", "{movie_basename:>20}.jpg"),
        ("series", "{season}.jpg"),
        ("season", "season.jpg"),
        ("season", "season{season:1000000d}.jpg"),
        ("season", "season{season}{season}.jpg"),
        ("movie", "poster.nfo"),
    ],
)
def test_templates_reject_expansion_and_non_jpeg_grammars(kind: str, template: str):
    with pytest.raises(PathValidationError):
        normalize_poster_template(kind, template)  # type: ignore[arg-type]


def test_rendering_rejects_traversal_and_filesystem_amplification():
    with pytest.raises(PathValidationError, match="path separators"):
        render_poster_filename(
            "movie",
            "{movie_basename}.jpg",
            movie_basename="../../database",
        )
    with pytest.raises(PathValidationError, match="255-byte"):
        render_poster_filename(
            "movie",
            "{movie_basename}.jpg",
            movie_basename="x" * 256,
        )


def test_sanitizer_rejects_explicit_non_jpeg_extensions():
    with pytest.raises(PathValidationError, match=".jpg or .jpeg"):
        sanitize_poster_filename("database.sqlite")


def test_jpeg_verifier_uses_content_not_filename_or_media_type():
    jpeg = BytesIO()
    Image.new("RGB", (2, 2), "red").save(jpeg, format="JPEG")
    verify_jpeg_file(jpeg)

    png = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(png, format="PNG")
    with pytest.raises(PathValidationError, match="not a JPEG"):
        verify_jpeg_file(png)
