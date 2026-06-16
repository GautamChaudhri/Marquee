"""Container mutation adapters (design §23.4).

``MatroskaAdapter`` (mkvmerge) for MKV writes, ``Mp4Adapter`` (ffmpeg) for
MP4/MOV. Adapters only *build* argument lists; the job worker executes them via
``asyncio.create_subprocess_exec`` (never a shell). Other containers are
read-only.
"""

from __future__ import annotations

from marquee.core.subtitles.adapters.base import MutationAdapter, UnsupportedContainerError
from marquee.core.subtitles.adapters.matroska import MatroskaAdapter
from marquee.core.subtitles.adapters.mp4 import Mp4Adapter

_ADAPTERS: dict[str, MutationAdapter] = {
    "mkv": MatroskaAdapter(),
    "mp4": Mp4Adapter(),
}


def adapter_for(container_family: str) -> MutationAdapter:
    """Return the mutation adapter for a container family, or raise."""
    adapter = _ADAPTERS.get(container_family)
    if adapter is None:
        raise UnsupportedContainerError(
            f"Container {container_family!r} is read-only for subtitle mutation"
        )
    return adapter


__all__ = ["MatroskaAdapter", "Mp4Adapter", "UnsupportedContainerError", "adapter_for"]
