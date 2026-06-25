"""Poster-pipeline cache inspection + clearing.

The "poster pipeline cache" is the *downloaded poster candidates* and the
derived working artifacts — never the learned-head / taste data. This module
clears only an explicit allow-list of directories and refuses to touch anything
that holds training state or the deployed-poster cache, so the UI's "Clear
cache" button can never wipe labels, exemplars, the taste profile, the learned
head, or the live posters on the media volume.

Cleared (contents only, the directory itself is kept):
  * ``data/runs/work``      — per-movie working trees (downloads + stage rejects)
  * ``data/staging``        — transient candidate staging
  * ``data/cache/embeddings`` (optional) — CLIP/DINO embedding cache (re-derived)
  * ``data/runs/archive``   (optional) — archived run JSON (past results history)

Never touched: ``data/feedback`` (labels), ``data/training`` (+ negative),
``data/ml`` (taste profile, learned head, zero-shot axes), and
``data/cache/posters`` (deployed-poster cache used for restore/self-heal).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from marquee.config import settings
from marquee.core.pipeline_config import pipeline_settings

logger = logging.getLogger(__name__)


def _protected_dirs() -> list[Path]:
    """Directories that must never be cleared by this module."""
    return [
        Path(pipeline_settings.FEEDBACK_LABELS_PATH).parent,  # data/feedback
        Path(pipeline_settings.TRAINING_DATA_DIR),  # data/training/positive
        Path(pipeline_settings.NEGATIVE_DATA_DIR),  # data/training/negative
        Path(pipeline_settings.TASTE_PROFILE_PATH).parent,  # data/ml
        settings.poster_cache_path,  # data/cache/posters
    ]


def _assert_clearable(target: Path) -> Path:
    """Guard: target must live under data/ and not hold any training state."""
    target = target.resolve()
    data_dir = settings.data_dir_path.resolve()
    if target == data_dir or data_dir not in target.parents:
        raise ValueError(f"refusing to clear path outside the data dir: {target}")
    for protected in _protected_dirs():
        protected = protected.resolve()
        if target == protected or protected in target.parents or target in protected.parents:
            raise ValueError(f"refusing to clear protected path: {target} (vs {protected})")
    return target


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _clear_contents(path: Path) -> int:
    """Remove everything *inside* ``path`` (keep the directory). Returns bytes freed."""
    if not path.exists():
        return 0
    freed = _dir_size(path)
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    return freed


def _targets(*, include_embeddings: bool, include_archives: bool) -> dict[str, Path]:
    targets: dict[str, Path] = {
        "runs_work": settings.runs_work_path,
        "staging": settings.poster_staging_path,
    }
    if include_embeddings:
        targets["embeddings"] = Path(pipeline_settings.EMBEDDING_CACHE_DIR)
    if include_archives:
        targets["archives"] = settings.runs_archive_path
    return targets


def cache_sizes() -> dict[str, object]:
    """Report on-disk size of each pipeline cache (for the Clear button)."""
    sizes = {
        "runs_work": _dir_size(settings.runs_work_path),
        "staging": _dir_size(settings.poster_staging_path),
        "embeddings": _dir_size(Path(pipeline_settings.EMBEDDING_CACHE_DIR)),
        "archives": _dir_size(settings.runs_archive_path),
    }
    # Clearable by default = work + staging + embeddings (archives are opt-in).
    clearable = sizes["runs_work"] + sizes["staging"] + sizes["embeddings"]
    return {"sizes_bytes": sizes, "clearable_bytes": clearable, "total_bytes": sum(sizes.values())}


def clear_pipeline_cache(
    *, include_embeddings: bool = True, include_archives: bool = False
) -> dict[str, object]:
    """Clear the poster-pipeline cache. Returns per-target bytes freed."""
    freed: dict[str, int] = {}
    for name, path in _targets(
        include_embeddings=include_embeddings, include_archives=include_archives
    ).items():
        _assert_clearable(path)
        freed[name] = _clear_contents(path)
    total = sum(freed.values())
    logger.info("PIPELINE CACHE CLEARED | freed=%d bytes | %s", total, freed)
    return {
        "cleared": list(freed),
        "freed_bytes": freed,
        "total_freed_bytes": total,
        "include_embeddings": include_embeddings,
        "include_archives": include_archives,
    }
