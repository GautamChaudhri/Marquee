from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

import marquee.core.configuration as configuration_module
from marquee.config import Settings, settings
from marquee.core.configuration import (
    APP_SECRET_STORE_KEYS,
    CONFIGURATION_CATALOG,
    PIPELINE_INTERNAL_KEYS,
    ConfigurationError,
    ConfigurationVersionConflictError,
    configuration_checksum,
    import_legacy_revision_values,
    legacy_environment_revision_updates,
    read_current_configuration,
    update_configuration,
)
from marquee.core.pipeline_config import PipelineSettings
from marquee.database import _get_session_factory
from marquee.models import ConfigurationRevision


def test_configuration_catalog_records_owner_sensitivity_and_apply_mode():
    pipeline = CONFIGURATION_CATALOG["K_NEIGHBORS"]
    assert pipeline.owner == "pipeline"
    assert pipeline.storage == "revision"
    assert pipeline.database_owned is True
    assert pipeline.apply_mode == "next_job"

    secret = CONFIGURATION_CATALOG["TMDB_READ_ACCESS_TOKEN"]
    assert secret.owner == "app"
    assert secret.sensitivity == "secret"
    assert secret.storage == "secret_store"
    assert secret.database_owned is False
    assert secret.apply_mode == "next_job"

    assert CONFIGURATION_CATALOG["RADARR_URL"].apply_mode == "next_job"
    assert CONFIGURATION_CATALOG["SONARR_URL"].apply_mode == "next_job"

    restart = CONFIGURATION_CATALOG["CLIP_MODEL_PATH"]
    assert restart.storage == "revision"
    assert restart.database_owned is True
    assert restart.apply_mode == "restart"


def test_configuration_catalog_classifies_every_modeled_field():
    expected = set(Settings.model_fields) | set(PipelineSettings.model_fields)
    assert set(CONFIGURATION_CATALOG) == expected
    assert {
        key for key, entry in CONFIGURATION_CATALOG.items() if entry.storage == "secret_store"
    } == set(APP_SECRET_STORE_KEYS)
    assert {
        key for key, entry in CONFIGURATION_CATALOG.items() if entry.storage == "internal"
    } == set(PIPELINE_INTERNAL_KEYS)
    assert all(
        entry.storage in {"revision", "secret_store", "deployment", "internal"}
        for entry in CONFIGURATION_CATALOG.values()
    )
    assert all(
        entry.tab
        in {"general", "connections", "media", "posters", "pipeline", "taste", "system", "access"}
        for entry in CONFIGURATION_CATALOG.values()
    )
    assert not any(
        entry.tab == "general" and entry.level == "advanced"
        for entry in CONFIGURATION_CATALOG.values()
    )
    assert all(not CONFIGURATION_CATALOG[key].visible for key in PIPELINE_INTERNAL_KEYS)
    for retired in {
        "TVDB_API_KEY",
        "LETTERBOX_DOVI_TOOL",
        "SUBGEN_URL",
        "SUBGEN_CALLBACK_TOKEN",
        "SUBTITLE_BACKUP_MODE",
    }:
        assert retired not in CONFIGURATION_CATALOG


def test_configuration_checksum_is_canonical():
    assert configuration_checksum({"b": 2, "a": 1}) == configuration_checksum({"a": 1, "b": 2})
    assert configuration_checksum({}) == (
        "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    )


def test_legacy_environment_seed_includes_only_explicit_revision_fields(monkeypatch):
    app = Settings(
        _env_file=None,
        APP_NAME="Legacy Marquee",
        RADARR_API_KEY="never-store-this",
        HOST="127.0.0.1",
        DATA_DIR=settings.DATA_DIR,
    )
    pipeline = PipelineSettings(_env_file=None, K_NEIGHBORS=17)
    app.__pydantic_fields_set__ = {"APP_NAME", "RADARR_API_KEY", "HOST"}
    pipeline.__pydantic_fields_set__ = {"K_NEIGHBORS"}
    monkeypatch.setitem(configuration_module._OWNER_BASES, "app", app)
    monkeypatch.setitem(configuration_module._OWNER_BASES, "pipeline", pipeline)

    updates = legacy_environment_revision_updates({})
    assert updates["APP_NAME"] == "Legacy Marquee"
    assert updates["K_NEIGHBORS"] == 17
    assert "RADARR_API_KEY" not in updates
    assert "HOST" not in updates


async def test_legacy_environment_seed_is_idempotent_and_preserves_existing_revision(
    db, monkeypatch
):
    app = Settings(_env_file=None, APP_NAME="Legacy Marquee", DATA_DIR=settings.DATA_DIR)
    pipeline = PipelineSettings(_env_file=None, K_NEIGHBORS=17)
    app.__pydantic_fields_set__ = {"APP_NAME"}
    pipeline.__pydantic_fields_set__ = {"K_NEIGHBORS"}
    monkeypatch.setitem(configuration_module._OWNER_BASES, "app", app)
    monkeypatch.setitem(configuration_module._OWNER_BASES, "pipeline", pipeline)

    state, imported = await import_legacy_revision_values(db)
    await db.commit()
    assert imported == 2
    assert state.values["APP_NAME"] == "Legacy Marquee"
    assert state.values["K_NEIGHBORS"] == 17

    state, imported = await import_legacy_revision_values(db)
    assert imported == 0
    assert state.version == 2


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
        ({"TMDB_READ_ACCESS_TOKEN": "leak"}, "secret"),
        ({"HOST": "127.0.0.1"}, "deployment-owned"),
        ({"K_NEIGHBORS": 0}, "invalid pipeline"),
    ],
)
async def test_invalid_unknown_secret_and_restart_updates_create_no_revision(db, updates, message):
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
