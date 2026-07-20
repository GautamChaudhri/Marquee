"""Read-only eligibility helpers for letterbox previews and planning."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from marquee.core.path_utils import PathValidationError, safe_translate_and_validate
from marquee.media import binaries
from marquee.models import Episode, Movie


@dataclass(frozen=True)
class LetterboxEligibility:
    eligible: bool
    reason: str | None
    path: Path | None


def resolve_movie_media_path(movie: Movie) -> Path:
    """Resolve one movie file without permitting a folder escape."""
    if not movie.movie_file_path:
        raise PathValidationError("Movie has no media file path (run sync)")
    folder = safe_translate_and_validate(movie.folder_path, source="radarr")
    candidate = (folder / movie.movie_file_path).resolve()
    if not candidate.is_relative_to(folder):
        raise PathValidationError(f"Media file escapes its folder: {candidate}")
    return candidate


def resolve_episode_media_path(episodes: list[Episode]) -> Path:
    """Resolve the one physical file shared by an episode group."""
    if not episodes:
        raise PathValidationError("Episode set is empty")
    resolved: Path | None = None
    for episode in episodes:
        if not episode.episode_file_path:
            raise PathValidationError(f"Episode {episode.id} has no media file path (run sync)")
        candidate = safe_translate_and_validate(episode.episode_file_path, source="sonarr")
        if resolved is None:
            resolved = candidate
        elif candidate != resolved:
            raise PathValidationError("Episode set does not share one media file")
    if resolved is None:
        raise PathValidationError("Episode set is empty")
    return resolved


def check_path_eligibility(path: Path) -> LetterboxEligibility:
    """Validate a resolved file for read-only letterbox planning."""
    if path.suffix.lower() != ".mkv":
        return LetterboxEligibility(False, "not_mkv", path)
    if not path.is_file():
        return LetterboxEligibility(False, "missing", path)
    if not os.access(path, os.W_OK):
        return LetterboxEligibility(False, "read_only", path)
    if binaries.resolve("mkvmerge") is not None:
        result = binaries.run("mkvmerge", ["-J", binaries.safe_media_path(path)], timeout=30.0)
        if result.ok:
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                data = {}
            container = (data.get("container", {}).get("type") or "").lower()
            has_video = any(track.get("type") == "video" for track in data.get("tracks", []))
            if "matroska" not in container and container:
                return LetterboxEligibility(False, "not_matroska", path)
            if not has_video:
                return LetterboxEligibility(False, "no_video_track", path)
    return LetterboxEligibility(True, None, path)


def check_movie_eligibility(movie: Movie) -> LetterboxEligibility:
    try:
        path = resolve_movie_media_path(movie)
    except PathValidationError as exc:
        return LetterboxEligibility(False, f"path_invalid: {exc}", None)
    return check_path_eligibility(path)


def check_episode_group_eligibility(episodes: list[Episode]) -> LetterboxEligibility:
    try:
        path = resolve_episode_media_path(episodes)
    except PathValidationError as exc:
        return LetterboxEligibility(False, f"path_invalid: {exc}", None)
    return check_path_eligibility(path)
