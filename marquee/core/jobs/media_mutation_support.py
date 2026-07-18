"""Shared confinement and probe helpers for JMC5B media mutations.

Both the track-mutation and sidecar leaves need the same confined boundary,
authoritative probe, and media-file resolution, so they live here rather than
being reached for across handler modules.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from marquee.core.filesystem import ClassifiedPath, FilesystemBoundary, RootSpec
from marquee.core.jobs.track_inventory_adapter import inventory_from_probe
from marquee.core.jobs.track_selectors import TrackInventoryV1
from marquee.core.media_files import compute_signature, resolve_media_file
from marquee.core.subtitles.probe import probe_container
from marquee.media import binaries
from marquee.models.media_file import EpisodeMediaFile, MediaFile
from marquee.models.subtitle_inventory import SubtitleInventory, SubtitleTrack
from marquee.models.subtitle_managed import ManagedSubtitleBinding

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


async def _probe_json(
    context: ExecutionContext, tool: str, args: list[str]
) -> dict[str, object] | None:
    process = await context.process_launcher.launch(tool, args)
    summary = await process.wait()
    if summary.exit_code != 0:
        return None
    try:
        parsed = json.loads(summary.stdout.captured.decode("utf-8", "replace"))
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def probe_inventory(
    context: ExecutionContext, path: Path, signature: str
) -> TrackInventoryV1:
    """Probe through tracked containment and return the authoritative typed inventory."""
    probe_json = await _probe_json(
        context,
        "ffprobe",
        [
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            str(path),
        ],
    )
    mkvmerge_json = None
    if path.suffix.lower() == ".mkv":
        mkvmerge_json = await _probe_json(
            context, "mkvmerge", ["-J", binaries.safe_media_path(str(path))]
        )
    result = probe_container(path, probe_json=probe_json, mkvmerge_json=mkvmerge_json)
    if result is None:
        raise TrackMutationError("the source media could not be probed")
    return inventory_from_probe(
        signature=signature,
        audio_streams=result.audio_streams,
        subtitles=result.subtitles,
        container=result.container,
    )


async def persist_post_mutation_inventory(
    context: ExecutionContext,
    *,
    media_file_id: int,
    inventory: TrackInventoryV1,
) -> None:
    """Replace the UI inventory only while the producing attempt owns its fence."""
    async with context.session_factory() as session, session.begin():
        if not await context.writer.owns_current_attempt(session):
            raise TrackMutationError("the attempt lost ownership before inventory persistence")
        row = await session.scalar(
            select(SubtitleInventory)
            .where(SubtitleInventory.media_file_id == media_file_id)
            .with_for_update()
        )
        if row is None:
            row = SubtitleInventory(media_file_id=media_file_id)
            session.add(row)
            await session.flush()
        else:
            await session.execute(
                delete(SubtitleTrack).where(SubtitleTrack.inventory_id == row.id)
            )

        audio_streams: list[dict[str, object]] = []
        for entry in inventory.entries:
            facts = entry.facts
            if facts.kind == "audio":
                audio_streams.append(
                    {
                        "index": entry.stream_index,
                        "codec": facts.codec,
                        "channels": facts.channels,
                        "language": facts.language_tag,
                        "title": facts.title,
                        "default": facts.is_default,
                    }
                )
                continue
            if facts.kind != "subtitle":
                continue
            identity = sha256(
                f"{media_file_id}:{entry.track_key}".encode()
            ).hexdigest()[:32]
            codec = (facts.codec or "").lower()
            session.add(
                SubtitleTrack(
                    id=identity,
                    inventory_id=row.id,
                    source=facts.source,
                    stream_index=entry.stream_index,
                    tool_track_id=entry.tool_track_id,
                    external_path=facts.managed_key,
                    codec=facts.codec,
                    kind="bitmap" if codec in {"dvd_subtitle", "hdmv_pgs_subtitle"} else "text",
                    language_tag=facts.language_tag,
                    language_source="metadata",
                    title=facts.title,
                    is_default=facts.is_default,
                    is_forced=facts.is_forced,
                    is_sdh=facts.is_hearing_impaired,
                    metadata_json=facts.model_dump(mode="json"),
                )
            )

        row.file_signature = inventory.signature
        row.container = inventory.container
        row.audio_streams_json = audio_streams
        row.coverage_json = {}
        row.probe_tool_versions_json = binaries.availability()
        row.scanned_at = datetime.now(UTC)
        row.error = None
        row.source_job_id = context.delivery.canonical_job_id
        row.source_attempt_id = context.attempt.attempt_id
        row.source_fence_token = context.attempt.fence_token


async def bind_managed_subtitle(
    context: ExecutionContext, *, asset_id: str, media_file_id: int
) -> None:
    """Bind a managed sidecar to media/logical owners with fenced job provenance."""
    async with context.session_factory() as session, session.begin():
        if not await context.writer.owns_current_attempt(session):
            raise TrackMutationError("the attempt lost ownership before sidecar binding")
        media = await session.get(MediaFile, media_file_id)
        if media is None:
            raise TrackMutationError("the media file disappeared before sidecar binding")
        owners: list[tuple[str | None, int | None]] = []
        if media.movie_id is not None:
            owners.append(("movie", media.movie_id))
        episode_ids = tuple(
            await session.scalars(
                select(EpisodeMediaFile.episode_id).where(
                    EpisodeMediaFile.media_file_id == media_file_id
                )
            )
        )
        owners.extend(("episode", episode_id) for episode_id in episode_ids)
        if not owners:
            owners.append((None, None))

        for owner_type, owner_id in owners:
            binding = await session.scalar(
                select(ManagedSubtitleBinding).where(
                    ManagedSubtitleBinding.asset_id == asset_id,
                    ManagedSubtitleBinding.media_file_id == media_file_id,
                    ManagedSubtitleBinding.owner_type.is_(owner_type)
                    if owner_type is None
                    else ManagedSubtitleBinding.owner_type == owner_type,
                    ManagedSubtitleBinding.owner_id.is_(owner_id)
                    if owner_id is None
                    else ManagedSubtitleBinding.owner_id == owner_id,
                )
            )
            if binding is None:
                binding = ManagedSubtitleBinding(
                    asset_id=asset_id,
                    media_file_id=media_file_id,
                    owner_type=owner_type,
                    owner_id=owner_id,
                )
                session.add(binding)
            binding.source_job_id = context.delivery.canonical_job_id
            binding.source_attempt_id = context.attempt.attempt_id
            binding.source_fence_token = context.attempt.fence_token


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
