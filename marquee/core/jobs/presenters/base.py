"""Presentation engine: validated snapshot-first context and shared assembly.

Presenters consume validated canonical documents and immutable subject
snapshots.  Optional live data may enrich a presentation but is never required
to render history.  Malformed optional evidence produces a warning and omits
only the affected content; invalid required canonical documents raise
`PresentationIntegrityError` instead of fabricating data.
"""

from __future__ import annotations

import re
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
from marquee.core.jobs.labels import (
    humanize_job_type,
    poster_group_batch_context,
    poster_group_disclosure_label,
    poster_group_display_name,
)
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
from marquee.core.jobs.progress import JobProgress, ProgressMeasurement
from marquee.core.jobs.retry_capability import resolve_retry_capability
from marquee.core.jobs.subjects import (
    SUBJECT_SNAPSHOT_ADAPTER,
    PosterSubjectGroupSnapshot,
    SubjectSnapshot,
)
from marquee.core.jobs.work_item_documents import (
    ContainedWorkStatusCounts,
    ContainedWorkSummary,
    poster_group_completion_message,
)
from marquee.core.jobs.work_items import work_item_summary

if TYPE_CHECKING:
    from marquee.models import Job, JobBatch, MediaOperationDetail


@dataclass(frozen=True)
class BatchProgressProjection:
    """Coordinator fields required by Activity presentation.

    Stored ``JobBatch`` rows and aggregate-only historical child jobs both adapt
    to this shape, keeping presenters independent from projection storage.
    """

    created_total: int
    terminal_total: int
    succeeded_total: int
    partially_succeeded_total: int
    no_change_total: int
    failed_total: int
    cancelled_total: int
    superseded_total: int
    dead_letter_total: int
    unsafe_total: int
    projection_sequence: int
    updated_at: Any


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
    batch: JobBatch | BatchProgressProjection | None = None
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
    batch: JobBatch | BatchProgressProjection | None = None,
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
        raise PresentationIntegrityError(f"job {job.id} has an invalid subject snapshot") from exc

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
        batch=batch,
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
                lines.append(f"S{subject.season_number:02d}E{subject.episode_number:02d}")
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


def present_context_subject(ctx: PresenterContext) -> PresentationSubject:
    presented = present_subject(ctx.subject, missing_live_subject=ctx.live_subject_missing)
    if ctx.job.type == "poster_pipeline_group" and isinstance(
        ctx.subject, PosterSubjectGroupSnapshot
    ):
        subject = ctx.subject
        return presented.model_copy(
            update={
                "display_name": poster_group_display_name(
                    library=subject.library,
                    member_count=len(subject.members),
                ),
                "context": poster_group_batch_context(
                    parent_job_id=ctx.job.parent_id,
                    chunk_index=subject.chunk_index,
                    chunk_total=subject.chunk_total,
                    batch_mode=subject.batch_mode,
                ),
                "monogram": "FP" if subject.library == "movies" else "TVP",
            }
        )
    policy = ctx.definition.activity_policy
    updates: dict[str, Any] = {}
    if policy.monogram is not None:
        updates["monogram"] = policy.monogram
    if ctx.batch is not None:
        title = {
            "poster_pipeline_batch": "Get Film Posters",
            "poster_pipeline_tv_batch": "Get Television Posters",
            "poster_heal": "Heal Missing Posters",
            "poster_deploy_reset": "Reset Deployed Posters",
            "poster_backup_all": "Back Up Posters",
        }.get(ctx.job.type)
        if title is not None:
            noun = (
                policy.item_label_singular.title()
                if ctx.batch.created_total == 1
                else policy.item_label_plural.title()
            )
            updates["display_name"] = f"{title} · {ctx.batch.created_total} {noun}"
    return presented.model_copy(update=updates) if updates else presented


