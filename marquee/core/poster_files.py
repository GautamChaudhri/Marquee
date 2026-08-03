"""Pure poster filename and provider-reference helpers."""

from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

from marquee.core.path_utils import PathValidationError

_TMDB_ORIGINAL = "https://image.tmdb.org/t/p/original"


def sanitize_poster_filename(name: str) -> str:
    """Validate one UI-configurable poster basename as a safe JPEG filename."""
    candidate = (name or "").strip()
    if not candidate or "\x00" in candidate:
        raise PathValidationError(f"Invalid poster filename: {name!r}")
    if any(ord(char) < 0x20 for char in candidate):
        raise PathValidationError("Poster filename cannot contain control characters")
    if "/" in candidate or "\\" in candidate:
        raise PathValidationError(f"Poster filename must not contain path separators: {name!r}")
    if candidate.startswith(".") or Path(candidate).name != candidate:
        raise PathValidationError(f"Poster filename must be a bare filename: {name!r}")
    suffix = Path(candidate).suffix.lower()
    if not suffix:
        candidate += ".jpg"
    elif suffix not in (".jpg", ".jpeg"):
        raise PathValidationError("Poster filename must use a .jpg or .jpeg extension")
    if len(candidate.encode("utf-8")) > 255:
        raise PathValidationError("Poster filename exceeds the 255-byte filesystem limit")
    return candidate


def verify_jpeg_file(fileobj: BinaryIO) -> None:
    """Verify JPEG content from an already-open descriptor without trusting its suffix."""

    try:
        fileobj.seek(0)
        with Image.open(fileobj) as image:
            if image.format != "JPEG":
                raise PathValidationError("Poster content is not a JPEG image")
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise PathValidationError("Poster content is not a valid JPEG image") from exc
    finally:
        fileobj.seek(0)


def is_jpeg_path(path: Path) -> bool:
    try:
        with path.open("rb") as fileobj:
            verify_jpeg_file(fileobj)
    except (OSError, PathValidationError):
        return False
    return True


def tmdb_original_url(orig_filename: str) -> str:
    """Reconstruct the TMDB original-size URL from a provider filename."""
    return f"{_TMDB_ORIGINAL}/{orig_filename.lstrip('/')}"
