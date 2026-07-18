"""Versioned, path-free contracts shared by every destructive job family."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import Field, JsonValue, field_validator, model_validator

from marquee.core.jobs.documents import SafeJobErrorV1, StrictDocument

_KEY = re.compile(r"^[A-Za-z0-9._:-]+(?:/[A-Za-z0-9._:-]+)*$")
_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
Checksum = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")]


def _bounded_facts(value: dict[str, JsonValue], *, label: str) -> dict[str, JsonValue]:
    if len(value) > 64:
        raise ValueError(f"{label} is bounded to 64 keys")
    stack: list[JsonValue] = list(value.values())
    count = 0
    while stack:
        item = stack.pop()
        count += 1
        if count > 512:
            raise ValueError(f"{label} is too deeply populated")
        if isinstance(item, str):
            if len(item) > 500:
                raise ValueError(f"{label} strings are bounded")
            if item.startswith(("/", "\\")) or "\\" in item or "/../" in f"/{item}/":
                raise ValueError(f"{label} cannot expose physical paths")
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return value


def _storage_key(value: str) -> str:
    if not _KEY.fullmatch(value) or ".." in value.split("/"):
        raise ValueError("storage keys must be confined logical keys")
    return value


class MutationTargetStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLIED = "not_applied"


class MutationJobOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    NO_CHANGE = "no_change"
    UNSAFE = "unsafe"


class MutationTargetV1(StrictDocument):
    key: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._:-]+$")
    kind: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=300)
    operation: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    selector_facts: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("selector_facts")
    @classmethod
    def bound_selector_facts(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _bounded_facts(value, label="selector facts")


class MutationSnapshotV1(StrictDocument):
    identity: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._:-]+$")
    signature: str | None = Field(default=None, min_length=8, max_length=160)
    checksum: Checksum | None = None
    size_bytes: int | None = Field(default=None, ge=0, le=1024**4)
    facts: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("facts")
    @classmethod
    def bound_facts(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _bounded_facts(value, label="snapshot facts")


class MutationInvariantV1(StrictDocument):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    passed: bool
    message: str = Field(min_length=1, max_length=500)


class MutationValidationV1(StrictDocument):
    source_probe: dict[str, JsonValue] = Field(default_factory=dict)
    output_probe: dict[str, JsonValue] = Field(default_factory=dict)
    invariant_checks: tuple[MutationInvariantV1, ...] = Field(default=(), max_length=64)
    warnings: tuple[str, ...] = Field(default=(), max_length=32)
    verdict: Literal["passed", "failed", "not_run"]

    @field_validator("source_probe", "output_probe")
    @classmethod
    def bound_probes(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _bounded_facts(value, label="probe summary")

    @field_validator("warnings")
    @classmethod
    def bound_warnings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not warning or len(warning) > 500 for warning in value):
            raise ValueError("validation warnings must be non-empty and bounded")
        return value


class MutationAtomicityV1(StrictDocument):
    group_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    boundary: Literal["single_target", "all_or_nothing", "partial_group"]
    published: bool
    rollback_available: bool
    uncertain_state: bool

    @model_validator(mode="after")
    def uncertainty_is_not_publication_proof(self) -> MutationAtomicityV1:
        if self.uncertain_state and self.published:
            raise ValueError("uncertain publication cannot be recorded as proven published")
        return self


class MutationBackupV1(StrictDocument):
    artifact_key: str
    checksum: Checksum
    size_bytes: int = Field(ge=0, le=1024**4)
    source_signature: str = Field(min_length=8, max_length=160)
    retention: str = Field(min_length=1, max_length=120)
    restore_eligible: bool

    _confine_artifact_key = field_validator("artifact_key")(_storage_key)


class MutationPublishV1(StrictDocument):
    candidate_checksum: Checksum
    candidate_signature: str = Field(min_length=8, max_length=160)
    destination_identity: str = Field(
        min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    fence_token: int = Field(ge=1)
    fsync_result: Literal["succeeded", "failed", "not_attempted", "unknown"]
    replace_result: Literal["succeeded", "failed", "not_attempted", "unknown"]
    post_publish_rescan: dict[str, JsonValue] = Field(default_factory=dict)
    reconciliation_state: Literal["not_required", "reconciled", "required", "quarantined"]

    @field_validator("post_publish_rescan")
    @classmethod
    def bound_rescan(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _bounded_facts(value, label="post-publish rescan")


class MutationTargetOutcomeV1(StrictDocument):
    target: MutationTargetV1
    status: MutationTargetStatus
    stage: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    message: str = Field(min_length=1, max_length=500)
    before: MutationSnapshotV1 | None = None
    expected: MutationSnapshotV1 | None = None
    actual: MutationSnapshotV1 | None = None
    bytes_changed: bool
    product_state_changed: bool

    @model_validator(mode="after")
    def unsuccessful_targets_cannot_claim_change(self) -> MutationTargetOutcomeV1:
        if self.status != MutationTargetStatus.SUCCEEDED and (
            self.bytes_changed or self.product_state_changed
        ):
            raise ValueError("only succeeded targets may report an applied change")
        return self


class MutationResultV1(StrictDocument):
    outcome: MutationJobOutcome
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    message: str = Field(min_length=1, max_length=1000)
    requested_targets: tuple[MutationTargetV1, ...] = Field(min_length=1, max_length=10_000)
    target_outcomes: tuple[MutationTargetOutcomeV1, ...] = Field(
        min_length=1, max_length=10_000
    )
    validation: MutationValidationV1
    atomicity: MutationAtomicityV1
    backup: MutationBackupV1 | None = None
    publish: MutationPublishV1 | None = None

    @model_validator(mode="after")
    def enforce_truthful_outcomes(self) -> MutationResultV1:
        requested = [target.key for target in self.requested_targets]
        observed = [outcome.target.key for outcome in self.target_outcomes]
        if len(set(requested)) != len(requested) or sorted(requested) != sorted(observed):
            raise ValueError("every requested target requires exactly one target outcome")
        if self.outcome == MutationJobOutcome.NO_CHANGE:
            if self.reason_code == "no_change" or not self.reason_code:
                raise ValueError("no_change requires a specific reason code")
            if any(
                outcome.bytes_changed or outcome.product_state_changed
                for outcome in self.target_outcomes
            ):
                raise ValueError("no_change cannot report applied changes")
        if (
            self.atomicity.boundary == "all_or_nothing"
            and self.outcome
            in {MutationJobOutcome.FAILED, MutationJobOutcome.CANCELLED}
            and any(
                outcome.status != MutationTargetStatus.NOT_APPLIED
                for outcome in self.target_outcomes
            )
        ):
            raise ValueError("failed all-or-nothing work must report every target not_applied")
        if self.atomicity.uncertain_state and self.outcome != MutationJobOutcome.UNSAFE:
            raise ValueError("uncertain publication requires the unsafe job outcome")
        if self.outcome == MutationJobOutcome.UNSAFE and not self.atomicity.uncertain_state:
            raise ValueError("unsafe mutation outcomes require uncertain-state evidence")
        return self


class MutationErrorV1(SafeJobErrorV1):
    stage: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    target_outcomes: tuple[MutationTargetOutcomeV1, ...] = Field(default=(), max_length=10_000)
    atomicity: MutationAtomicityV1 | None = None
    publish: MutationPublishV1 | None = None


class MutationEvidenceV1(StrictDocument):
    requested_target: MutationTargetV1
    expected_target: MutationSnapshotV1 | None = None
    actual_target: MutationSnapshotV1 | None = None
    validation: MutationValidationV1
    atomicity: MutationAtomicityV1
    backup: MutationBackupV1 | None = None
    publish: MutationPublishV1 | None = None


class PosterCandidateSelectionV1(StrictDocument):
    source: Literal["pipeline_run", "subject_artwork"]
    storage_key: str
    run_id: str | None = Field(default=None, min_length=1, max_length=80)
    candidate_reference: str | None = Field(
        default=None, min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._-]+$"
    )
    source_kind: Literal["movie", "series", "season"] | None = None
    source_id: int | None = Field(default=None, ge=1)
    expected_checksum: Checksum
    selection_facts: dict[str, JsonValue] = Field(default_factory=dict)

    _confine_storage_key = field_validator("storage_key")(_storage_key)

    @field_validator("selection_facts")
    @classmethod
    def bound_selection_facts(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _bounded_facts(value, label="selection facts")

    @model_validator(mode="after")
    def require_exact_source_identity(self) -> PosterCandidateSelectionV1:
        if self.source == "pipeline_run":
            if not self.run_id or not self.candidate_reference:
                raise ValueError("pipeline selections require run and candidate identities")
            if self.source_kind is not None or self.source_id is not None:
                raise ValueError("pipeline selections cannot carry subject-source fields")
        elif self.source_kind is None or self.source_id is None:
            raise ValueError("subject artwork selections require a subject identity")
        elif self.run_id is not None or self.candidate_reference is not None:
            raise ValueError("subject artwork selections cannot carry pipeline fields")
        return self


class PosterDeployRequestV1(StrictDocument):
    target_kind: Literal["movie", "series", "season"]
    target_id: int = Field(ge=1)
    candidate: PosterCandidateSelectionV1
    ai_selected: bool = True
    user_approved: bool = False


class PosterRestoreRequestV1(StrictDocument):
    target_kind: Literal["movie", "series", "season"]
    target_id: int = Field(ge=1)
    allowed_sources: tuple[Literal["backup", "cache"], ...] = Field(
        default=("backup", "cache"), min_length=1, max_length=2
    )

    @field_validator("allowed_sources")
    @classmethod
    def unique_sources(
        cls, value: tuple[Literal["backup", "cache"], ...]
    ) -> tuple[Literal["backup", "cache"], ...]:
        if len(set(value)) != len(value):
            raise ValueError("restore sources must be unique")
        return value


class PosterResetRequestV1(StrictDocument):
    target_kind: Literal["movie", "series", "season"]
    target_id: int = Field(ge=1)
    preserve_cache: bool = True


class PosterBackupRequestV1(StrictDocument):
    target_kind: Literal["movie", "series", "season"]
    target_id: int = Field(ge=1)
    retention: Literal["recoverable_artwork"] = "recoverable_artwork"


class PosterParentRequestV1(StrictDocument):
    operation: Literal["reset", "backup", "heal"]
    scope: Literal["all", "missing"]
    selection_count: int = Field(ge=0, le=10_000)
    unchanged_count: int = Field(default=0, ge=0, le=10_000)
    unsupported_count: int = Field(default=0, ge=0, le=10_000)


class BackupCreateRequestV1(StrictDocument):
    reason: Literal["manual", "scheduled", "pre_maintenance"] = "manual"


class PosterMaintenanceRequestV1(StrictDocument):
    dry_run: bool = True
    confirmed_plan_checksum: Checksum | None = None
    max_items: int = Field(default=10_000, ge=1, le=10_000)
    batch_size: int = Field(default=100, ge=1, le=500)


class PipelineCacheClearRequestV1(StrictDocument):
    include_embeddings: bool = True
    include_archives: bool = False
    dry_run: bool = True
    confirmed_plan_checksum: Checksum | None = None
    max_items: int = Field(default=10_000, ge=1, le=10_000)
    batch_size: int = Field(default=100, ge=1, le=500)


class RetentionPurgeRequestV1(StrictDocument):
    retention_days: int = Field(ge=1, le=3650)
    evidence_only: bool = False
    dry_run: bool = True
    confirmed_plan_checksum: Checksum | None = None
    max_records: int = Field(default=10_000, ge=1, le=10_000)
    batch_size: int = Field(default=100, ge=1, le=500)


class MetricsPurgeRequestV1(StrictDocument):
    retention_days: int = Field(ge=1, le=365)
    dry_run: bool = True
    confirmed_plan_checksum: Checksum | None = None
    max_records: int = Field(default=10_000, ge=1, le=10_000)
    batch_size: int = Field(default=100, ge=1, le=500)


class MaintenanceBackupEvidenceV1(StrictDocument):
    backup_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    created_at: str = Field(min_length=1, max_length=80)
    database_checksum: Checksum
    state_checksum: Checksum
    manifest_checksum: Checksum
    database_size: int = Field(ge=0, le=1024**5)
    state_size: int = Field(ge=0, le=1024**5)


class MaintenanceResultV1(StrictDocument):
    outcome: Literal["succeeded", "no_change", "partially_succeeded"]
    operation: Literal[
        "backup_create",
        "poster_maintenance",
        "pipeline_cache_clear",
        "job_retention_purge",
        "system_metrics_purge",
    ]
    message: str = Field(min_length=1, max_length=500)
    dry_run: bool = False
    plan_checksum: Checksum
    planned_count: int = Field(ge=0, le=10_000)
    processed_count: int = Field(ge=0, le=10_000)
    deleted_count: int = Field(ge=0, le=10_000)
    counts: dict[str, int] = Field(default_factory=dict)
    cancelled: bool = False
    backup: MaintenanceBackupEvidenceV1 | None = None

    @field_validator("counts")
    @classmethod
    def bound_counts(cls, value: dict[str, int]) -> dict[str, int]:
        if len(value) > 32 or any(
            not _KEY.fullmatch(key) or count < 0 or count > 10_000
            for key, count in value.items()
        ):
            raise ValueError("maintenance counts are invalid")
        return value


class MaintenanceErrorV1(StrictDocument):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    summary: str = Field(min_length=1, max_length=500)
    operation: str = Field(default="maintenance", min_length=1, max_length=80)
    plan_checksum: Checksum | None = None
    processed_count: int = Field(default=0, ge=0, le=10_000)
    deleted_count: int = Field(default=0, ge=0, le=10_000)


class PosterMutationResultV1(MutationResultV1):
    """Family-specific leaf result; common fields retain one shared vocabulary."""


class MutationCoordinatorProtocol(Protocol):
    """The ordered authority used by family-specific destructive coordinators."""

    async def resolve(self) -> MutationTargetV1: ...

    async def persist_before(self) -> MutationSnapshotV1: ...

    async def stage(self) -> MutationSnapshotV1: ...

    async def validate(self) -> MutationValidationV1: ...

    async def recheck(self) -> None: ...

    async def publish(self) -> MutationPublishV1: ...

    async def rescan(self) -> MutationSnapshotV1: ...

    async def persist_result(self, result: MutationResultV1) -> None: ...


class MutationPreconditionError(RuntimeError):
    def __init__(self, code: str):
        if not _CODE.fullmatch(code):
            raise ValueError("mutation precondition codes are bounded safe codes")
        super().__init__(code)
        self.code = code


def require_publication_preconditions(
    *, fence_current: bool, cancellation_requested: bool, source_current: bool, destination_confined: bool
) -> None:
    """Final coordinator guard immediately before calling the publication service."""
    if not fence_current:
        raise MutationPreconditionError("stale_fence")
    if cancellation_requested:
        raise MutationPreconditionError("cancel_requested")
    if not source_current:
        raise MutationPreconditionError("stale_source")
    if not destination_confined:
        raise MutationPreconditionError("unconfined_destination")


def publication_reconciliation_state(
    *, intent_recorded: bool, publication_recorded: bool
) -> Literal["not_required", "reconciled", "required"]:
    """Classify crash evidence without guessing whether a side effect occurred."""
    if publication_recorded:
        return "reconciled"
    if intent_recorded:
        return "required"
    return "not_required"
