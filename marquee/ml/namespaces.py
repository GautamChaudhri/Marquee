from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from marquee.core.pipeline_config import pipeline_settings


@dataclass(frozen=True)
class TasteNamespace:
    library: str  # "movies" | "tv"
    profile_path: Path
    head_path: Path
    training_dirs: dict[str, Path]  # asset_kind -> dir
    negative_dir: Path
    feedback_dir: Path  # labels/events root for this library
    map_history_dir: Path
    artifact_kind_profile: str  # "taste_profile" | "taste_profile_tv"
    artifact_kind_head: str  # "learned_head"  | "learned_head_tv"


def _movies_namespace() -> TasteNamespace:
    return TasteNamespace(
        library="movies",
        profile_path=Path(pipeline_settings.TASTE_PROFILE_PATH),
        head_path=Path(pipeline_settings.LEARNED_HEAD_PATH),
        training_dirs={"movie": Path(pipeline_settings.TRAINING_DATA_DIR)},
        negative_dir=Path(pipeline_settings.NEGATIVE_DATA_DIR),
        feedback_dir=Path(pipeline_settings.FEEDBACK_LABELS_PATH).parent,
        map_history_dir=Path(pipeline_settings.TASTE_PROFILE_PATH).parent / "taste_map_history",
        artifact_kind_profile="taste_profile",
        artifact_kind_head="learned_head",
    )


def _tv_namespace() -> TasteNamespace:
    return TasteNamespace(
        library="tv",
        profile_path=Path(pipeline_settings.TASTE_PROFILE_TV_PATH),
        head_path=Path(pipeline_settings.LEARNED_HEAD_TV_PATH),
        training_dirs={
            "show": Path(pipeline_settings.TV_TRAINING_SHOW_DIR),
            "season": Path(pipeline_settings.TV_TRAINING_SEASON_DIR),
        },
        negative_dir=Path(pipeline_settings.NEGATIVE_DATA_TV_DIR),
        feedback_dir=Path(pipeline_settings.FEEDBACK_LABELS_PATH).parent / "tv",
        map_history_dir=Path(pipeline_settings.TASTE_PROFILE_TV_PATH).parent / "taste_map_history_tv",
        artifact_kind_profile="taste_profile_tv",
        artifact_kind_head="learned_head_tv",
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