def present_status(job: Job) -> PresentationStatus:
    key = job.outcome if job.phase == "terminal" and job.outcome else job.phase
    label, tone = _STATUS_LABELS.get(key, (humanize_job_type(key), "neutral"))
    if job.type == "poster_pipeline_group" and key == "partially_succeeded":
        # `review_required` has no canonical outcome, so it borrows this key. The label
        # is keyed on what was actually picked, not on the alias: a group that chose
        # seven posters and left one undecided partially succeeded, and one that chose
        # nothing at all did not succeed at any level.
        result = job.result if isinstance(job.result, dict) else {}
        failed = int(result.get("failed_count") or 0)
        total = int(result.get("member_count") or 0)
        succeeded = int(result.get("succeeded_count") or 0)
        if failed and failed >= total:
            label, tone = "Failed", "negative"
        elif succeeded == 0:
            label, tone = "Needs attention", "warning"
        else:
            label, tone = "Partially succeeded", "warning"
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
    if job.type == "poster_pipeline_group" and isinstance(job.result, dict):
        total = int(job.result.get("member_count") or 0)
        failed = int(job.result.get("failed_count") or 0)
        review = int(job.result.get("review_required_count") or 0)
        if failed or review:
            return PresentationAttention(
                level=(
                    AttentionLevel.ERROR
                    if total > 0 and failed >= total
                    else AttentionLevel.WARNING
                ),
                reason=AttentionReason.FAILED if failed else AttentionReason.REVIEW,
                message=poster_group_completion_message(
                    total=total,
                    failed=failed,
                    review=review,
                ),
            )
    if (
        ctx.batch is not None
        and ctx.definition.activity_policy.contained_work.value == "child_jobs"
    ):
        batch = ctx.batch
        total = batch.created_total
        failed = batch.failed_total + batch.dead_letter_total + batch.unsafe_total
        review = batch.partially_succeeded_total
        if failed or review:
            if failed >= total and total > 0:
                noun = "poster" if total == 1 else "posters"
                message = f"Failed: all {total} {noun} errored."
                level = AttentionLevel.ERROR
            elif failed:
                verb = "needs" if failed == 1 else "need"
                message = f"Partially failed: {failed} of {total} posters {verb} attention"
                if review:
                    review_verb = "is" if review == 1 else "are"
                    message += f"; {review} {review_verb} ready for review"
                message += "."
                level = AttentionLevel.WARNING
            else:
                noun = "poster" if review == 1 else "posters"
                verb = "is" if review == 1 else "are"
                message = f"Ready for review: {review} {noun} {verb} waiting for a choice."
                level = AttentionLevel.WARNING
            return PresentationAttention(
                level=level,
                reason=AttentionReason.FAILED if failed else AttentionReason.REVIEW,
                message=message,
            )
    stored = job.attention if isinstance(job.attention, dict) else None
    if stored is not None:
        try:
            return PresentationAttention.model_validate(stored)
        except ValidationError:
            ctx.warn("malformed_evidence", "The stored attention document could not be validated.")
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
        return PresentationAttention(level=AttentionLevel.NORMAL, reason=AttentionReason.HELD)
    if ctx.progress is not None and ctx.progress.wait is not None:
        return PresentationAttention(level=AttentionLevel.NORMAL, reason=AttentionReason.WAITING)
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
        retryable=resolve_retry_capability(job, definition).available,
        logs_available=logs_available,
        artifacts_available=artifacts_available,
    )
    return tuple(sorted(allowed_actions(definition.action_policy, context)))


_LABEL_KEY = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)+$")


def _display_label(text: str | None) -> str | None:
    """Render a stage label key as text.

    ``ProgressPolicy`` stages carry translation keys and the progress writer
    stores one as the headline, so the raw key reaches the UI verbatim
    ("jobs.poster_pipeline.progress.finalizing"). Until a catalog exists, show
    the leaf. Anything that is not a dotted key is already display text.
    """
    if text is None or not _LABEL_KEY.match(text):
        return text
    return text.rsplit(".", 1)[-1].replace("_", " ").capitalize()


