"""Small copies of library posters for the web app, cached by the poster's content.

A thumbnail is made from the poster's bytes with Pillow at one of :data:`WIDTHS`, never
enlarged (a narrower poster keeps its own width), and encoded as a JPEG. It is kept as
``data/cache/thumbs/<poster sha256>-<width>.jpg``, written under a temporary name and
renamed, so a reader never sees half a file. The name holds the poster's hash, so a kept
copy is right for exactly as long as the poster is.

A poster that does not decode is an error, never a broken image: the engine makes Pillow
accept a cut-off file for the whole process, and :func:`make` turns that off while it decodes.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import stat
import tempfile
import warnings
from pathlib import Path

from PIL import Image, ImageOps

from marquee.artwork.deploy import strict_decoding
from marquee.config import settings

WIDTHS = (160, 320, 640)
JPEG_QUALITY = 85
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ThumbnailError(Exception):
    """The poster's bytes do not decode as an image; the message says why."""


def cache_path(sha256: str, width: int) -> Path:
    """Where the thumbnail of the poster with this hash, at this width, is kept."""
    if not _SHA256.fullmatch(sha256) or width not in WIDTHS:
        raise ValueError(f"no thumbnail for {sha256!r} at width {width!r}")
    return settings.thumbnail_cache_dir / f"{sha256}-{width}.jpg"


def cached(sha256: str, width: int) -> bytes | None:
    """The kept thumbnail, or ``None`` when there is none. Blocking."""
    path = cache_path(sha256, width)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        return path.read_bytes()
    except FileNotFoundError:
        return None


def make(data: bytes, width: int) -> bytes:
    """A JPEG of the poster in ``data``, ``width`` pixels wide or its own width if narrower.
    Blocking; raises :class:`ThumbnailError` when the bytes do not decode."""
    if width not in WIDTHS:
        raise ValueError(f"width must be one of {WIDTHS}")
    try:
        with strict_decoding(), warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                source_width, source_height = image.size
                target_width = min(width, source_width)
                target_height = max(1, round(source_height * target_width / source_width))
                # A JPEG can be decoded at 1/2, 1/4, or 1/8 scale; ask for the smallest
                # scale that is still at least the target, which is far quicker.
                image.draft("RGB", (target_width, target_height))
                image.load()
                upright = ImageOps.exif_transpose(image)
                if upright.mode != "RGB":
                    upright = upright.convert("RGB")
                if upright.width != target_width:
                    target_height = max(1, round(upright.height * target_width / upright.width))
                    upright = upright.resize(
                        (target_width, target_height), Image.Resampling.LANCZOS
                    )
                out = io.BytesIO()
                upright.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ThumbnailError(f"the poster does not decode as an image: {exc}") from exc
    return out.getvalue()


def store(sha256: str, width: int, data: bytes) -> None:
    """Keep a thumbnail: written under a temporary name in the same folder, then renamed.
    Blocking. Two requests making the same thumbnail at once both end with a whole file."""
    target = cache_path(sha256, width)
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
