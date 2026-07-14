from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from marquee.core.configuration import (
    CONFIGURATION_CATALOG,
    ConfigurationError,
    ConfigurationVersionConflictError,
    configuration_checksum,
    read_current_configuration,
    update_configuration,
)
from marquee.database import _get_session_factory
from marquee.models import ConfigurationRevision


def test_configuration_catalog_records_owner_sensitivity_and_apply_mode():
    pipeline = CONFIGURATION_CATALOG["K_NEIGHBORS"]
    assert pipeline.owner == "pipeline"
    assert pipeline.database_owned is True
    assert pipeline.apply_mode == "next_job"

    secret = CONFIGURATION_CATALOG["SUBGEN_CALLBACK_TOKEN"]
    assert secret.owner == "subtitle"
    assert secret.sensitivity == "secret"
    assert secret.database_owned is False

    restart = CONFIGURATION_CATALOG["CLIP_MODEL_PATH"]
    assert restart.database_owned is False
    assert restart.apply_mode == "restart"


def test_configuration_checksum_is_canonical():
    assert configuration_checksum({"b": 2, "a": 1}) == configuration_checksum({"a": 1, "b": 2})
    assert configuration_checksum({}) == (
        "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    )


async def test_optimistic_update_appends_revision_and_noop_does_not_churn(db):
    state, changed = await update_configuration(
        db,
        expected_version=1,
        updates={"K_NEIGHBORS": 12},
        actor={"kind": "user", "id": "test"},
        trigger="api",
    )
    await db.commit()

    assert changed is True
    assert state.version == 2
    assert state.values == {"K_NEIGHBORS": 12}
    assert (await read_current_configuration(db)).version == 2

    unchanged, changed = await update_configuration(
        db,
        expected_version=2,
        updates={"K_NEIGHBORS": 12},
        actor={"kind": "user", "id": "test"},
        trigger="api",
    )
    await db.commit()

    assert changed is False
    assert unchanged.version == 2
    count = await db.scalar(select(func.count()).select_from(ConfigurationRevision))
    assert count == 2


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"UNKNOWN_OPTION": True}, "unknown"),
        ({"api_token": "leak"}, "secret-like"),
        ({"SUBGEN_CALLBACK_TOKEN": "leak"}, "secret"),
        ({"CLIP_MODEL_PATH": "/tmp/model.onnx"}, "restart-owned"),
        ({"K_NEIGHBORS": 0}, "invalid pipeline"),
    ],
)
async def test_invalid_unknown_secret_and_restart_updates_create_no_revision(
    db, updates, message
):
    with pytest.raises(ConfigurationError, match=message):
        await update_configuration(
            db,
            expected_version=1,
            updates=updates,
            actor={"kind": "user", "id": "test"},
            trigger="api",
        )
    await db.rollback()

    count = await db.scalar(select(func.count()).select_from(ConfigurationRevision))
    assert count == 1
    assert (await read_current_configuration(db)).version == 1


async def test_stale_writer_gets_current_version_without_creating_revision(db):
    state, _ = await update_configuration(
        db,
        expected_version=1,
        updates={"K_NEIGHBORS": 11},
        actor={"kind": "user", "id": "winner"},
        trigger="api",
    )
    await db.commit()

    with pytest.raises(ConfigurationVersionConflictError) as conflict:
        await update_configuration(
            db,
            expected_version=1,
            updates={"K_NEIGHBORS": 12},
            actor={"kind": "user", "id": "stale"},
            trigger="api",
        )
    assert conflict.value.current.version == state.version == 2


async def test_simultaneous_same_version_writers_have_one_winner(db):
    factory = _get_session_factory()

    async def write(value: int):
        async with factory() as session:
            try:
                state, changed = await update_configuration(
                    session,
                    expected_version=1,
                    updates={"K_NEIGHBORS": value},
                    actor={"kind": "user", "id": str(value)},
                    trigger="api",
                )
                await session.commit()
                return state, changed
            except ConfigurationVersionConflictError as exc:
                await session.rollback()
                return exc

    results = await asyncio.gather(write(11), write(12))

    winners = [result for result in results if isinstance(result, tuple)]
    conflicts = [
        result for result in results if isinstance(result, ConfigurationVersionConflictError)
    ]
    assert len(winners) == len(conflicts) == 1
    assert winners[0][0].version == conflicts[0].current.version == 2
    assert await db.scalar(select(func.count()).select_from(ConfigurationRevision)) == 2
