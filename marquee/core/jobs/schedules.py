"""Code-owned PgQueuer schedules that only produce canonical jobs."""

from __future__ import annotations

import logging
import re
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pgqueuer import PgQueuer
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.configuration_cache import configuration_provider
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.submission import (
    Initiator,
    SubjectLocator,
    SubmissionResult,
    submit_job,
)
from marquee.database import _get_session_factory

logger = logging.getLogger(__name__)

MAX_SCHEDULE_DIAGNOSTICS = 100
PRODUCTION_SCHEDULE_OCCURRENCES_ENABLED = False
_KEY = re.compile(r"^[a-z][a-z0-9-]{0,62}[a-z0-9]$")


class ScheduleValue(Protocol):
    """The stable PgQueuer values used by occurrence normalization."""

    updated: datetime


class OccurrencePolicy(StrEnum):
    EXACT_SECOND = "exact_second"
    INTERVAL_BUCKET = "interval_bucket"
    HOURLY_WINDOW = "hourly_window"


@dataclass(frozen=True, slots=True)
class ScheduleConfiguration:
    revision: int | None
    sync_interval_minutes: int
    audio_subs_deep_scan_enabled: bool
    audio_subs_deep_scan_hour: int
    audio_subs_deep_scan_batch: int
    production_occurrences_enabled: bool = PRODUCTION_SCHEDULE_OCCURRENCES_ENABLED


EnabledPredicate = Callable[[ScheduleConfiguration], bool]
RequestBuilder = Callable[[ScheduleConfiguration, datetime], Mapping[str, object]]
SubjectBuilder = Callable[[ScheduleConfiguration, datetime], SubjectLocator]


@dataclass(frozen=True, slots=True)
class ScheduleDefinition:
    key: str
    entrypoint: str
    expression: str
    produced_job_type: str
    trigger: TriggerKind
    initiator: Initiator | None
    enabled_predicate: EnabledPredicate
    occurrence_policy: OccurrencePolicy
    request_builder: RequestBuilder
    subject_builder: SubjectBuilder

    def __post_init__(self) -> None:
        if not _KEY.fullmatch(self.key):
            raise ValueError("schedule key is invalid")
        if not self.entrypoint or len(self.entrypoint) > 80:
            raise ValueError("schedule entrypoint is invalid")
        if not self.expression or len(self.expression) > 80:
            raise ValueError("schedule expression is invalid")


