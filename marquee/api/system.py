"""What ``GET /api/system`` answers, built from what the lower layers expose.

It is cheap and read-only: counts from the database, the model manifest and a stat of each
file it lists (no hashing), and the size of four folders under the data directory, measured
off the event loop and kept for a minute. It never runs the doctor or loads a model.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee import __version__
from marquee.api.schemas.system import (
    LastSync,
    LibraryInfo,
    ModelFile,
    ModelsInfo,
    SchemaInfo,
    SettingsInfo,
    StorageInfo,
    SystemInfo,
)
from marquee.config import settings
from marquee.db import migrate
from marquee.db.models import (
    Artwork,
    Deployment,
    Exemplar,
    Job,
    Movie,
    PosterRun,
    PreferenceEvent,
)
from marquee.ml import model_manifest

STORAGE_TTL_S = 60.0


def folder_size(path: Path) -> int:
    """Blocking: the bytes of every file under ``path``, links not followed; 0 when absent."""
    total = 0
    for root, _, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def measure_storage(data_dir: Path) -> StorageInfo:
    """Blocking: the size of runs/, backups/, cache/, and ml/ under ``data_dir``."""
    return StorageInfo(
        runs=folder_size(data_dir / "runs"),
        backups=folder_size(data_dir / "backups"),
        cache=folder_size(data_dir / "cache"),
        ml=folder_size(data_dir / "ml"),
        measured_at=datetime.now(UTC),
    )


@dataclass
class StorageCache:
    """The last measurement of each data directory, kept for ``ttl_s`` seconds."""

    ttl_s: float = STORAGE_TTL_S
    clock: Callable[[], float] = time.monotonic
    measure: Callable[[Path], StorageInfo] = measure_storage

    def __post_init__(self) -> None:
        self._kept: dict[Path, tuple[float, StorageInfo]] = {}

    async def get(self, data_dir: Path) -> StorageInfo:
        kept = self._kept.get(data_dir)
        if kept is not None and self.clock() - kept[0] < self.ttl_s:
            return kept[1]
        found = await asyncio.to_thread(self.measure, data_dir)
        self._kept[data_dir] = (self.clock(), found)
        return found


storage_cache = StorageCache()


def models_on_disk(data_dir: Path, manifest_path: Path) -> ModelsInfo:
    """Blocking: each file the model manifest lists, whether it is there, and its size."""
    try:
        manifest = model_manifest.load_manifest(manifest_path)
    except model_manifest.ManifestError:
        return ModelsInfo(manifest="unreadable", files=[])
    if manifest is None:
        return ModelsInfo(manifest="missing", files=[])
    files = []
    for entry in manifest.entries:
        path = data_dir / entry.path
        if entry.kind == "folder":
            present = path.is_dir()
            size = folder_size(path) if present else None
        else:
            present = path.is_file()
            size = path.stat().st_size if present else None
        files.append(
            ModelFile(
                item=entry.item,
                path=entry.path,
                kind=entry.kind,
                present=present,
                size_bytes=size,
                expected_size_bytes=entry.size,
            )
        )
    return ModelsInfo(manifest="found", files=files)


async def _count(session: AsyncSession, *conditions: object, model: type = Movie) -> int:
    return int(
        await session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
    )


async def _by(session: AsyncSession, column: object) -> dict[str, int]:
    rows = await session.execute(select(column, func.count()).group_by(column))
    return {str(key): int(count) for key, count in rows}


async def library_counts(session: AsyncSession) -> LibraryInfo:
    exemplars: dict[str, dict[str, int]] = {}
    rows = await session.execute(
        select(Exemplar.state, Exemplar.polarity, func.count()).group_by(
            Exemplar.state, Exemplar.polarity
        )
    )
    for state, polarity, count in rows:
        exemplars.setdefault(state, {})[polarity] = int(count)
    return LibraryInfo(
        movies=await _count(session, Movie.is_present.is_(True)),
        movies_with_file=await _count(
            session, Movie.is_present.is_(True), Movie.has_file.is_(True)
        ),
        posters=await _by(session, Artwork.origin),
        runs=await _by(session, PosterRun.state),
        deployments=await _count(session, model=Deployment),
        exemplars=exemplars,
        preference_events=await _count(session, model=PreferenceEvent),
    )


async def last_sync(session: AsyncSession) -> LastSync | None:
    job = await session.scalar(
        select(Job).where(Job.kind == "library_sync").order_by(Job.id.desc()).limit(1)
    )
    if job is None:
        return None
    return LastSync(
        job_id=job.id,
        state=job.state,  # type: ignore[arg-type]
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def summary(session: AsyncSession, head_revision: str) -> SystemInfo:
    current = await session.run_sync(lambda sync: migrate.current_revision(sync.connection()))
    data_dir = settings.DATA_DIR
    return SystemInfo(
        version=__version__,
        environment=settings.MARQUEE_ENVIRONMENT,
        schema=SchemaInfo(current=current, head=head_revision, at_head=current == head_revision),
        settings=SettingsInfo(
            deploy_policy=settings.DEPLOY_POLICY,
            run_new_movies=settings.RUN_NEW_MOVIES,
            sync_interval_minutes=settings.SYNC_INTERVAL_MINUTES,
            movie_roots=[str(root) for root in settings.MOVIE_ROOTS],
            data_dir=str(data_dir),
        ),
        library=await library_counts(session),
        models=await asyncio.to_thread(models_on_disk, data_dir, settings.model_manifest_path),
        last_sync=await last_sync(session),
        storage=await storage_cache.get(data_dir),
    )
