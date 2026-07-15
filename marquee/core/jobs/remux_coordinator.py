"""Confined MKV remux execution for JMC5B track mutations (B06/B08/B10–B13).

The ordered protocol mirrors JMC5A's coordinator and adds the source-media rules:

1. resolve durable selectors against the authoritative probe (fail closed on drift);
2. build the command array from the frozen plan, never from client arguments;
3. stage the candidate on the **destination filesystem** (B11 forbids cross-device
   copy-over) through the JMC3 tracked launcher (B10);
4. validate the candidate by re-probing it;
5. create a canonical checksummed backup before the source is replaced (B12);
6. recheck fence, cancellation, and source signature, then publish atomically
   through the JMC3 coordinator only;
7. rescan the published file — that probe is authoritative (B13).

An embedded remux is all-or-nothing (B08): if it fails, every requested target is
reported ``not_applied`` and nothing is published.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from marquee.core.filesystem import ClassifiedPath, FilesystemBoundary
from marquee.core.jobs.audio_subtitle_documents import MediaTrackMutationResultV1
from marquee.core.jobs.delivery import ExecutionContext
from marquee.core.jobs.mkvmerge_plan import MkvmergePlanError
from marquee.core.jobs.mutation_documents import (
    MutationAtomicityV1,
    MutationJobOutcome,
    MutationTargetOutcomeV1,
    MutationTargetStatus,
    MutationTargetV1,
    MutationValidationV1,
)
from marquee.core.jobs.process_launcher import ProcessLaunchError
from marquee.core.jobs.progress_adapters import MkvmergeProgressAdapter
from marquee.core.jobs.publication import FileSignature, PublicationCoordinator, file_signature
from marquee.core.jobs.track_selectors import TrackEntryV1, TrackInventoryV1

MutationStage = str


class RemuxError(RuntimeError):
    """A remux could not be completed safely; nothing was published."""

    def __init__(self, stage: MutationStage, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.reason_code = reason_code


class RemuxCancelledError(RemuxError):
    """Cooperative cancellation observed before publication."""


@dataclass(frozen=True, slots=True)
class RemuxOutcome:
    """Everything the caller needs to build a typed family result."""

    candidate: ClassifiedPath
    candidate_signature: FileSignature
    progress_samples: int


async def publish_media_candidate(
    context: ExecutionContext,
    *,
    boundary: FilesystemBoundary,
    candidate: ClassifiedPath,
    destination: ClassifiedPath,
    expected_destination: FileSignature,
) -> FileSignature:
    """Publish source-changing output only through the fenced coordinator."""
    candidate_signature = file_signature(boundary, candidate)
    maximum_bytes = max(expected_destination.size * 2, candidate_signature.size, 64 * 1024 * 1024)
    return await PublicationCoordinator(boundary, maximum_bytes=maximum_bytes).publish(
        staged=candidate,
        destination=destination,
        expected_destination=expected_destination,
        fence=context.writer,
    )


def target_for(entry: TrackEntryV1, operation: str) -> MutationTargetV1:
    """One requested target described without paths or raw indexes."""
    facts = entry.facts
    label_bits = [facts.kind.title(), facts.language_tag]
    if facts.codec:
        label_bits.append(facts.codec)
    if facts.channels:
        label_bits.append(f"{facts.channels}ch")
    if facts.title:
        label_bits.append(facts.title)
    return MutationTargetV1(
        key=entry.track_key,
        kind="track",
        label=" · ".join(label_bits),
        operation=operation,
        selector_facts=facts.model_dump(mode="json"),
    )


def not_applied(
    targets: Sequence[MutationTargetV1], *, stage: MutationStage, reason_code: str, message: str
) -> tuple[MutationTargetOutcomeV1, ...]:
    """B08: a failed all-or-nothing remux marks every requested target `not_applied`."""
    return tuple(
        MutationTargetOutcomeV1(
            target=target,
            status=MutationTargetStatus.NOT_APPLIED,
            stage=stage,
            reason_code=reason_code,
            message=message,
            bytes_changed=False,
            product_state_changed=False,
        )
        for target in targets
    )


def atomicity(group_id: str, *, published: bool, uncertain: bool = False) -> MutationAtomicityV1:
    return MutationAtomicityV1(
        group_id=group_id,
        boundary="all_or_nothing",
        published=published,
        rollback_available=False,
        uncertain_state=uncertain,
    )


def failed_result(
    *,
    targets: Sequence[MutationTargetV1],
    before: TrackInventoryV1,
    stage: MutationStage,
    reason_code: str,
    message: str,
    group_id: str,
    uncertain: bool = False,
) -> MediaTrackMutationResultV1:
    """A truthful failure: nothing published, every target `not_applied`."""
    return MediaTrackMutationResultV1(
        outcome=MutationJobOutcome.UNSAFE if uncertain else MutationJobOutcome.FAILED,
        reason_code=reason_code,
        message=message,
        requested_targets=tuple(targets),
        target_outcomes=not_applied(
            targets, stage=stage, reason_code=reason_code, message=message
        ),
        validation=MutationValidationV1(verdict="failed" if not uncertain else "not_run"),
        atomicity=atomicity(group_id, published=False, uncertain=uncertain),
        before_inventory=before,
    )


def no_change_result(
    *,
    targets: Sequence[MutationTargetV1],
    before: TrackInventoryV1,
    reason_code: str,
    message: str,
    group_id: str,
) -> MediaTrackMutationResultV1:
    """A03: `no_change` is a reasoned job outcome, never a synonym for success."""
    return MediaTrackMutationResultV1(
        outcome=MutationJobOutcome.NO_CHANGE,
        reason_code=reason_code,
        message=message,
        requested_targets=tuple(targets),
        target_outcomes=tuple(
            MutationTargetOutcomeV1(
                target=target,
                status=MutationTargetStatus.SKIPPED,
                stage="preflight",
                reason_code=reason_code,
                message=message,
                bytes_changed=False,
                product_state_changed=False,
            )
            for target in targets
        ),
        validation=MutationValidationV1(verdict="not_run"),
        atomicity=atomicity(group_id, published=False),
        before_inventory=before,
    )


def _cancelled(context: ExecutionContext) -> bool:
    token = getattr(context, "cancellation", None)
    for probe in ("is_cancelled", "cancelled", "is_set"):
        value = getattr(token, probe, None)
        if callable(value) and bool(value()):
            return True
        if isinstance(value, bool) and value:
            return True
    return False


async def run_mkvmerge_plan(
    context: ExecutionContext,
    *,
    boundary: FilesystemBoundary,
    args: Sequence[str],
    candidate: ClassifiedPath,
) -> RemuxOutcome:
    """Run one remux through the tracked launcher and validate its candidate.

    The candidate must already be addressed on the destination filesystem; this
    never copies across devices and never publishes.
    """
    if _cancelled(context):
        raise RemuxCancelledError("remux", "cancelled", "cancelled before the tool was launched")
    try:
        # args[0] is the tool name; the launcher resolves and allowlists it.
        process = await context.process_launcher.launch(args[0], list(args[1:]))
    except ProcessLaunchError as exc:
        raise RemuxError("remux", "tool_unavailable", "the remux tool could not be launched") from exc

    summary = await process.wait()
    adapter = MkvmergeProgressAdapter()
    samples = adapter.feed(summary.stdout.captured)
    if summary.exit_signal is not None:
        raise RemuxError("remux", "tool_signalled", "the remux tool terminated on a signal")
    # mkvmerge exit 1 is "completed with warnings"; anything else is a failure.
    if summary.exit_code not in (0, 1):
        raise RemuxError("remux", "tool_failed", "the remux tool reported a failure")
    if _cancelled(context):
        raise RemuxCancelledError("remux", "cancelled", "cancelled before validation")

    try:
        candidate_signature = file_signature(boundary, candidate)
    except Exception as exc:  # noqa: BLE001 - classified as a validation failure
        raise RemuxError(
            "validate", "candidate_missing", "the remux produced no readable candidate"
        ) from exc
    if candidate_signature.size <= 0:
        raise RemuxError("validate", "candidate_empty", "the remux produced an empty candidate")
    return RemuxOutcome(
        candidate=candidate,
        candidate_signature=candidate_signature,
        progress_samples=len(samples),
    )


async def run_mkvpropedit_plan(
    context: ExecutionContext,
    *,
    boundary: FilesystemBoundary,
    args: Sequence[str],
    candidate: ClassifiedPath,
) -> RemuxOutcome:
    """Apply metadata to an owned candidate without inventing native progress."""
    if _cancelled(context):
        raise RemuxCancelledError("remux", "cancelled", "cancelled before the tool was launched")
    try:
        process = await context.process_launcher.launch(args[0], list(args[1:]))
    except ProcessLaunchError as exc:
        raise RemuxError("remux", "tool_unavailable", "the metadata tool could not be launched") from exc
    summary = await process.wait()
    if summary.exit_signal is not None:
        raise RemuxError("remux", "tool_signalled", "the metadata tool terminated on a signal")
    if summary.exit_code not in (0, 1):
        raise RemuxError("remux", "tool_failed", "the metadata tool reported a failure")
    if _cancelled(context):
        raise RemuxCancelledError("remux", "cancelled", "cancelled before validation")
    try:
        candidate_signature = file_signature(boundary, candidate)
    except Exception as exc:  # noqa: BLE001 - classified as a validation failure
        raise RemuxError(
            "validate", "candidate_missing", "the metadata edit produced no readable candidate"
        ) from exc
    return RemuxOutcome(
        candidate=candidate,
        candidate_signature=candidate_signature,
        progress_samples=0,
    )


def classify_plan_error(exc: MkvmergePlanError) -> tuple[MutationStage, str]:
    """Plan-building failures are permanent pre-effect failures (§6.1)."""
    return "preflight", "plan_rejected"
