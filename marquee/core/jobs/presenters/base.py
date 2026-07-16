"""Presentation engine: validated snapshot-first context and shared assembly.

Presenters consume validated canonical documents and immutable subject
snapshots.  Optional live data may enrich a presentation but is never required
to render history.  Malformed optional evidence produces a warning and omits
only the affected content; invalid required canonical documents raise
`PresentationIntegrityError` instead of fabricating data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from marquee.core.jobs.contracts import (
    AttentionLevel,
    AttentionReason,
    JobAction,
    TriggerKind,
)
from marquee.core.jobs.definitions import JobDefinition
from marquee.core.jobs.documents import (
    BuiltInResultV1,
    DocumentVersionError,
    SafeJobErrorV1,
    StrictDocument,
)
from marquee.core.jobs.labels import humanize_job_type
from marquee.core.jobs.mutation_documents import MutationEvidenceV1
from marquee.core.jobs.mutation_evidence import read_mutation_evidence
from marquee.core.jobs.policies import ActionContext, allowed_actions
from marquee.core.jobs.presentation import (
    CompactProgress,
    DiagnosticLinks,
    EvidenceAvailability,
    Fact,
    FactsSection,
    FailureItem,
    FailuresSection,
    JobPresentation,
    JobRow,
    LinkValue,
    NoticeSection,
    PresentationAction,
    PresentationAttention,
    PresentationImpact,
    PresentationSection,
    PresentationStatus,
    PresentationSubject,
    PresentationTrigger,
    RowLinks,
    SuggestedAction,
    TextValue,
    WarningItem,
)
from marquee.core.jobs.progress import JobProgress
from marquee.core.jobs.subjects import SUBJECT_SNAPSHOT_ADAPTER, SubjectSnapshot

if TYPE_CHECKING:
    from marquee.models import Job, MediaOperationDetail


class PresentationIntegrityError(RuntimeError):
    """A required canonical document is invalid; presentation cannot proceed."""


_STATUS_LABELS: dict[str, tuple[str, str]] = {
    # phase/outcome -> (label, tone)
    "planned": ("Planned", "neutral"),
    "queued": ("Queued", "neutral"),
    "running": ("Running", "active"),
    "stopping": ("Stopping", "warning"),
    "succeeded": ("Succeeded", "positive"),
    "partially_succeeded": ("Partially succeeded", "warning"),
    "no_change": ("No change needed", "positive"),
    "failed": ("Failed", "negative"),
    "cancelled": ("Cancelled", "neutral"),
    "superseded": ("Superseded", "neutral"),
    "dead_letter": ("Needs attention", "negative"),
    "unsafe": ("Needs manual review", "negative"),
}

_TRIGGER_LABELS: dict[TriggerKind, str] = {
    TriggerKind.MANUAL: "Started manually",
    TriggerKind.SCHEDULE: "Scheduled",
    TriggerKind.POLICY: "Policy",
    TriggerKind.BATCH: "Batch",
    TriggerKind.PARENT: "Parent job",
    TriggerKind.HEALING: "Automatic healing",
    TriggerKind.SYSTEM: "System",
    TriggerKind.WEBHOOK: "Webhook",
}


@dataclass
class PresenterContext:
    """Validated evidence for one canonical job."""

    job: Job
    definition: JobDefinition
    subject: SubjectSnapshot
    request: StrictDocument | None
    result: BuiltInResultV1 | StrictDocument | None
    error: SafeJobErrorV1 | None
    progress: JobProgress | None
    mutation: MutationEvidenceV1 | None = None
    warnings: list[WarningItem] = field(default_factory=list)
    live: Mapping[str, Any] = field(default_factory=dict)
    live_subject_missing: bool = False
    logs_available: bool = False
    artifacts_available: bool = False

    def warn(self, code: str, message: str) -> None:
        self.warnings.append(WarningItem(code=code, message=message))

    @property
    def summary(self) -> Mapping[str, Any]:
        """Bounded result evidence, preserving legacy summaries beside typed fields."""
        if isinstance(self.result, BuiltInResultV1):
            return self.result.summary
        if isinstance(self.result, StrictDocument):
            payload = self.result.model_dump(mode="json", exclude_none=True)
            stored = payload.pop("summary", {})
            if not isinstance(stored, Mapping):
                self.warn("malformed_evidence", "Stored result summary has the wrong type.")
                stored = {}
            if stored:
                return dict(stored)
            # Existing V1 summaries remain authoritative for historic rows; newly typed
            # fields fill gaps without losing their bounded legacy presentation evidence.
            values = dict(payload)
            values.pop("outcome", None)
            values.pop("message", None)
            return values
        return {}

    def summary_value(
        self, key: str, kinds: type | tuple[type, ...], *, max_length: int = 300
    ) -> Any:
        """One tolerant summary field: wrong types warn and are omitted."""
        if key not in self.summary:
            return None
        value = self.summary[key]
        if kinds is bool and isinstance(value, bool):
            return value
        if isinstance(value, bool) and kinds is not bool:
            self.warn("malformed_evidence", f"Stored evidence field '{key}' has the wrong type.")
            return None
        if not isinstance(value, kinds):
            self.warn("malformed_evidence", f"Stored evidence field '{key}' has the wrong type.")
            return None
        if isinstance(value, str) and (not value or len(value) > max_length):
            self.warn("malformed_evidence", f"Stored evidence field '{key}' is out of bounds.")
            return None
        if isinstance(value, int | float) and not isinstance(value, bool) and abs(value) > 1e15:
            self.warn("malformed_evidence", f"Stored evidence field '{key}' is out of bounds.")
            return None
        return value


def load_context(
    job: Job,
    definition: JobDefinition,
    *,
    live: Mapping[str, Any] | None = None,
    live_subject_missing: bool = False,
    logs_available: bool = False,
    artifacts_available: bool = False,
    mutation_detail: MediaOperationDetail | None = None,
) -> PresenterContext:
    """Validate stored documents into a presenter context.

    Subject snapshots are required canonical evidence; the request document is
    validated strictly but a legacy-invalid request degrades to a warning so
    history stays explainable.  Result/error/progress are optional evidence.
    """
    warnings: list[WarningItem] = []

    try:
        subject = SUBJECT_SNAPSHOT_ADAPTER.validate_python(job.subject_snapshot)
    except ValidationError as exc:
        raise PresentationIntegrityError(
            f"job {job.id} has an invalid subject snapshot"
        ) from exc

    request: StrictDocument | None = None
    try:
        request = definition.request.validate(job.request or {}, version=job.payload_version)
    except (ValidationError, DocumentVersionError):
        warnings.append(
            WarningItem(
                code="malformed_evidence",
                message="The stored request document could not be validated.",
            )
        )

    result: StrictDocument | None = None
    if job.result is not None:
        try:
            result = definition.result.validate(job.result, version=job.result_version)
        except (ValidationError, DocumentVersionError):
            warnings.append(
                WarningItem(
                    code="malformed_evidence",
                    message="The stored result document could not be validated.",
                )
            )

    error: SafeJobErrorV1 | None = None
    if job.error is not None:
        try:
            validated = definition.error.validate(job.error, version=job.error_version)
            if isinstance(validated, SafeJobErrorV1):
                error = validated
        except (ValidationError, DocumentVersionError):
            warnings.append(
                WarningItem(
                    code="malformed_evidence",
                    message="The stored error document could not be validated.",
                )
            )

    progress: JobProgress | None = None
    if job.progress is not None:
        try:
            progress = JobProgress.model_validate(job.progress)
        except ValidationError:
            warnings.append(
                WarningItem(
                    code="malformed_evidence",
                    message="The stored progress snapshot could not be validated.",
                )
            )

    mutation: MutationEvidenceV1 | None = None
    if mutation_detail is not None:
        try:
            mutation = read_mutation_evidence(mutation_detail)
        except ValidationError:
            warnings.append(
                WarningItem(
                    code="malformed_evidence",
                    message="The stored mutation evidence could not be validated.",
                )
            )

    context = PresenterContext(
        job=job,
        definition=definition,
        subject=subject,
        request=request,
        result=result,
        error=error,
        progress=progress,
        mutation=mutation,
        live=dict(live or {}),
        live_subject_missing=live_subject_missing,
        logs_available=logs_available,
        artifacts_available=artifacts_available,
    )
    context.warnings.extend(warnings)
    return context


def subject_context_lines(subject: SubjectSnapshot) -> tuple[str, ...]:
    lines: list[str] = []
    kind = subject.kind
    if kind == "movie" and subject.year or kind == "series" and subject.year:
        lines.append(str(subject.year))
    elif kind == "season":
        lines.append(subject.series_title)
        lines.append(f"Season {subject.season_number}")
    elif kind == "episode":
        lines.append(subject.series_title)
        lines.append(f"Season {subject.season_number}")
        code = subject.episode_code
        if subject.episode_title:
            code = f"{code} — {subject.episode_title}"
        lines.append(code)
    elif kind == "media_file":
        if subject.movie_title:
            lines.append(subject.movie_title)
        elif subject.series_title:
            lines.append(subject.series_title)
            if subject.season_number is not None and subject.episode_number is not None:
                lines.append(
                    f"S{subject.season_number:02d}E{subject.episode_number:02d}"
                )
        if subject.container:
            lines.append(subject.container.upper())
    elif kind == "track":
        descriptor = subject.language
        if subject.codec:
            descriptor = f"{descriptor} · {subject.codec}"
        lines.append(descriptor)
        lines.append(subject.file_name)
        if subject.series_title:
            lines.append(subject.series_title)
    elif kind == "poster_candidate_set":
        if subject.year:
            lines.append(str(subject.year))
        if subject.season_number is not None:
            lines.append(f"Season {subject.season_number}")
    elif kind == "model_profile_training" and subject.model_name:
        lines.append(subject.model_name)
    elif kind == "aggregate_batch" and subject.scope_summary:
        lines.append(subject.scope_summary)
    elif kind == "maintenance_scope":
        lines.append("Dry run" if subject.dry_run else "Applies changes")
    return tuple(lines[:6])


def present_subject(
    subject: SubjectSnapshot, *, missing_live_subject: bool = False
) -> PresentationSubject:
    return PresentationSubject(
        kind=subject.kind,
        display_id=subject.display_id,
        display_name=subject.display_name,
        artwork_key=getattr(subject, "artwork_key", None),
        context=subject_context_lines(subject),
        snapshot_at=subject.snapshot_at,
        missing_live_subject=missing_live_subject,
    )


def present_status(job: Job) -> PresentationStatus:
    key = job.outcome if job.phase == "terminal" and job.outcome else job.phase
    label, tone = _STATUS_LABELS.get(key, (humanize_job_type(key), "neutral"))
    return PresentationStatus(
        phase=job.phase,
        outcome=job.outcome,
        label=label,
        label_key=f"jobs.status.{key}",
        tone=tone,
    )


def present_trigger(job: Job, definition: JobDefinition) -> PresentationTrigger:
    try:
        kind = TriggerKind(job.trigger_kind)
    except ValueError:
        kind = TriggerKind.SYSTEM
    initiator = None
    if isinstance(job.initiator, dict):
        raw = job.initiator.get("label") or job.initiator.get("kind")
        if isinstance(raw, str) and raw and len(raw) <= 200:
            initiator = raw
    return PresentationTrigger(
        kind=kind,
        label=_TRIGGER_LABELS.get(kind, "System"),
        initiator=initiator,
    )


def present_attention(ctx: PresenterContext) -> PresentationAttention:
    job = ctx.job
    stored = job.attention if isinstance(job.attention, dict) else None
    if stored is not None:
        try:
            return PresentationAttention.model_validate(stored)
        except ValidationError:
            ctx.warn(
                "malformed_evidence", "The stored attention document could not be validated."
            )
    if job.phase == "terminal":
        if job.outcome in {"failed", "dead_letter"}:
            return PresentationAttention(
                level=AttentionLevel.ERROR,
                reason=AttentionReason.FAILED,
                message=ctx.error.summary if ctx.error else None,
                remediation=ctx.error.remediation if ctx.error else None,
            )
        if job.outcome == "unsafe":
            return PresentationAttention(
                level=AttentionLevel.ERROR,
                reason=AttentionReason.UNSAFE,
                message="The operation ended in an unverified state and needs manual review.",
            )
        if job.outcome == "partially_succeeded":
            return PresentationAttention(
                level=AttentionLevel.WARNING,
                reason=AttentionReason.FAILED,
                message="Some targets did not complete.",
            )
        return PresentationAttention()
    if job.desired_state == "pause":
        return PresentationAttention(
            level=AttentionLevel.NORMAL, reason=AttentionReason.HELD
        )
    if ctx.progress is not None and ctx.progress.wait is not None:
        return PresentationAttention(
            level=AttentionLevel.NORMAL, reason=AttentionReason.WAITING
        )
    return PresentationAttention()


def present_actions(
    job: Job,
    definition: JobDefinition,
    *,
    logs_available: bool = False,
    artifacts_available: bool = False,
) -> tuple[JobAction, ...]:
    context = ActionContext(
        phase=job.phase,
        desired_state=job.desired_state,
        outcome=job.outcome,
        active_attempt=job.current_attempt_id is not None,
        retryable=definition.enabled,
        logs_available=logs_available,
        artifacts_available=artifacts_available,
    )
    return tuple(sorted(allowed_actions(definition.action_policy, context)))


def present_compact_progress(ctx: PresenterContext) -> CompactProgress | None:
    progress = ctx.progress
    if progress is None:
        return None
    return CompactProgress(
        headline=progress.headline,
        stage_key=progress.stage.key,
        stage_label=progress.stage.detail or progress.stage.key.replace("_", " "),
        overall=progress.overall,
        current=progress.current,
        current_subject=(
            present_subject(progress.current_subject)
            if progress.current_subject is not None
            else None
        ),
        freshness=progress.freshness,
        wait=progress.wait,
        updated_at=progress.updated_at,
        sequence=progress.sequence,
    )


def present_impact(ctx: PresenterContext) -> PresentationImpact | None:
    input_bytes = ctx.summary_value("input_bytes", int)
    output_bytes = ctx.summary_value("output_bytes", int)
    items = ctx.summary_value("items_processed", int)
    duration = None
    if ctx.job.started_at and ctx.job.terminal_at:
        seconds = (ctx.job.terminal_at - ctx.job.started_at).total_seconds()
        duration = seconds if seconds >= 0 else None
    if input_bytes is None and output_bytes is None and items is None and duration is None:
        return None
    delta = None
    if isinstance(input_bytes, int) and isinstance(output_bytes, int):
        delta = output_bytes - input_bytes
    return PresentationImpact(
        input_bytes=input_bytes if isinstance(input_bytes, int) and input_bytes >= 0 else None,
        output_bytes=(
            output_bytes if isinstance(output_bytes, int) and output_bytes >= 0 else None
        ),
        storage_delta_bytes=delta,
        duration_seconds=duration,
        items_processed=items if isinstance(items, int) and items >= 0 else None,
    )


def present_links(job: Job, definition: JobDefinition) -> DiagnosticLinks:
    base = f"/api/jobs/{job.id}"
    is_parent = definition.parent_policy is not None or bool(definition.child_job_types)
    return DiagnosticLinks(
        detail=f"/projection-room/jobs/{job.id}",
        snapshot=f"{base}/snapshot",
        presentation=f"{base}/presentation",
        attempts=f"{base}/attempts",
        events=f"{base}/events",
        artifacts=f"{base}/artifacts",
        children=f"{base}/children" if is_parent else None,
        raw_request=f"{base}/raw/request",
        raw_plan=f"{base}/raw/plan" if job.plan is not None else None,
        raw_result=f"{base}/raw/result" if job.result is not None else None,
        raw_error=f"{base}/raw/error" if job.error is not None else None,
    )


def present_failures(ctx: PresenterContext) -> tuple[FailureItem, ...]:
    job = ctx.job
    if job.phase != "terminal" or job.outcome not in {
        "failed",
        "dead_letter",
        "unsafe",
        "partially_succeeded",
    }:
        return ()
    if ctx.error is not None:
        stage = ctx.error.diagnostics.get("stage")
        target = ctx.error.diagnostics.get("target")
        media_changed = ctx.error.diagnostics.get("media_changed")
        atomicity = ctx.error.diagnostics.get("atomicity_held")
        return (
            FailureItem(
                stage=stage if isinstance(stage, str) else None,
                target=target if isinstance(target, str) else None,
                code=ctx.error.code,
                message=ctx.error.summary,
                media_changed=media_changed if isinstance(media_changed, bool) else None,
                atomicity_held=atomicity if isinstance(atomicity, bool) else None,
                remediation=ctx.error.remediation,
            ),
        )
    if job.outcome == "partially_succeeded":
        return ()
    label, _tone = _STATUS_LABELS.get(job.outcome or "", ("Failed", "negative"))
    return (
        FailureItem(
            message=f"{label}: no structured error evidence was stored for this job.",
        ),
    )


def present_suggested_actions(
    ctx: PresenterContext, actions: tuple[JobAction, ...]
) -> tuple[SuggestedAction, ...]:
    suggestions: list[SuggestedAction] = []
    if ctx.error is not None and ctx.error.remediation:
        suggestions.append(SuggestedAction(label=ctx.error.remediation))
    if JobAction.RETRY in actions:
        suggestions.append(SuggestedAction(label="Retry as a new job", action=JobAction.RETRY))
    if JobAction.CANCEL in actions and ctx.job.phase in {"running", "stopping"}:
        suggestions.append(SuggestedAction(label="Cancel", action=JobAction.CANCEL))
    return tuple(suggestions[:8])


class JobPresenter:
    """One dedicated presenter per built-in definition (never generic here)."""

    version = 1
    generic = False

    def __init__(self, job_type: str) -> None:
        self.job_type = job_type
        self.key = f"jobs.{job_type}"

    # Family hooks -------------------------------------------------------
    def action(self, ctx: PresenterContext) -> PresentationAction:
        return PresentationAction(headline=humanize_job_type(self.job_type))

    def sections(self, ctx: PresenterContext) -> tuple[PresentationSection, ...]:
        return ()

    # Shared assembly ----------------------------------------------------
    def present(self, ctx: PresenterContext) -> JobPresentation:
        job = ctx.job
        definition = ctx.definition
        actions = present_actions(
            job,
            definition,
            logs_available=ctx.logs_available,
            artifacts_available=ctx.artifacts_available,
        )
        sections = list(self.sections(ctx))
        if ctx.mutation is not None:
            mutation = ctx.mutation
            sections.append(
                FactsSection(
                    title="Mutation evidence",
                    facts=(
                        Fact(
                            label="Validation",
                            value=TextValue(text=mutation.validation.verdict),
                        ),
                        Fact(
                            label="Atomicity",
                            value=TextValue(text=mutation.atomicity.boundary),
                        ),
                        Fact(
                            label="Published",
                            value=TextValue(
                                text="Yes" if mutation.atomicity.published else "No"
                            ),
                        ),
                    ),
                )
            )
        if job.retry_of_job_id:
            sections.append(
                FactsSection(
                    title="Lineage",
                    facts=(
                        Fact(
                            label="Retry of",
                            value=LinkValue(
                                href=f"/projection-room/jobs/{job.retry_of_job_id}",
                                label=job.retry_of_job_id,
                            ),
                        ),
                    ),
                )
            )
        if ctx.live_subject_missing:
            sections.append(
                NoticeSection(
                    tone="info",
                    message=(
                        "The library item this job worked on is no longer present; "
                        "details come from the snapshot captured when the job was created."
                    ),
                )
            )
        if (
            job.phase == "terminal"
            and job.outcome == "no_change"
            and not any(section.kind == "notice" for section in sections)
        ):
            message = None
            if isinstance(ctx.result, BuiltInResultV1):
                message = ctx.result.message
            sections.append(
                NoticeSection(
                    tone="success",
                    message=message or "Everything was already in the requested state.",
                )
            )
        failures = present_failures(ctx)
        if failures:
            sections.append(FailuresSection(items=failures))
        return JobPresentation(
            presenter_key=self.key,
            presenter_version=self.version,
            job_id=job.id,
            job_type=job.type,
            label=humanize_job_type(job.type),
            label_key=definition.label_key,
            feature_area=definition.feature_area,
            presentation_family=definition.presentation_family,
            subject=present_subject(
                ctx.subject, missing_live_subject=ctx.live_subject_missing
            ),
            action=self.action(ctx),
            trigger=present_trigger(job, definition),
            attention=present_attention(ctx),
            allowed_actions=actions,
            status=present_status(job),
            progress=present_compact_progress(ctx),
            impact=present_impact(ctx),
            sections=tuple(sections[:24]),
            warnings=tuple(ctx.warnings[:100]),
            failures=failures,
            suggested_actions=present_suggested_actions(ctx, actions),
            evidence=EvidenceAvailability(
                logs_available=ctx.logs_available,
                artifacts_available=ctx.artifacts_available,
            ),
            links=present_links(job, definition),
        )

    def present_row(self, ctx: PresenterContext) -> JobRow:
        job = ctx.job
        definition = ctx.definition
        actions = present_actions(
            job,
            definition,
            logs_available=ctx.logs_available,
            artifacts_available=ctx.artifacts_available,
        )
        duration = None
        if job.started_at and job.terminal_at:
            seconds = (job.terminal_at - job.started_at).total_seconds()
            duration = seconds if seconds >= 0 else None
        return JobRow(
            job_id=job.id,
            job_type=job.type,
            label=humanize_job_type(job.type),
            label_key=definition.label_key,
            feature_area=definition.feature_area,
            presentation_family=definition.presentation_family,
            subject=present_subject(
                ctx.subject, missing_live_subject=ctx.live_subject_missing
            ),
            action_headline=self.action(ctx).headline,
            status=present_status(job),
            trigger=present_trigger(job, definition),
            attention=present_attention(ctx),
            progress=present_compact_progress(ctx),
            impact=present_impact(ctx),
            allowed_actions=actions,
            is_parent=definition.parent_policy is not None,
            parent_id=job.parent_id,
            root_id=job.root_id,
            retry_of_job_id=job.retry_of_job_id,
            fence_token=job.fence_token,
            execution_class=definition.execution_class.value,
            priority=job.priority,
            eligible_at=job.eligible_at,
            created_at=job.created_at,
            started_at=job.started_at,
            terminal_at=job.terminal_at,
            duration_seconds=duration,
            evidence=EvidenceAvailability(),
            links=RowLinks(
                detail=f"/projection-room/jobs/{job.id}",
                snapshot=f"/api/jobs/{job.id}/snapshot",
                presentation=f"/api/jobs/{job.id}/presentation",
            ),
        )
