"""Retired letterbox-preview cache cleanup.

Preview rendering is owned by the ``letterbox_preview`` job definition.  This
module deliberately retains only removal of cache files created by releases
before that canonical execution path existed.
"""

from __future__ import annotations

import logging
import re

from marquee.config import settings

logger = logging.getLogger(__name__)

_SUBJECT_KEY = re.compile(r"(?:movie|episode)-[1-9][0-9]*\Z")


def movie_subject_key(movie_id: int) -> str:
    if movie_id < 1:
        raise ValueError("movie id must be positive")
    return f"movie-{movie_id}"


def episode_subject_key(episode_id: int) -> str:
    if episode_id < 1:
        raise ValueError("episode id must be positive")
    return f"episode-{episode_id}"


def _coerce_subject_key(subject_key: str | int) -> str:
    normalized = movie_subject_key(subject_key) if isinstance(subject_key, int) else subject_key
    if not _SUBJECT_KEY.fullmatch(normalized):
        raise ValueError("preview subject key is invalid")
    return normalized


def _purge_glob(pattern: str) -> int:
    root = settings.letterbox_preview_path
    if not root.exists():
        return 0
    removed = 0
    for path in root.glob(pattern):
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            logger.warning("retired preview purge failed for %s: %s", path.name, exc)
    return removed


def purge_previews(subject_key: str | int) -> int:
    """Remove retired cached frames for one movie or episode."""
    return _purge_glob(f"{_coerce_subject_key(subject_key)}_*.webp")


def purge_movie_previews(movie_id: int) -> int:
    """Remove canonical and pre-subject-key cache names for a movie."""
    normalized = movie_subject_key(movie_id)
    return _purge_glob(f"{normalized}_*.webp") + _purge_glob(f"{movie_id}_*.webp")


def purge_all_episode_previews() -> int:
    """Remove retired cached frames for every episode."""
    return _purge_glob("episode-*_*.webp")
