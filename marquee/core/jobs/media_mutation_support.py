"""Shared confinement and probe helpers for JMC5B media mutations.

Both the track-mutation and sidecar leaves need the same confined boundary,
authoritative probe, and media-file resolution, so they live here rather than
being reached for across handler modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from marquee.core.filesystem import ClassifiedPath, FilesystemBoundary, RootSpec
from marquee.core.jobs.track_inventory_adapter import inventory_from_probe
from marquee.core.jobs.track_selectors import TrackInventoryV1
from marquee.core.media_files import compute_signature, resolve_media_file
from marquee.core.subtitles.probe import probe_container

if TYPE_CHECKING:
    from marquee.core.jobs.delivery import ExecutionContext

#: Only Matroska can carry the full track/metadata model these leaves preserve.
SUPPORTED_CONTAINERS = ("matroska", "webm")


class TrackMutationError(RuntimeError):
    """A media mutation could not start; nothing was published."""


def data_dir() -> Path:
    from marquee.config import settings

    return Path(settings.DATA_DIR)


def physical(classified: ClassifiedPath) -> Path:
    """Resolve a confined key to its on-disk path for tool arguments."""
    return classified.root.resolved() / classified.key.value


def confined_boundary(source: Path) -> tuple[FilesystemBoundary, ClassifiedPath]:
    """Confine the source and stage candidates beside it on the same filesystem."""
    boundary = FilesystemBoundary(
        {
            "media": RootSpec(
                name="media",
                path=source.parent,
                purpose="source media and same-filesystem staging",
                access="read_write",
                allow_symlinks=False,
                same_filesystem=True,
            ),
            "data": RootSpec(
                name="data",
                path=data_dir(),
                purpose="canonical media backups and managed sidecars",
                access="read_write",
                allow_symlinks=False,
            ),
        }
    )
    return boundary, boundary.from_key("media", source.name)


def probe_inventory(path: Path, signature: str) -> TrackInventoryV1:
    """The authoritative typed inventory for before/actual state (B13)."""
    result = probe_container(path)
    if result is None:
        raise TrackMutationError("the source media could not be probed")
    return inventory_from_probe(
        signature=signature,
        audio_streams=result.audio_streams,
        subtitles=result.subtitles,
        container=result.container,
    )


def current_signature(path: Path) -> str:
    """Recompute the live media signature after coordinator publication."""
    stat = path.stat()
    return compute_signature(path, size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def container_supported(inventory: TrackInventoryV1) -> bool:
    container = (inventory.container or "").lower()
    return any(name in container for name in SUPPORTED_CONTAINERS)


async def load_media_file(context: ExecutionContext, media_file_id: int):
    async with context.session_factory() as session:
        return await resolve_media_file(session, media_file_id)
