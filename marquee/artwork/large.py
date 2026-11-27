"""Large copies of a run's ranked posters, for looking at one closely in the web app.

The pipeline works on TMDB's 500 px files, which is all the engine ever reads. When a run
has ranked its candidates, each ranked one is fetched once more at TMDB's original size,
scaled down to at most :data:`LARGE_WIDTH` pixels wide (never enlarged), and kept as
``<run folder>/large/<TMDB file stem>.jpg``. The copies are for the eye only: nothing ranks
with them, and a deploy still fetches its own original and writes those bytes untouched.

It is best effort. A poster TMDB will not send, or one that does not decode, is left out
and the web app shows its 500 px file; a run never fails over a large copy. The copies live
in the run's folder, so they go when the run's folder goes.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import httpx
from PIL import Image, ImageOps

from marquee.artwork import deploy, runs

logger = logging.getLogger(__name__)

LARGE_DIR = "large"
LARGE_WIDTH = 1500
JPEG_QUALITY = 85
CONCURRENCY = 4
TIME_LIMIT_S = 180.0
"""For one run's copies together; what is not fetched by then is left out."""
MEDIA_TYPE = "image/jpeg"


class LargeCopyError(Exception):
    """A large copy cannot be made or named; the message says why."""


@dataclass(frozen=True)
class LargeCopies:
    """What fetching one run's large copies did."""

    saved: list[str] = field(default_factory=list)
    """TMDB file names whose copy is in the folder now, kept ones included."""
    failed: dict[str, str] = field(default_factory=dict)
    """TMDB file name -> why it has no copy."""


def large_name(source_file: str) -> str:
    """The file name of a candidate's large copy: its TMDB stem with ``.jpg``."""
    name = PurePosixPath(source_file).name
    stem = PurePosixPath(name).stem
    # A leading dot is refused too: the folder's own temporary files start with one.
    if not name or name != source_file or name.startswith(".") or not stem or "\0" in name:
        raise LargeCopyError(f"{source_file!r} is not a TMDB file name")
    return f"{stem}.jpg"


def large_dir(run_folder: str, runs_dir: Path | None = None) -> Path:
    return runs.run_dir(run_folder, runs_dir) / LARGE_DIR


def large_path(run_folder: str, source_file: str, runs_dir: Path | None = None) -> Path:
    """Where a candidate's large copy is kept, whether or not it is there."""
    return large_dir(run_folder, runs_dir) / large_name(source_file)


def available(run_folder: str, runs_dir: Path | None = None) -> frozenset[str]:
    """The names of the large copies a run's folder holds. Blocking; none when it has no
    such folder."""
    try:
        with os.scandir(large_dir(run_folder, runs_dir)) as entries:
            return frozenset(
                entry.name
                for entry in entries
                if entry.name.endswith(".jpg") and entry.is_file(follow_symlinks=False)
            )
    except (OSError, runs.RunRecordError):
        return frozenset()


def make_large(data: bytes, width: int = LARGE_WIDTH) -> bytes:
    """A JPEG of the poster in ``data`` at most ``width`` pixels wide. Blocking.

    A JPEG that is already narrow enough is returned as it is, so nothing is encoded twice
    without need. Raises :class:`LargeCopyError` when the bytes do not decode.
    """
    try:
        with deploy.strict_decoding(), warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format == "JPEG" and image.width <= width:
                    image.load()
                    return data
                target_height = max(1, round(image.height * min(width, image.width) / image.width))
                image.draft("RGB", (min(width, image.width), target_height))
                image.load()
                upright = ImageOps.exif_transpose(image)
                if upright.mode != "RGB":
                    upright = upright.convert("RGB")
                if upright.width > width:
                    upright = upright.resize(
                        (width, max(1, round(upright.height * width / upright.width))),
                        Image.Resampling.LANCZOS,
                    )
                out = io.BytesIO()
                upright.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise LargeCopyError(f"the poster does not decode as an image: {exc}") from exc
    return out.getvalue()


def store(target: Path, data: bytes) -> None:
    """Keep a copy: written under a temporary name in the same folder, then renamed, so a
    reader never sees half a file. Blocking."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


async def _one(
    target: Path, source_file: str, client: httpx.AsyncClient, gate: asyncio.Semaphore, width: int
) -> None:
    async with gate:
        data = await deploy.fetch_original(source_file, client)
        await asyncio.to_thread(store, target, await asyncio.to_thread(make_large, data, width))


async def fetch_large(
    run_folder: str,
    source_files: list[str],
    *,
    client: httpx.AsyncClient | None = None,
    width: int = LARGE_WIDTH,
    time_limit_s: float = TIME_LIMIT_S,
    runs_dir: Path | None = None,
) -> LargeCopies:
    """Fetch and keep the large copy of each named candidate of a run, best first.

    A copy already in the folder is kept as it is, so a second call fetches only what is
    missing. One poster's failure never stops the others, and nothing is raised for one:
    the result names what was saved and why the rest was not.
    """
    result = LargeCopies()
    wanted: dict[str, Path] = {}
    for source_file in dict.fromkeys(source_files):
        try:
            target = large_path(run_folder, source_file, runs_dir)
        except (LargeCopyError, runs.RunRecordError) as exc:
            result.failed[source_file] = str(exc)
            continue
        if await asyncio.to_thread(target.is_file):
            result.saved.append(source_file)
        else:
            wanted[source_file] = target
    if not wanted:
        return result
    own = client is None
    client = client or httpx.AsyncClient(timeout=deploy.FETCH_TIMEOUT_S)
    gate = asyncio.Semaphore(CONCURRENCY)
    tasks = {
        asyncio.create_task(_one(target, source_file, client, gate, width)): source_file
        for source_file, target in wanted.items()
    }
    try:
        _, late = await asyncio.wait(tasks, timeout=time_limit_s)
        for task in late:
            task.cancel()
        await asyncio.gather(*late, return_exceptions=True)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        if own:
            await client.aclose()
    for task, source_file in tasks.items():
        if task.cancelled():
            result.failed[source_file] = f"not fetched within {time_limit_s:.0f} s"
        elif (error := task.exception()) is None:
            result.saved.append(source_file)
        elif isinstance(error, httpx.HTTPError | deploy.DeployError | LargeCopyError | OSError):
            result.failed[source_file] = f"{type(error).__name__}: {error}"
        else:
            raise error
    return result
