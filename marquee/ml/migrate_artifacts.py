"""Legacy .npz artifact migration entrypoint."""

from __future__ import annotations

from pathlib import Path

from marquee.config import settings
from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.artifact_codec import ArtifactKind, ArtifactMigrationError, ensure_safe_artifact


def live_artifact_targets() -> list[tuple[Path, ArtifactKind]]:
    cache_root = settings.poster_cache_path.parent
    targets: list[tuple[Path, ArtifactKind]] = [
        (Path(pipeline_settings.TASTE_PROFILE_PATH), "taste_profile"),
        (Path(pipeline_settings.LEARNED_HEAD_PATH), "learned_head"),
        (Path(pipeline_settings.ZEROSHOT_AXES_PATH), "zeroshot_axes"),
    ]
    for artifact in sorted(cache_root.glob("taste_map*.npz")):
        if artifact.parent.name == "taste_map_history":
            continue
        targets.append((artifact, "taste_map"))
    return targets


def migrate_live_artifacts() -> list[str]:
    messages: list[str] = []
    for path, kind in live_artifact_targets():
        if not path.exists():
            continue
        try:
            result = ensure_safe_artifact(path, kind)
        except ArtifactMigrationError:
            if kind == "taste_map":
                continue
            raise
        if result.migrated:
            messages.append(f"{kind}: {path}")
    return messages


def main() -> None:
    migrated = migrate_live_artifacts()
    if not migrated:
        print("[INFO] No legacy .npz artifacts needed migration.")
        return
    for item in migrated:
        print(f"[INFO] Migrated {item}")


if __name__ == "__main__":
    main()
