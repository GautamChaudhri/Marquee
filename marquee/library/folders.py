"""Read-only scan of the movie library folders.

The layout on disk is ``<root>/<quality folder>/<Title (Year)>/<files>``. Every visible
sub-folder of a root is a quality folder (``1080p``, ``4K``, or anything else), and every
visible sub-folder of a quality folder is one movie. Entries whose name starts with ``.`` are
skipped at every level: the roots hold app state folders that are not titles. Nothing below a
title folder is read.

This module only lists directories and stats entries. It opens no file and writes nothing.
The roots usually live on a network share that mounts on demand, so a root that is missing,
is not a directory, or shows no visible sub-folder raises :class:`LibraryRootError` instead
of looking like an empty library.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

POSTER_NAME = "poster.jpg"
VIDEO_EXTENSIONS = frozenset(
    {".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ts", ".webm", ".wmv"}
)
IMAGE_EXTENSIONS = frozenset({".jpeg", ".jpg", ".png", ".webp"})

_TITLE_YEAR = re.compile(r"^(?P<title>.*?)\s*\((?P<year>\d{4})\)$")
_IMDB_ID = re.compile(r"\{imdbid-(?P<imdb_id>tt\d+)\}")


class LibraryRootError(Exception):
    """A movie root cannot be scanned: missing, not a directory, unreadable, or empty."""


@dataclass(frozen=True)
class MovieFolder:
    """One title folder, as found on disk."""

    path: Path
    quality: str
    name: str
    title: str
    year: int | None
    imdb_id: str | None
    poster: Path | None
    videos: tuple[Path, ...]
    images: tuple[Path, ...]
    """Image files in the folder other than ``poster.jpg``."""
    file_named_images: tuple[Path, ...]
    """The subset of ``images`` whose stem equals a video file's stem."""

    @property
    def has_file_named_image(self) -> bool:
        return bool(self.file_named_images)


def split_title_year(name: str) -> tuple[str, int | None]:
    """``"Die Hard (1988)"`` -> ``("Die Hard", 1988)``; no trailing year -> ``(name, None)``."""
    match = _TITLE_YEAR.match(name.strip())
    if match is None or not match.group("title"):
        return name.strip(), None
    return match.group("title"), int(match.group("year"))


def imdb_id_from_name(name: str) -> str | None:
    """The IMDb id in a Radarr-style file name (``… {imdbid-tt0095016} …``), if any."""
    match = _IMDB_ID.search(name)
    return match.group("imdb_id") if match else None


def _visible_entries(path: Path) -> list[os.DirEntry[str]]:
    with os.scandir(path) as entries:
        return sorted(
            (entry for entry in entries if not entry.name.startswith(".")),
            key=lambda entry: entry.name,
        )


def _visible_subfolders(path: Path) -> list[Path]:
    return [Path(entry.path) for entry in _visible_entries(path) if entry.is_dir()]


def read_movie_folder(path: Path, quality: str) -> MovieFolder:
    """Describe one title folder from a listing of its top level."""
    files = [Path(entry.path) for entry in _visible_entries(path) if entry.is_file()]
    videos = tuple(f for f in files if f.suffix.lower() in VIDEO_EXTENSIONS)
    poster = next((f for f in files if f.name == POSTER_NAME), None)
    images = tuple(
        f for f in files if f.suffix.lower() in IMAGE_EXTENSIONS and f.name != POSTER_NAME
    )
    video_stems = {video.stem for video in videos}
    file_named = tuple(image for image in images if image.stem in video_stems)

    imdb_ids = sorted({i for i in (imdb_id_from_name(v.name) for v in videos) if i})
    imdb_id = imdb_ids[0] if len(imdb_ids) == 1 else None
    if len(imdb_ids) > 1:
        logger.warning("LIBRARY | %s: video files disagree on the IMDb id %s", path, imdb_ids)

    title, year = split_title_year(path.name)
    return MovieFolder(
        path=path,
        quality=quality,
        name=path.name,
        title=title,
        year=year,
        imdb_id=imdb_id,
        poster=poster,
        videos=videos,
        images=images,
        file_named_images=file_named,
    )


def scan_root(root: Path) -> list[MovieFolder]:
    """Every title folder under one movie root, sorted by quality folder, then name."""
    root = Path(root)
    if not root.exists():
        raise LibraryRootError(f"movie root {root} does not exist (is the share mounted?)")
    if not root.is_dir():
        raise LibraryRootError(f"movie root {root} is not a directory")
    try:
        quality_folders = _visible_subfolders(root)
    except OSError as exc:
        raise LibraryRootError(f"movie root {root} cannot be listed: {exc.strerror}") from exc
    if not quality_folders:
        raise LibraryRootError(
            f"movie root {root} has no visible sub-folder (is the share mounted?)"
        )

    folders: list[MovieFolder] = []
    for quality_folder in quality_folders:
        for title_folder in _visible_subfolders(quality_folder):
            folders.append(read_movie_folder(title_folder, quality_folder.name))
    return folders


def scan_movie_roots(roots: Sequence[Path]) -> list[MovieFolder]:
    """Every title folder under every movie root, in root order."""
    if not roots:
        raise LibraryRootError("no movie roots configured (set MOVIE_ROOTS)")
    folders: list[MovieFolder] = []
    for root in roots:
        folders.extend(scan_root(root))
    return folders
