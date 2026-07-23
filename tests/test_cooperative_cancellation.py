"""Fast checks for cooperative job-cancellation hooks."""

from __future__ import annotations

from threading import Event

import pytest

from marquee.core.cancellation import JobCancelledError
from marquee.core.sync_service import SyncService
from marquee.ml.taste_map import build_map


def _set_event() -> Event:
    event = Event()
    event.set()
    return event


async def test_sync_all_raises_when_cancel_event_is_set(db):
    with pytest.raises(JobCancelledError, match="library sync cancelled"):
        await SyncService(db).sync_all(cancel_event=_set_event())


def test_build_map_raises_when_cancel_event_is_set():
    with pytest.raises(JobCancelledError, match="taste map build cancelled"):
        build_map(cancel_event=_set_event())
