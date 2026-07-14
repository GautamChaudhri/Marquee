"""Fast checks for cooperative job-cancellation hooks."""

from __future__ import annotations

from threading import Event

import pytest

from marquee.core.jobs import cancel_registry
from marquee.core.jobs.builtin_handlers import subtitle_scan_all
from marquee.core.jobs.cancel_registry import JobCancelledError
from marquee.core.sync_service import SyncService
from marquee.ml.head_trainer import train_from_labels
from marquee.ml.taste_map import build_map
from marquee.models import Job, MediaFile


def _set_event() -> Event:
    event = Event()
    event.set()
    return event


async def test_sync_all_raises_when_cancel_event_is_set(db):
    with pytest.raises(JobCancelledError, match="library sync cancelled"):
        await SyncService(db).sync_all(cancel_event=_set_event())


async def test_subtitle_scan_all_raises_when_registry_event_is_set(db, monkeypatch):
    media_file = MediaFile(
        source="radarr",
        source_key="radarr:cancel-scan",
        path="/movies/cancel-scan.mkv",
        is_active=True,
    )
    db.add(media_file)
    await db.commit()

    async def candidates(*_args, **_kwargs):
        return [media_file]

    monkeypatch.setattr(
        "marquee.core.jobs.builtin_handlers._stale_or_missing_subtitle_scan_candidates",
        candidates,
    )
    job = Job(
        id="cancel-scan-job",
        root_id="cancel-scan-job",
        type="subtitle_scan_all",
        request={},
    )
    event = cancel_registry.register(job.id)
    event.set()
    try:
        with pytest.raises(JobCancelledError, match="subtitle scan-all cancelled"):
            await subtitle_scan_all(job)
    finally:
        cancel_registry.discard(job.id)


def test_train_from_labels_raises_when_cancel_event_is_set():
    with pytest.raises(JobCancelledError, match="learned head training cancelled"):
        train_from_labels(cancel_event=_set_event())


def test_build_map_raises_when_cancel_event_is_set():
    with pytest.raises(JobCancelledError, match="taste map build cancelled"):
        build_map(cancel_event=_set_event())
