from __future__ import annotations

import asyncio

import asyncpg
import pytest
from sqlalchemy import delete

from marquee.core.configuration import (
    ConfigurationError,
    ConfigurationUnavailableError,
    update_configuration,
)
from marquee.core.configuration_cache import ConfigurationProvider
from marquee.db_migration import asyncpg_dsn
from marquee.models import ConfigurationCurrent, ConfigurationRevision


async def test_provider_rejects_stale_notifications_and_repairs_missed_update(db):
    provider = ConfigurationProvider()
    await provider.refresh_from_session(db, initial=True)

    assert await provider.handle_notification("1") is False
    assert await provider.handle_notification("not-a-version") is False
    assert provider.health()["ignored_notifications"] == 2

    state, _ = await update_configuration(
        db,
        expected_version=1,
        updates={"K_NEIGHBORS": 12},
        actor={"kind": "test"},
        trigger="cache_test",
    )
    await db.commit()

    repaired = await provider.repair()
    assert repaired.version == state.version == 2
    assert provider.effective("pipeline")["K_NEIGHBORS"] == 12


async def test_api_worker_and_scheduler_providers_use_same_notification_rules(db):
    providers = [ConfigurationProvider() for _ in range(3)]
    for provider in providers:
        await provider.refresh_from_session(db, initial=True)

    await update_configuration(
        db,
        expected_version=1,
        updates={"HEAL_INTERVAL_MINUTES": 45},
        actor={"kind": "test"},
        trigger="role_cache_test",
    )
    await db.commit()

    reloaded = await asyncio.gather(*(provider.handle_notification("2") for provider in providers))
    assert reloaded == [True, True, True]
    assert {provider.state.version for provider in providers} == {2}

    restarted = ConfigurationProvider()
    await restarted.refresh_from_session(db, initial=True)
    assert restarted.state.version == 2


async def test_invalid_later_revision_retains_last_valid_state(db):
    provider = ConfigurationProvider()
    original = await provider.refresh_from_session(db, initial=True)
    db.add(
        ConfigurationRevision(
            version=2,
            values={"K_NEIGHBORS": 12},
            checksum="invalid-checksum",
            schema_version=1,
            actor={"kind": "test"},
            trigger="corrupt_test",
        )
    )
    pointer = await db.get(ConfigurationCurrent, 1)
    assert pointer is not None
    pointer.current_version = 2
    await db.commit()

    retained = await provider.repair()
    assert retained == original
    assert provider.health()["status"] == "invalid"
    assert provider.health()["version"] == 1


async def test_initial_load_without_revision_fails_closed(db):
    await db.execute(delete(ConfigurationCurrent))
    await db.execute(delete(ConfigurationRevision))
    await db.commit()
    provider = ConfigurationProvider()

    with pytest.raises(ConfigurationUnavailableError, match="no valid initial"):
        await provider.refresh_from_session(db, initial=True)
    assert provider.health()["status"] == "unavailable"


async def test_database_outage_marks_cache_stale_and_retains_last_valid(db, monkeypatch):
    provider = ConfigurationProvider()
    original = await provider.refresh_from_session(db, initial=True)

    class BrokenContext:
        async def __aenter__(self):
            raise OSError("database unavailable")

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(
        "marquee.core.configuration_cache._get_session_factory",
        lambda: lambda: BrokenContext(),
    )
    retained = await provider.repair()
    assert retained == original
    assert provider.health()["status"] == "stale"


async def test_snapshot_for_is_bounded_to_public_database_execution_keys(db):
    provider = ConfigurationProvider()
    await provider.refresh_from_session(db, initial=True)

    empty = provider.snapshot_for(())
    assert empty.version == 1 and empty.values == {}
    bounded = provider.snapshot_for({"K_NEIGHBORS", "GATE_MIN_WIDTH"})
    assert list(bounded.values) == ["GATE_MIN_WIDTH", "K_NEIGHBORS"]

    for forbidden in (
        "TMDB_READ_ACCESS_TOKEN",
        "CLIP_MODEL_PATH",
        "HEAL_ENABLED",
        "unknown_key",
    ):
        with pytest.raises(ConfigurationError):
            provider.snapshot_for({forbidden})


async def test_configuration_notification_is_delivered_only_after_commit(db):
    listener = await asyncpg.connect(asyncpg_dsn())
    received = asyncio.Event()

    def notified(_connection, _pid, _channel, payload):
        if payload == "2":
            received.set()

    await listener.add_listener("marquee_configuration", notified)
    try:
        await update_configuration(
            db,
            expected_version=1,
            updates={"K_NEIGHBORS": 12},
            actor={"kind": "test"},
            trigger="notification_test",
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(received.wait(), timeout=0.05)
        await db.commit()
        await asyncio.wait_for(received.wait(), timeout=2)
    finally:
        await listener.remove_listener("marquee_configuration", notified)
        await listener.close()
