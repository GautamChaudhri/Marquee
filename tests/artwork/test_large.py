"""Large copies of ranked posters: named after the TMDB file, never enlarged, refused when
they do not decode, fetched best effort, and kept in the run's folder."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageFile

from marquee.artwork import large
from marquee.config import settings

RUN = "00000007-alpha"


def _image(width: int, height: int, fmt: str = "JPEG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (40, 90, 160)).save(out, format=fmt, quality=95)
    return out.getvalue()


def _size(data: bytes) -> tuple[int, int, str | None]:
    with Image.open(io.BytesIO(data)) as image:
        return image.width, image.height, image.format


@pytest.fixture(autouse=True)
def runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    (settings.runs_dir / RUN).mkdir(parents=True)
    return settings.runs_dir


def _client(files: dict[str, tuple[int, bytes, str]]) -> httpx.AsyncClient:
    """TMDB's image host: each file name answers with its status, body, and content type."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        seen.append(str(request.url))
        status, content, content_type = files.get(name, (404, b"", "text/plain"))
        return httpx.Response(status, content=content, headers={"content-type": content_type})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.seen = seen  # type: ignore[attr-defined]
    return client


def test_a_copy_is_named_after_the_tmdb_file() -> None:
    assert large.large_name("abc.jpg") == "abc.jpg"
    assert large.large_name("abc.png") == "abc.jpg"
    path = large.large_path(RUN, "abc.png")
    assert path == settings.runs_dir / RUN / "large" / "abc.jpg"


@pytest.mark.parametrize("name", ["", ".", "..", "a/b.jpg", "../b.jpg", ".jpg", "a\0b.jpg"])
def test_only_a_plain_file_name_names_a_copy(name: str) -> None:
    with pytest.raises(large.LargeCopyError):
        large.large_name(name)


def test_a_wide_poster_is_scaled_down_and_a_narrow_one_is_left_alone() -> None:
    wide = large.make_large(_image(2000, 3000))
    assert _size(wide) == (1500, 2250, "JPEG")

    narrow = _image(1000, 1500)
    assert large.make_large(narrow) is narrow

    assert _size(large.make_large(_image(800, 1200, "PNG"))) == (800, 1200, "JPEG")
    assert _size(large.make_large(_image(2000, 3000), 600)) == (600, 900, "JPEG")


def test_bytes_that_do_not_decode_are_refused_even_cut_off_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The engine makes Pillow accept a cut-off file for the whole process.
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    whole = _image(2000, 3000)
    with pytest.raises(large.LargeCopyError):
        large.make_large(whole[: len(whole) // 2])
    with pytest.raises(large.LargeCopyError):
        large.make_large(b"not an image")
    assert ImageFile.LOAD_TRUNCATED_IMAGES is True


async def test_fetching_keeps_one_copy_per_ranked_poster() -> None:
    client = _client(
        {
            "a.jpg": (200, _image(2000, 3000), "image/jpeg"),
            "b.jpg": (200, _image(1000, 1500), "image/jpeg"),
        }
    )

    result = await large.fetch_large(RUN, ["a.jpg", "b.png", "a.jpg"], client=client)

    assert result.saved == ["a.jpg", "b.png"] and result.failed == {}
    assert client.seen == [  # type: ignore[attr-defined]
        "https://image.tmdb.org/t/p/original/a.jpg",
        "https://image.tmdb.org/t/p/original/b.jpg",
    ]
    folder = settings.runs_dir / RUN / "large"
    assert sorted(path.name for path in folder.iterdir()) == ["a.jpg", "b.jpg"]
    assert _size((folder / "a.jpg").read_bytes()) == (1500, 2250, "JPEG")
    assert large.available(RUN) == {"a.jpg", "b.jpg"}


async def test_a_copy_already_kept_is_not_fetched_again() -> None:
    kept = settings.runs_dir / RUN / "large" / "a.jpg"
    kept.parent.mkdir()
    kept.write_bytes(b"kept")
    client = _client({"b.jpg": (200, _image(1000, 1500), "image/jpeg")})

    result = await large.fetch_large(RUN, ["a.jpg", "b.jpg"], client=client)

    assert sorted(result.saved) == ["a.jpg", "b.jpg"]
    assert client.seen == ["https://image.tmdb.org/t/p/original/b.jpg"]  # type: ignore[attr-defined]
    assert kept.read_bytes() == b"kept"


async def test_one_posters_failure_leaves_the_others() -> None:
    client = _client(
        {
            "good.jpg": (200, _image(1000, 1500), "image/jpeg"),
            "page.jpg": (200, b"<html>", "text/html"),
            "png.jpg": (200, _image(1000, 1500, "PNG"), "image/png"),
            "down.jpg": (503, b"", "text/plain"),
        }
    )

    result = await large.fetch_large(
        RUN, ["good.jpg", "page.jpg", "png.jpg", "down.jpg", "gone.jpg", "a/b.jpg"], client=client
    )

    assert result.saved == ["good.jpg"]
    assert set(result.failed) == {"page.jpg", "png.jpg", "down.jpg", "gone.jpg", "a/b.jpg"}
    assert large.available(RUN) == {"good.jpg"}
    # Nothing half written is left beside the copy.
    assert [p.name for p in (settings.runs_dir / RUN / "large").iterdir()] == ["good.jpg"]


async def test_what_is_not_fetched_in_time_is_left_out() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    result = await large.fetch_large(RUN, ["a.jpg"], client=client, time_limit_s=0.5)
    assert result.saved == [] and "a.jpg" in result.failed


def test_a_run_without_copies_has_none_available(runs_dir: Path) -> None:
    assert large.available(RUN) == frozenset()
    assert large.available("00000099-missing") == frozenset()
    assert large.available("../outside") == frozenset()
    folder = runs_dir / RUN / "large"
    folder.mkdir()
    (folder / "real.jpg").write_bytes(b"x")
    (folder / "notes.txt").write_bytes(b"x")
    (folder / "linked.jpg").symlink_to(folder / "real.jpg")
    assert large.available(RUN) == {"real.jpg"}
