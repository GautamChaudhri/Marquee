"""Frozen JMC2C presentation contract: typed values, sections, and `JobPresentation`.

Serialized shapes here are product contracts.  Presenters return these types
only — never HTML, component names, arbitrary JSON blocks, raw transport rows,
or internal machine status labels as primary text.  Additions to the section
or value vocabulary are explicit schema/API changes.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, field_validator

from marquee.core.jobs.contracts import (
    AttentionLevel,
    AttentionReason,
    FeatureArea,
    JobAction,
    TriggerKind,
)
from marquee.core.jobs.documents import StrictDocument
from marquee.core.jobs.progress import (
    ProgressFreshness,
    ProgressMeasurement,
    ProgressMetrics,
    ProgressWait,
)

PRESENTATION_VERSION = 1

_TONES = ("neutral", "active", "positive", "warning", "negative")


def _validate_safe_href(href: str) -> str:
    if not href.startswith("/") or href.startswith("//") or "://" in href:
        raise ValueError("presentation links must be relative application paths")
    if any(ch in href for ch in ("\n", "\r", " ")):
        raise ValueError("presentation links must not contain whitespace")
    return href


class TextValue(StrictDocument):
    type: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=500)


class NumberValue(StrictDocument):
    type: Literal["number"] = "number"
    value: float
    unit: str | None = Field(default=None, max_length=40)

    @field_validator("value")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("presentation numbers must be finite")
        return value


class DurationValue(StrictDocument):
    type: Literal["duration"] = "duration"
    seconds: float = Field(ge=0)

    @field_validator("seconds")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("durations must be finite")
        return value


class BytesValue(StrictDocument):
    type: Literal["bytes"] = "bytes"
    bytes: int = Field(ge=0)


class TimestampValue(StrictDocument):
    type: Literal["timestamp"] = "timestamp"
    at: datetime


class BooleanValue(StrictDocument):
    type: Literal["boolean"] = "boolean"
    value: bool


class BadgeValue(StrictDocument):
    type: Literal["badge"] = "badge"
    text: str = Field(min_length=1, max_length=100)
    tone: Literal["neutral", "active", "positive", "warning", "negative"] = "neutral"


class SubjectRefValue(StrictDocument):
    type: Literal["subject"] = "subject"
    kind: str = Field(min_length=1, max_length=40)
    display_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=500)
    artwork_key: str | None = Field(default=None, max_length=200)


class LinkValue(StrictDocument):
    type: Literal["link"] = "link"
    href: str = Field(min_length=1, max_length=500)
    label: str = Field(min_length=1, max_length=200)

    @field_validator("href")
    @classmethod
    def safe_href(cls, value: str) -> str:
        return _validate_safe_href(value)


PresentationValue = Annotated[
    TextValue
    | NumberValue
    | DurationValue
    | BytesValue
    | TimestampValue
    | BooleanValue
    | BadgeValue
    | SubjectRefValue
    | LinkValue,
    Field(discriminator="type"),
]
PRESENTATION_VALUE_ADAPTER: TypeAdapter = TypeAdapter(PresentationValue)


class Fact(StrictDocument):
    label: str = Field(min_length=1, max_length=200)
    label_key: str | None = Field(default=None, max_length=120)
    value: PresentationValue


class FactsSection(StrictDocument):
    kind: Literal["facts"] = "facts"
    title: str | None = Field(default=None, max_length=200)
    facts: tuple[Fact, ...] = Field(min_length=1, max_length=100)


class BeforeAfterRow(StrictDocument):
    label: str = Field(min_length=1, max_length=200)
    before: PresentationValue | None = None
    after: PresentationValue | None = None
    changed: bool


class BeforeAfterSection(StrictDocument):
    kind: Literal["before_after"] = "before_after"
    title: str | None = Field(default=None, max_length=200)
    rows: tuple[BeforeAfterRow, ...] = Field(min_length=1, max_length=100)


TargetOutcome = Literal["succeeded", "failed", "skipped", "not_applied"]


class ChangeItem(StrictDocument):
    target_key: str = Field(min_length=1, max_length=200)
    target_label: str = Field(min_length=1, max_length=300)
    requested: str = Field(min_length=1, max_length=300)
    outcome: TargetOutcome
    stage: str | None = Field(default=None, max_length=100)
    reason: str | None = Field(default=None, max_length=500)


class ChangeListSection(StrictDocument):
    kind: Literal["change_list"] = "change_list"
    title: str | None = Field(default=None, max_length=200)
    items: tuple[ChangeItem, ...] = Field(min_length=1, max_length=200)


class TrackRow(StrictDocument):
    track_kind: Literal["audio", "subtitle"]
    language: str = Field(default="und", max_length=20)
    codec: str | None = Field(default=None, max_length=60)
    channels: int | None = Field(default=None, ge=0)
    title: str | None = Field(default=None, max_length=300)
    is_default: bool = False
    is_forced: bool = False
    is_sdh: bool = False
    is_commentary: bool = False
    embedded: bool = True
    requested: str | None = Field(default=None, max_length=300)
    outcome: TargetOutcome | None = None
    reason: str | None = Field(default=None, max_length=500)


class TrackTableSection(StrictDocument):
    kind: Literal["track_table"] = "track_table"
    title: str | None = Field(default=None, max_length=200)
    tracks: tuple[TrackRow, ...] = Field(min_length=1, max_length=200)


class MetricCard(StrictDocument):
    label: str = Field(min_length=1, max_length=200)
    value: PresentationValue
    interpretation: str | None = Field(default=None, max_length=300)


class MetricCardsSection(StrictDocument):
    kind: Literal["metric_cards"] = "metric_cards"
    title: str | None = Field(default=None, max_length=200)
    cards: tuple[MetricCard, ...] = Field(min_length=1, max_length=24)


class WarningItem(StrictDocument):
    code: str | None = Field(default=None, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)


class WarningsSection(StrictDocument):
    kind: Literal["warnings"] = "warnings"
    items: tuple[WarningItem, ...] = Field(min_length=1, max_length=100)


class FailureItem(StrictDocument):
    stage: str | None = Field(default=None, max_length=100)
    target: str | None = Field(default=None, max_length=300)
    code: str | None = Field(default=None, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)
    media_changed: bool | None = None
    atomicity_held: bool | None = None
    remediation: str | None = Field(default=None, max_length=500)


class FailuresSection(StrictDocument):
    kind: Literal["failures"] = "failures"
    items: tuple[FailureItem, ...] = Field(min_length=1, max_length=100)


class Step(StrictDocument):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]*$")
    label: str = Field(min_length=1, max_length=200)
    state: Literal["pending", "running", "succeeded", "failed", "skipped", "cancelled"]
    at: datetime | None = None


class StepsSection(StrictDocument):
    kind: Literal["steps"] = "steps"
    steps: tuple[Step, ...] = Field(min_length=1, max_length=50)


ArtifactStatus = Literal[
    "available", "missing", "expired", "quarantined", "deleted", "unavailable"
]


class ArtifactItem(StrictDocument):
    artifact_kind: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=300)
    status: ArtifactStatus
    content_type: str | None = Field(default=None, max_length=100)
    size_bytes: int | None = Field(default=None, ge=0)
    retention: str | None = Field(default=None, max_length=200)
    link: LinkValue | None = None


class ArtifactsSection(StrictDocument):
    kind: Literal["artifacts"] = "artifacts"
    items: tuple[ArtifactItem, ...] = Field(min_length=1, max_length=50)


class ChildrenSection(StrictDocument):
    kind: Literal["children"] = "children"
    total: int = Field(ge=0)
    queued: int = Field(default=0, ge=0)
    running: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    no_change: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    sealed: bool = True
    children_link: LinkValue


class NoticeSection(StrictDocument):
    kind: Literal["notice"] = "notice"
    tone: Literal["info", "success", "warning"] = "info"
    message: str = Field(min_length=1, max_length=500)


PresentationSection = Annotated[
    FactsSection
    | BeforeAfterSection
    | ChangeListSection
    | TrackTableSection
    | MetricCardsSection
    | WarningsSection
    | FailuresSection
    | StepsSection
    | ArtifactsSection
    | ChildrenSection
    | NoticeSection,
    Field(discriminator="kind"),
]
PRESENTATION_SECTION_ADAPTER: TypeAdapter = TypeAdapter(PresentationSection)

SECTION_KINDS = frozenset(
    {
        "facts",
        "before_after",
        "change_list",
        "track_table",
        "metric_cards",
        "warnings",
        "failures",
        "steps",
        "artifacts",
        "children",
        "notice",
    }
)

VALUE_TYPES = frozenset(
    {
        "text",
        "number",
        "duration",
        "bytes",
        "timestamp",
        "boolean",
        "badge",
        "subject",
        "link",
    }
)


class PresentationSubject(StrictDocument):
    kind: str = Field(min_length=1, max_length=40)
    display_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=500)
    artwork_key: str | None = Field(default=None, max_length=200)
    context: tuple[str, ...] = Field(default=(), max_length=6)
    snapshot_at: datetime | None = None
    missing_live_subject: bool = False

    @field_validator("context")
    @classmethod
    def bound_context(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or len(item) > 300 for item in value):
            raise ValueError("subject context lines must be non-empty and bounded")
        return value


class PresentationAction(StrictDocument):
    headline: str = Field(min_length=1, max_length=300)
    explanation: str | None = Field(default=None, max_length=1000)


class PresentationTrigger(StrictDocument):
    kind: TriggerKind
    label: str = Field(min_length=1, max_length=200)
    initiator: str | None = Field(default=None, max_length=200)


class PresentationAttention(StrictDocument):
    level: AttentionLevel = AttentionLevel.NORMAL
    reason: AttentionReason = AttentionReason.NONE
    message: str | None = Field(default=None, max_length=500)
    remediation: str | None = Field(default=None, max_length=500)


class PresentationStatus(StrictDocument):
    phase: Literal["planned", "queued", "running", "stopping", "terminal"]
    outcome: (
        Literal[
            "succeeded",
            "partially_succeeded",
            "no_change",
            "failed",
            "cancelled",
            "superseded",
            "dead_letter",
            "unsafe",
        ]
        | None
    ) = None
    label: str = Field(min_length=1, max_length=100)
    label_key: str = Field(min_length=1, max_length=120)
    tone: Literal["neutral", "active", "positive", "warning", "negative"] = "neutral"


class CompactProgress(StrictDocument):
    """Compact typed projection of the durable `JobProgress` snapshot."""

    headline: str | None = Field(default=None, max_length=500)
    stage_key: str | None = Field(default=None, max_length=100)
    stage_label: str | None = Field(default=None, max_length=200)
    overall: ProgressMeasurement | None = None
    current: ProgressMeasurement | None = None
    current_subject: PresentationSubject | None = None
    metrics: ProgressMetrics = Field(default_factory=ProgressMetrics)
    freshness: ProgressFreshness | None = None
    wait: ProgressWait | None = None
    updated_at: datetime | None = None
    sequence: int | None = Field(default=None, ge=1)


class PresentationImpact(StrictDocument):
    input_bytes: int | None = Field(default=None, ge=0)
    output_bytes: int | None = Field(default=None, ge=0)
    storage_delta_bytes: int | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    items_processed: int | None = Field(default=None, ge=0)
    label: str | None = Field(default=None, max_length=300)


class SuggestedAction(StrictDocument):
    label: str = Field(min_length=1, max_length=200)
    action: JobAction | None = None
    link: LinkValue | None = None


class EvidenceAvailability(StrictDocument):
    logs_available: bool = False
    artifacts_available: bool = False


class DiagnosticLinks(StrictDocument):
    detail: str
    snapshot: str
    presentation: str
    attempts: str
    events: str
    artifacts: str
    children: str | None = None
    raw_request: str | None = None
    raw_plan: str | None = None
    raw_result: str | None = None
    raw_error: str | None = None

    @field_validator(
        "detail",
        "snapshot",
        "presentation",
        "attempts",
        "events",
        "artifacts",
        "children",
        "raw_request",
        "raw_plan",
        "raw_result",
        "raw_error",
    )
    @classmethod
    def safe_href(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_safe_href(value)


class JobPresentation(StrictDocument):
    version: Literal[1] = 1
    presenter_key: str = Field(min_length=1, max_length=120)
    presenter_version: int = Field(ge=1)
    job_id: str = Field(min_length=1, max_length=32)
    job_type: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    label_key: str = Field(min_length=1, max_length=120)
    feature_area: FeatureArea
    presentation_family: str = Field(min_length=1, max_length=80)
    subject: PresentationSubject
    action: PresentationAction
    trigger: PresentationTrigger
    attention: PresentationAttention
    allowed_actions: tuple[JobAction, ...] = ()
    status: PresentationStatus
    progress: CompactProgress | None = None
    impact: PresentationImpact | None = None
    sections: tuple[PresentationSection, ...] = Field(default=(), max_length=24)
    warnings: tuple[WarningItem, ...] = Field(default=(), max_length=100)
    failures: tuple[FailureItem, ...] = Field(default=(), max_length=100)
    suggested_actions: tuple[SuggestedAction, ...] = Field(default=(), max_length=8)
    evidence: EvidenceAvailability = Field(default_factory=EvidenceAvailability)
    links: DiagnosticLinks

    @field_validator("allowed_actions")
    @classmethod
    def unique_actions(cls, value: tuple[JobAction, ...]) -> tuple[JobAction, ...]:
        if len(set(value)) != len(value):
            raise ValueError("allowed actions must be unique")
        return value


class RowLinks(StrictDocument):
    detail: str
    snapshot: str
    presentation: str

    @field_validator("detail", "snapshot", "presentation")
    @classmethod
    def safe_href(cls, value: str) -> str:
        return _validate_safe_href(value)


class JobRow(StrictDocument):
    """Compact typed Queue/History row — the only shape list responses return."""

    version: Literal[1] = 1
    job_id: str = Field(min_length=1, max_length=32)
    job_type: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    label_key: str = Field(min_length=1, max_length=120)
    feature_area: FeatureArea
    presentation_family: str = Field(min_length=1, max_length=80)
    subject: PresentationSubject
    action_headline: str = Field(min_length=1, max_length=300)
    status: PresentationStatus
    trigger: PresentationTrigger
    attention: PresentationAttention
    progress: CompactProgress | None = None
    impact: PresentationImpact | None = None
    allowed_actions: tuple[JobAction, ...] = ()
    is_parent: bool = False
    parent_id: str | None = Field(default=None, max_length=32)
    root_id: str | None = Field(default=None, max_length=32)
    retry_of_job_id: str | None = Field(default=None, max_length=32)
    fence_token: int = Field(ge=0)
    execution_class: str = Field(min_length=1, max_length=40)
    priority: int
    queue_rank: int | None = Field(default=None, ge=1)
    eligible_at: datetime | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    terminal_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    evidence: EvidenceAvailability = Field(default_factory=EvidenceAvailability)
    links: RowLinks

    @field_validator("allowed_actions")
    @classmethod
    def unique_actions(cls, value: tuple[JobAction, ...]) -> tuple[JobAction, ...]:
        if len(set(value)) != len(value):
            raise ValueError("allowed actions must be unique")
        return value
