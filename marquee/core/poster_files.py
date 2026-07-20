"""Pure poster filename and provider-reference helpers."""

from pathlib import Path

from marquee.core.path_utils import PathValidationError

_TMDB_ORIGINAL = "https://image.tmdb.org/t/p/original"


def sanitize_poster_filename(name: str) -> str:
    """Validate one UI-configurable poster basename as a safe JPEG filename."""
    candidate = (name or "").strip()
    if not candidate or "\x00" in candidate:
        raise PathValidationError(f"Invalid poster filename: {name!r}")
    if "/" in candidate or "\\" in candidate:
        raise PathValidationError(f"Poster filename must not contain path separators: {name!r}")
    if candidate.startswith(".") or Path(candidate).name != candidate:
        raise PathValidationError(f"Poster filename must be a bare filename: {name!r}")
    if Path(candidate).suffix.lower() not in (".jpg", ".jpeg"):
        candidate += ".jpg"
    return candidate


def tmdb_original_url(orig_filename: str) -> str:
    """Reconstruct the TMDB original-size URL from a provider filename."""
    return f"{_TMDB_ORIGINAL}/{orig_filename.lstrip('/')}"
