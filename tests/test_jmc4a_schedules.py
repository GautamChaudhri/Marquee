"""A4 code-owned catalog, UTC occurrence, and canonical callback contracts."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pgqueuer import Queries
from sqlalchemy import func, select

import marquee.core.jobs.submission as submission_module
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.definitions import JobDefinitionRegistry
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.pgqueuer_scheduler import create_scheduler
from marquee.core.jobs.schedules import (
    FIXED_TEST_SCHEDULE_CATALOG,
    MAX_SCHEDULE_DIAGNOSTICS,
    PRODUCTION_SCHEDULE_CATALOG,
    ScheduleCatalog,
    ScheduleConfiguration,
    ScheduleDefinition,
    ScheduleDiagnostics,
    normalize_due_occurrence,
    occurrence_key,
    submit_schedule_occurrence,
)
from marquee.database import _get_engine, _get_session_factory
from marquee.models import Job


@pytest_asyncio.fixture(autouse=True)
async def installed_pgqueuer(db) -> Queries:
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queries = Queries.from_asyncpg_connection(raw.driver_connection)
        await queries.install()
        try:
            yield queries
        finally:
            await db.rollback()
            await queries.uninstall()


def _configuration(
    *, enabled: bool = True, production: bool = True, interval: int = 15, hour: int = 3
) -> ScheduleConfiguration:
    return ScheduleConfiguration(
        revision=1,
        sync_interval_minutes=interval,
        audio_subs_deep_scan_enabled=enabled,
        audio_subs_deep_scan_hour=hour,
        audio_subs_deep_scan_batch=200,
        production_occurrences_enabled=production,
    )


def _schedule(value: datetime):
    return SimpleNamespace(updated=value)


def _fixed_schedule() -> ScheduleDefinition:
    return next(iter(FIXED_TEST_SCHEDULE_CATALOG))


@pytest.fixture
def schedule_enabled_noop_registry(monkeypatch) -> JobDefinitionRegistry:
    definition = replace(
        JOB_DEFINITION_REGISTRY.get("system_noop"),
        trigger_kinds=frozenset({TriggerKind.SYSTEM, TriggerKind.SCHEDULE}),
    )
    registry = JobDefinitionRegistry((definition,))
    monkeypatch.setattr(submission_module, "JOB_DEFINITION_REGISTRY", registry)
    return registry


@pytest.mark.asyncio
async def test_production_catalog_is_registered_but_occurrences_are_code_disabled(db) -> None:
    definitions = {definition.key: definition for definition in PRODUCTION_SCHEDULE_CATALOG}
    assert list(definitions) == [
        "library-sync",
        "audio-subs-deep-scan",
    ]
    assert all(
        not definition.enabled_predicate(_configuration(enabled=True, production=False))
        for definition in PRODUCTION_SCHEDULE_CATALOG
    )
    assert not definitions["library-sync"].enabled_predicate(
        _configuration(production=True, interval=0)
    )
    assert definitions["library-sync"].enabled_predicate(
        _configuration(production=True, interval=15)
    )
    assert not definitions["audio-subs-deep-scan"].enabled_predicate(
        _configuration(enabled=False, production=True)
    )
    assert definitions["audio-subs-deep-scan"].enabled_predicate(
        _configuration(enabled=True, production=True)
    )
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = create_scheduler(
            raw.driver_connection,
            catalog=PRODUCTION_SCHEDULE_CATALOG,
        )
    assert {(key.entrypoint, key.expression) for key in app.sm.registry} == {
        ("schedule_library_sync", "* * * * *"),
        ("schedule_audio_subs_deep_scan", "0 * * * *"),
    }


@pytest.mark.asyncio
async def test_fixed_test_schedule_dispatches_only_through_canonical_submission(
    db, schedule_enabled_noop_registry
) -> None:
    diagnostics = ScheduleDiagnostics()
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        app = create_scheduler(
            raw.driver_connection,
            catalog=FIXED_TEST_SCHEDULE_CATALOG,
            configuration_loader=_configuration,
            diagnostics=diagnostics,
        )
        key, executor = next(iter(app.sm.registry.items()))
        await app.queries.insert_schedule({key: timedelta(0)})
        picked = (await app.queries.fetch_schedule({key: timedelta(seconds=1)}))[0]
        await app.sm.dispatch(executor, picked)

    job = await db.scalar(select(Job))
    assert job is not None
    assert job.type == "system_noop"
    assert job.trigger_kind == "schedule"
    assert job.idempotency_key.startswith("schedule:fixed-noop:")
    assert [item.disposition for item in diagnostics.snapshot()] == ["created"]


def test_catalog_rejects_duplicate_product_or_transport_identity() -> None:
    fixed = _fixed_schedule()
    with pytest.raises(ValueError, match="keys must be unique"):
        ScheduleCatalog((fixed, fixed))
    with pytest.raises(ValueError, match="pairs must be unique"):
        ScheduleCatalog((fixed, replace(fixed, key="fixed-noop-second")))


def test_interval_misfires_coalesce_to_only_the_current_utc_bucket() -> None:
    definition = next(
        item for item in PRODUCTION_SCHEDULE_CATALOG if item.key == "library-sync"
    )
    config = _configuration(interval=15)
    first = normalize_due_occurrence(
        definition, _schedule(datetime(2026, 7, 13, 12, 7, 42, tzinfo=UTC)), config
    )
    duplicate = normalize_due_occurrence(
        definition, _schedule(datetime(2026, 7, 13, 12, 14, 59, tzinfo=UTC)), config
    )
    following = normalize_due_occurrence(
        definition, _schedule(datetime(2026, 7, 13, 12, 16, tzinfo=UTC)), config
    )
    assert first == duplicate == datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    assert following == datetime(2026, 7, 13, 12, 15, tzinfo=UTC)
    assert occurrence_key(definition, first) == "schedule:library-sync:20260713T120000Z"


def test_hourly_occurrence_is_utc_and_skips_the_wrong_hour() -> None:
    definition = next(
        item
        for item in PRODUCTION_SCHEDULE_CATALOG
        if item.key == "audio-subs-deep-scan"
    )
    config = _configuration(hour=3)
    due = normalize_due_occurrence(
        definition, _schedule(datetime(2026, 7, 13, 3, 9, tzinfo=UTC)), config
    )
    assert due == datetime(2026, 7, 13, 3, 0, tzinfo=UTC)
    assert (
        normalize_due_occurrence(
            definition, _schedule(datetime(2026, 7, 13, 4, 0, tzinfo=UTC)), config
        )
        is None
    )


@pytest.mark.asyncio
async def test_fixed_callback_duplicate_restart_and_two_scheduler_race_reuse_one_job(
    db, schedule_enabled_noop_registry
) -> None:
    definition = _fixed_schedule()
    value = _schedule(datetime(2026, 7, 13, 12, 0, 1, tzinfo=UTC))
    factory = _get_session_factory()
    diagnostics = ScheduleDiagnostics()

    first, second = await asyncio.gather(
        submit_schedule_occurrence(
            definition,
            value,
            configuration_loader=_configuration,
            diagnostics=diagnostics,
            session_factory=factory,
        ),
        submit_schedule_occurrence(
            definition,
            value,
            configuration_loader=_configuration,
            diagnostics=diagnostics,
            session_factory=factory,
        ),
    )
    assert first is not None and second is not None
    assert {first.disposition, second.disposition} == {"created", "reused"}
    restarted = await submit_schedule_occurrence(
        definition,
        value,
        configuration_loader=_configuration,
        diagnostics=diagnostics,
        session_factory=factory,
    )
    assert restarted is not None and restarted.disposition == "reused"
    assert await db.scalar(select(func.count()).select_from(Job)) == 1


@pytest.mark.asyncio
async def test_fixed_schedule_disable_reenable_does_not_create_extra_jobs(
    db, schedule_enabled_noop_registry
) -> None:
    schedule_definition = _fixed_schedule()
    value = _schedule(datetime(2026, 7, 13, 3, 5, tzinfo=UTC))
    state = {"enabled": False}

    def configuration() -> ScheduleConfiguration:
        return _configuration(enabled=True, production=state["enabled"], hour=3)

    diagnostics = ScheduleDiagnostics()
    factory = _get_session_factory()
    assert (
        await submit_schedule_occurrence(
            schedule_definition,
            value,
            configuration_loader=configuration,
            diagnostics=diagnostics,
            session_factory=factory,
        )
        is None
    )
    state["enabled"] = True
    created = await submit_schedule_occurrence(
        schedule_definition,
        value,
        configuration_loader=configuration,
        diagnostics=diagnostics,
        session_factory=factory,
    )
    state["enabled"] = False
    assert (
        await submit_schedule_occurrence(
            schedule_definition,
            value,
            configuration_loader=configuration,
            diagnostics=diagnostics,
            session_factory=factory,
        )
        is None
    )
    state["enabled"] = True
    reused = await submit_schedule_occurrence(
        schedule_definition,
        value,
        configuration_loader=configuration,
        diagnostics=diagnostics,
        session_factory=factory,
    )
    assert created is not None and created.disposition == "created"
    assert reused is not None and reused.disposition == "reused"
    assert await db.scalar(select(func.count()).select_from(Job)) == 1
    assert [item.disposition for item in diagnostics.snapshot()] == [
        "disabled",
        "created",
        "disabled",
        "reused",
    ]


@pytest.mark.asyncio
async def test_ineligible_and_disabled_occurrences_are_bounded_diagnostics(db) -> None:
    definition = next(
        item
        for item in PRODUCTION_SCHEDULE_CATALOG
        if item.key == "audio-subs-deep-scan"
    )
    diagnostics = ScheduleDiagnostics()
    for minute in range(MAX_SCHEDULE_DIAGNOSTICS + 1):
        await submit_schedule_occurrence(
            definition,
            _schedule(datetime(2026, 7, 14, 4, minute % 60, tzinfo=UTC)),
            configuration_loader=lambda: _configuration(
                enabled=True, production=False, hour=3
            ),
            diagnostics=diagnostics,
        )
    assert len(diagnostics.snapshot()) == MAX_SCHEDULE_DIAGNOSTICS
    assert all(item.disposition == "ineligible" for item in diagnostics.snapshot())
    assert await db.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.asyncio
async def test_callback_failures_are_not_converted_to_diagnostics(db) -> None:
    diagnostics = ScheduleDiagnostics()

    def unavailable() -> ScheduleConfiguration:
        raise RuntimeError("configuration unavailable")

    with pytest.raises(RuntimeError, match="configuration unavailable"):
        await submit_schedule_occurrence(
            _fixed_schedule(),
            _schedule(datetime(2026, 7, 13, 12, 0, tzinfo=UTC)),
            configuration_loader=unavailable,
            diagnostics=diagnostics,
        )
    assert diagnostics.snapshot() == ()
    assert await db.scalar(select(func.count()).select_from(Job)) == 0
