"""Poster thumbnails: made with Pillow, never enlarged, refused when they do not decode, and
kept under the poster's hash."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, ImageFile

from marquee.artwork import thumbnails
from marquee.config import settings


def _jpeg(width: int, height: int, mode: str = "RGB") -> bytes:
    out = io.BytesIO()
    Image.new(mode, (width, height), 128).save(out, format="JPEG", quality=95)
    return out.getvalue()


def _size(data: bytes) -> tuple[int, int, str | None]:
    with Image.open(io.BytesIO(data)) as image:
        return image.width, image.height, image.format


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


@pytest.mark.parametrize("width", thumbnails.WIDTHS)
def test_a_thumbnail_is_a_jpeg_of_the_width_asked_for(width: int) -> None:
    assert _size(thumbnails.make(_jpeg(2000, 3000), width)) == (width, width * 3 // 2, "JPEG")


def test_a_narrow_poster_is_never_enlarged() -> None:
    assert _size(thumbnails.make(_jpeg(500, 750), 640)) == (500, 750, "JPEG")


def test_a_greyscale_or_png_poster_comes_out_as_a_colour_jpeg() -> None:
    grey = thumbnails.make(_jpeg(800, 1200, mode="L"), 160)
    out = io.BytesIO()
    Image.new("RGBA", (800, 1200), (1, 2, 3, 4)).save(out, format="PNG")
    png = thumbnails.make(out.getvalue(), 160)
    for data in (grey, png):
        with Image.open(io.BytesIO(data)) as image:
            assert (image.format, image.mode, image.width) == ("JPEG", "RGB", 160)


def test_a_cut_off_poster_is_refused_even_when_the_engine_tolerates_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    whole = _jpeg(800, 1200)
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)  # as the engine sets it
    with pytest.raises(thumbnails.ThumbnailError):
        thumbnails.make(whole[: len(whole) // 2], 320)
    assert ImageFile.LOAD_TRUNCATED_IMAGES is True  # put back
    with pytest.raises(thumbnails.ThumbnailError):
        thumbnails.make(b"\xff\xd8 not a poster", 320)


def test_kept_under_the_posters_hash_and_read_back(data_dir: Path) -> None:
    sha = "a" * 64
    assert thumbnails.cached(sha, 320) is None
    thumbnails.store(sha, 320, b"thumb")
    path = data_dir / "cache" / "thumbs" / f"{sha}-320.jpg"
    assert path.read_bytes() == b"thumb" and thumbnails.cached(sha, 320) == b"thumb"
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no temporary left
    thumbnails.store(sha, 320, b"again")  # a second writer replaces it whole
    assert thumbnails.cached(sha, 320) == b"again"


@pytest.mark.parametrize(("sha", "width"), [("a" * 64, 500), ("A" * 64, 320), ("../x", 320)])
def test_only_known_widths_and_hashes_have_a_place(sha: str, width: int) -> None:
    with pytest.raises(ValueError):
        thumbnails.cache_path(sha, width)
    with pytest.raises(ValueError):
        thumbnails.make(_jpeg(600, 900), 500)
