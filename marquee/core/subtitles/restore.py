"""Managed-subtitle restoration after a media-file replacement (design §17.7).

The subtitle analogue of PosterService restore: when a Radarr/Sonarr upgrade
swaps the media file, managed assets bound to the logical movie/episode that are
no longer present get re-embedded — through the same audited mutation path, with
the same hardlink/space/validation safety. Cached subtitle bytes live under
Marquee's data dir, never the media root.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.media_files import resolve_media_file
from marquee.core.subtitles import languages, service
from marquee.models import (
    EpisodeMediaFile,
    ManagedSubtitleAsset,
    ManagedSubtitleBinding,
    MediaFile,
)

logger = logging.getLogger(__name__)


async def _owner_keys(db: AsyncSession, media_file: MediaFile) -> list[tuple[str, int]]:
    """Logical owners (movie/episode) of a media file, for asset binding lookup."""
    owners: list[tuple[str, int]] = []
    if media_file.movie_id is not None:
        owners.append(("movie", media_file.movie_id))
    episode_ids = (
        (
            await db.execute(
                select(EpisodeMediaFile.episode_id).where(
                    EpisodeMediaFile.media_file_id == media_file.id
                )
            )
        )
        .scalars()
        .all()
    )
    owners.extend(("episode", eid) for eid in episode_ids)
    return owners


async def restore_managed_assets(db: AsyncSession, job, emit) -> dict:
    """Re-embed managed assets that are missing from the current file."""
    media_file = await db.get(MediaFile, job.media_file_id)
    if media_file is None:
        return {"restored": 0, "reason": "media file gone"}

    owners = await _owner_keys(db, media_file)
    if not owners:
        return {"restored": 0, "reason": "no logical owner"}

    asset_ids: set[str] = set()
    for owner_type, owner_id in owners:
        bound = (
            (
                await db.execute(
                    select(ManagedSubtitleBinding.asset_id).where(
                        ManagedSubtitleBinding.owner_type == owner_type,
                        ManagedSubtitleBinding.owner_id == owner_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        asset_ids.update(bound)
    if not asset_ids:
        return {"restored": 0, "reason": "no managed assets"}

    assets = (
        (
            await db.execute(
                select(ManagedSubtitleAsset).where(
                    ManagedSubtitleAsset.id.in_(asset_ids),
                    ManagedSubtitleAsset.active.is_(True),
                    ManagedSubtitleAsset.restore_on_replacement.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if not assets:
        return {"restored": 0, "reason": "no auto-restore assets"}

    inventory = await service.get_inventory_dict(db, media_file.id)
    present_langs = {t["language_tag"] for t in inventory["tracks"] if t["source"] == "embedded"}

    restored = 0
    for asset in assets:
        if languages.normalize(asset.language_tag)[0] in present_langs:
            continue  # already present after the replacement
        cache = Path(asset.cache_path)
        if not cache.is_file():
            logger.warning("managed asset %s cache missing: %s", asset.id, cache)
            continue
        resolved = await resolve_media_file(db, media_file.id)
        sidecar = resolved.path.with_suffix(f".{asset.language_tag}.restored.srt")
        try:
            shutil.copy2(cache, sidecar)
        except OSError as exc:
            logger.warning("could not stage managed asset %s: %s", asset.id, exc)
            continue
        await emit(
            db, job.job_id, "restore", "running", message=f"re-embedding {asset.language_tag}"
        )
        # Rescan so the staged sidecar becomes an embeddable external track, then
        # embed it via the normal mutation transaction.
        await service.scan_inventory(db, resolved)
        track = await _external_track_for(db, media_file.id, sidecar)
        if track is None:
            continue
        from marquee.core.subtitles import mutation  # noqa: PLC0415

        job.operation = "subtitle_embed"
        job.request_json = json.dumps({"inventory_id": track.inventory_id, "track_ids": [track.id]})
        await mutation.execute_job(db, job, emit)
        restored += 1

    return {"restored": restored}


async def _external_track_for(db: AsyncSession, media_file_id: int, path: Path):
    inventory = await service.get_inventory_dict(db, media_file_id)
    for track in inventory["tracks"]:
        if track["source"] != "external":
            continue
        full = await service.get_track(db, media_file_id, track["id"])
        if full and full.external_path and Path(full.external_path) == path:
            return full
    return None
