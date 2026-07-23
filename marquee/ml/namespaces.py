from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from marquee.core.pipeline_config import pipeline_settings


@dataclass(frozen=True)
class TasteNamespace:
    library: str  # "movies" | "tv"
    profile_path: Path
    map_history_dir: Path
    artifact_kind_profile: str  # "taste_profile" | "taste_profile_tv"


def _movies_namespace() -> TasteNamespace:
    return TasteNamespace(
        library="movies",
        profile_path=Path(pipeline_settings.TASTE_PROFILE_PATH),
        map_history_dir=Path(pipeline_settings.TASTE_PROFILE_PATH).parent / "taste_map_history",
        artifact_kind_profile="taste_profile",
    )


def _tv_namespace() -> TasteNamespace:
    return TasteNamespace(
        library="tv",
        profile_path=Path(pipeline_settings.TASTE_PROFILE_TV_PATH),
        map_history_dir=Path(pipeline_settings.TASTE_PROFILE_TV_PATH).parent / "taste_map_history_tv",
        artifact_kind_profile="taste_profile_tv",
    )


def get_namespace(library: str) -> TasteNamespace:
    """Resolve a namespace fresh from current pipeline_settings.

    Always builds a new ``TasteNamespace`` from the live ``pipeline_settings``
    singleton rather than caching one at import time, so overrides (env vars,
    the settings API, or test monkeypatches) are always reflected.
    """
    if library == "movies":
        return _movies_namespace()
    elif library == "tv":
        return _tv_namespace()
    else:
        raise ValueError(f"Unknown taste library namespace: {library!r}")