class ScheduleCatalog:
    """Immutable catalog with unique product and PgQueuer identities."""

    def __init__(self, definitions: Sequence[ScheduleDefinition]) -> None:
        values = tuple(definitions)
        keys = [definition.key for definition in values]
        transport_keys = [
            (definition.entrypoint, definition.expression) for definition in values
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("schedule catalog keys must be unique")
        if len(transport_keys) != len(set(transport_keys)):
            raise ValueError("schedule entrypoint/expression pairs must be unique")
        self._definitions = values

    def __iter__(self):
        return iter(self._definitions)

    def __len__(self) -> int:
        return len(self._definitions)


@dataclass(frozen=True, slots=True)
class ScheduleDiagnostic:
    schedule_key: str
    due_utc: str
    disposition: str
    reason: str | None
    recorded_at: str


class ScheduleDiagnostics:
    """Bounded, sanitized process-local scheduler observations."""

    def __init__(self, *, limit: int = MAX_SCHEDULE_DIAGNOSTICS) -> None:
        if limit < 1 or limit > MAX_SCHEDULE_DIAGNOSTICS:
            raise ValueError("schedule diagnostic limit is invalid")
        self._items: deque[ScheduleDiagnostic] = deque(maxlen=limit)

    def record(
        self,
        definition: ScheduleDefinition,
        *,
        due: datetime,
        disposition: str,
        reason: str | None = None,
    ) -> None:
        item = ScheduleDiagnostic(
            schedule_key=definition.key,
            due_utc=_format_utc(due),
            disposition=disposition,
            reason=reason,
            recorded_at=_format_utc(datetime.now(UTC)),
        )
        self._items.append(item)
        logger.info(
            "schedule occurrence %s: key=%s due=%s reason=%s",
            disposition,
            item.schedule_key,
            item.due_utc,
            reason or "none",
        )

    def snapshot(self) -> tuple[ScheduleDiagnostic, ...]:
        return tuple(self._items)

    def clear(self) -> None:
        self._items.clear()


schedule_diagnostics = ScheduleDiagnostics()


def load_schedule_configuration() -> ScheduleConfiguration:
    """Read the current versioned scheduler inputs and restart-owned sync interval."""
    subtitle = configuration_provider.effective("subtitle")
    return ScheduleConfiguration(
        revision=configuration_provider.state.version,
        sync_interval_minutes=settings.SYNC_INTERVAL_MINUTES,
        audio_subs_deep_scan_enabled=bool(subtitle["AUDIO_SUBS_DEEP_SCAN_ENABLED"]),
        audio_subs_deep_scan_hour=int(subtitle["AUDIO_SUBS_DEEP_SCAN_HOUR"]),
        audio_subs_deep_scan_batch=int(subtitle["AUDIO_SUBS_DEEP_SCAN_BATCH"]),
    )


def normalize_due_occurrence(
    definition: ScheduleDefinition,
    schedule: ScheduleValue,
    configuration: ScheduleConfiguration,
) -> datetime | None:
    """Normalize one PgQueuer pick into the catalog's UTC occurrence identity."""
    value = schedule.updated
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("PgQueuer schedule value must be timezone-aware")
    current = value.astimezone(UTC).replace(microsecond=0)
    if definition.occurrence_policy == OccurrencePolicy.EXACT_SECOND:
        return current
    if definition.occurrence_policy == OccurrencePolicy.INTERVAL_BUCKET:
        interval = configuration.sync_interval_minutes
        if interval < 1:
            return None
        epoch_minutes = int(current.timestamp()) // 60
        bucket_minutes = epoch_minutes - epoch_minutes % interval
        return datetime.fromtimestamp(bucket_minutes * 60, tz=UTC)
    due = current.replace(minute=0, second=0)
    if due.hour != configuration.audio_subs_deep_scan_hour:
        return None
    return due


def occurrence_key(definition: ScheduleDefinition, due: datetime) -> str:
    return f"schedule:{definition.key}:{_format_utc(due)}"


async def submit_schedule_occurrence(
    definition: ScheduleDefinition,
    schedule: ScheduleValue,
    *,
    configuration_loader: Callable[[], ScheduleConfiguration] = load_schedule_configuration,
    diagnostics: ScheduleDiagnostics = schedule_diagnostics,
    session_factory: Callable[[], AsyncSession] | None = None,
) -> SubmissionResult | None:
    """Submit one ordinary canonical job or record a bounded non-occurrence."""
    configuration = configuration_loader()
    due = normalize_due_occurrence(definition, schedule, configuration)
    diagnostic_due = (
        due
        if due is not None
        else schedule.updated.astimezone(UTC).replace(microsecond=0)
    )
    if due is None:
        diagnostics.record(
            definition,
            due=diagnostic_due,
            disposition="ineligible",
            reason="outside occurrence window",
        )
        return None
    if not definition.enabled_predicate(configuration):
        diagnostics.record(
            definition,
            due=due,
            disposition="disabled",
            reason="catalog predicate disabled",
        )
        return None
    factory = session_factory or _get_session_factory()
    async with factory() as session, session.begin():
        result = await submit_job(
            session,
            job_type=definition.produced_job_type,
            request=definition.request_builder(configuration, due),
            subject=definition.subject_builder(configuration, due),
            trigger=definition.trigger,
            initiator=definition.initiator,
            idempotency_key=occurrence_key(definition, due),
        )
    diagnostics.record(
        definition,
        due=due,
        disposition=result.disposition,
    )
    return result


def register_schedule_callbacks(
    app: PgQueuer,
    *,
    catalog: ScheduleCatalog,
    configuration_loader: Callable[[], ScheduleConfiguration] = load_schedule_configuration,
    diagnostics: ScheduleDiagnostics = schedule_diagnostics,
) -> None:
    """Register catalog callbacks without importing routes or product handlers."""
    for definition in catalog:
        async def callback(schedule, *, _definition=definition) -> None:
            await submit_schedule_occurrence(
                _definition,
                schedule,
                configuration_loader=configuration_loader,
                diagnostics=diagnostics,
            )

        callback.__name__ = definition.entrypoint
        app.schedule(definition.entrypoint, definition.expression)(callback)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


_SCHEDULER_INITIATOR = Initiator(
    kind="system",
    identifier="pgqueuer-scheduler",
    display_name="PgQueuer scheduler",
)


PRODUCTION_SCHEDULE_CATALOG = ScheduleCatalog(
    (
        ScheduleDefinition(
            key="library-sync",
            entrypoint="schedule_library_sync",
            expression="* * * * *",
            produced_job_type="library_sync",
            trigger=TriggerKind.SCHEDULE,
            initiator=_SCHEDULER_INITIATOR,
            enabled_predicate=lambda config: (
                config.production_occurrences_enabled and config.sync_interval_minutes > 0
            ),
            occurrence_policy=OccurrencePolicy.INTERVAL_BUCKET,
            request_builder=lambda _config, _due: {"source": "schedule"},
            subject_builder=lambda _config, _due: SubjectLocator(
                kind="maintenance_scope", reference="library-sync"
            ),
        ),
        ScheduleDefinition(
            key="audio-subs-deep-scan",
            entrypoint="schedule_audio_subs_deep_scan",
            expression="0 * * * *",
            produced_job_type="audio_subs_deep_scan",
            trigger=TriggerKind.SCHEDULE,
            initiator=_SCHEDULER_INITIATOR,
            enabled_predicate=lambda config: (
                config.production_occurrences_enabled
                and config.audio_subs_deep_scan_enabled
            ),
            occurrence_policy=OccurrencePolicy.HOURLY_WINDOW,
            request_builder=lambda _config, _due: {"scope": "all"},
            subject_builder=lambda _config, _due: SubjectLocator(
                kind="maintenance_scope", reference="audio-subs-deep-scan"
            ),
        ),
    )
)


FIXED_TEST_SCHEDULE_CATALOG = ScheduleCatalog(
    (
        ScheduleDefinition(
            key="fixed-noop",
            entrypoint="schedule_fixed_noop",
            expression="*/1 * * * * *",
            produced_job_type="system_noop",
            trigger=TriggerKind.SCHEDULE,
            initiator=Initiator(kind="system", identifier="fixed-noop"),
            enabled_predicate=lambda config: config.production_occurrences_enabled,
            occurrence_policy=OccurrencePolicy.EXACT_SECOND,
            request_builder=lambda _config, due: {"echo": {"due": due.isoformat()}},
            subject_builder=lambda _config, _due: SubjectLocator(
                kind="system_work", reference="system_noop"
            ),
        ),
    )
)