def present_compact_progress(ctx: PresenterContext) -> CompactProgress | None:
    progress = ctx.progress
    if progress is None:
        batch = ctx.batch
        if batch is None or batch.created_total <= 0:
            return None
        policy = ctx.definition.activity_policy
        headline = {
            "poster_heal": "Healing missing posters",
            "poster_deploy_reset": "Resetting deployed posters",
            "poster_backup_all": "Backing up posters",
        }.get(ctx.job.type, "Processing contained work")
        if ctx.job.phase == "stopping":
            headline = "Cancelling"
        elif ctx.job.phase == "terminal":
            headline = "Complete"
        return CompactProgress(
            headline=headline,
            stage_key="contained_work",
            stage_label=f"{batch.terminal_total} of {batch.created_total} {policy.item_label_plural}",
            overall=ProgressMeasurement.determinate(
                scope_id="contained-work-overall",
                completed=batch.terminal_total,
                total=batch.created_total,
                unit=policy.item_label_plural,
            ),
            current=ProgressMeasurement.indeterminate(scope_id="contained-work-current"),
            updated_at=batch.updated_at,
            sequence=max(1, batch.projection_sequence),
        )
    return CompactProgress(
        headline=_display_label(progress.headline),
        stage_key=progress.stage.key,
        stage_label=progress.stage.detail or progress.stage.key.replace("_", " "),
        overall=progress.overall,
        current=progress.current,
        current_subject=(
            present_subject(progress.current_subject)
            if progress.current_subject is not None
            else None
        ),
        metrics=progress.metrics,
        freshness=progress.freshness,
        wait=progress.wait,
        updated_at=progress.updated_at,
        sequence=progress.sequence,
    )


def present_contained_work(ctx: PresenterContext) -> ContainedWorkSummary | None:
    policy = ctx.definition.activity_policy
    if policy.contained_work.value == "none" or policy.disclosure_label is None:
        return None
    if policy.contained_work.value == "work_items":
        summary = work_item_summary(ctx.job)
        if summary is None:
            return None
        counts = summary.counts
        completed = (
            counts.succeeded
            + counts.no_change
            + counts.review_required
            + counts.failed
            + counts.cancelled
        )
        return ContainedWorkSummary(
            source="work_items",
            # "run" or "group" is a property of this job's batch shape, not of
            # its type, so the definition's label is only the fallback.
            label=(
                poster_group_disclosure_label(ctx.subject.batch_mode)
                if isinstance(ctx.subject, PosterSubjectGroupSnapshot)
                else policy.disclosure_label
            ),
            item_label_singular=policy.item_label_singular,
            item_label_plural=policy.item_label_plural,
            total=summary.total,
            completed=completed,
            counts=ContainedWorkStatusCounts(**counts.model_dump()),
            sequence=summary.sequence,
            updated_at=summary.updated_at,
            href=f"/api/jobs/{ctx.job.id}/contained-work",
        )
    batch = ctx.batch
    if batch is None:
        return None
    outstanding = max(0, batch.created_total - batch.terminal_total)
    pending = outstanding if ctx.job.phase in {"planned", "queued"} else 0
    running = outstanding - pending
    return ContainedWorkSummary(
        source="child_jobs",
        label=policy.disclosure_label,
        item_label_singular=policy.item_label_singular,
        item_label_plural=policy.item_label_plural,
        total=batch.created_total,
        completed=batch.terminal_total,
        counts=ContainedWorkStatusCounts(
            pending=pending,
            running=running,
            succeeded=batch.succeeded_total,
            no_change=batch.no_change_total,
            review_required=batch.partially_succeeded_total,
            failed=batch.failed_total + batch.dead_letter_total + batch.unsafe_total,
            cancelled=batch.cancelled_total + batch.superseded_total,
        ),
        sequence=batch.projection_sequence,
        updated_at=batch.updated_at,
        href=f"/api/jobs/{ctx.job.id}/contained-work",
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
                            value=TextValue(text="Yes" if mutation.atomicity.published else "No"),
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
            feature_label=definition.activity_policy.feature_label,
            presentation_family=definition.presentation_family,
            subject=present_context_subject(ctx),
            action=self.action(ctx),
            trigger=present_trigger(job, definition),
            attention=present_attention(ctx),
            allowed_actions=actions,
            status=present_status(job),
            progress=present_compact_progress(ctx),
            work_items=work_item_summary(job),
            contained_work=present_contained_work(ctx),
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
            subject=present_context_subject(ctx),
            action_headline=self.action(ctx).headline,
            status=present_status(job),
            trigger=present_trigger(job, definition),
            attention=present_attention(ctx),
            progress=present_compact_progress(ctx),
            work_items=work_item_summary(job),
            contained_work=present_contained_work(ctx),
            feature_label=definition.activity_policy.feature_label,
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
