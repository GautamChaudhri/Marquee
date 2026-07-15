from __future__ import annotations

import asyncio
import copy
import hashlib
from dataclasses import dataclass, is_dataclass, replace
from datetime import UTC, datetime

from marquee.core.jobs.audio_subtitle_documents import (
    ManagedSidecarV1,
    SubtitleEmbedRequestV1,
    SubtitleGenerateRequestV1,
    SubtitleGenerationResultV1,
)
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.handlers_sidecars import (
    SidecarError,
    _publish_managed,
    _register_managed_asset,
    execute_subtitle_embed,
    managed_key,
    validate_subtitle_bytes,
)
from marquee.core.jobs.media_mutation_support import TrackMutationError
from marquee.core.jobs.media_mutation_support import confined_boundary as _boundary
from marquee.core.jobs.media_mutation_support import load_media_file as _load
from marquee.core.jobs.media_mutation_support import physical as _physical
from marquee.core.jobs.media_mutation_support import probe_inventory as _inventory
from marquee.core.jobs.mutation_documents import (
    MutationJobOutcome,
    MutationTargetOutcomeV1,
    MutationTargetStatus,
    MutationTargetV1,
    MutationValidationV1,
)
from marquee.core.jobs.remux_coordinator import atomicity, failed_result
from marquee.core.media_files import MediaFileNotFoundError, MediaFileUnavailableError
from marquee.core.subtitles.generators.base import GenerationRequest

#: Bounded polling: the provider is never trusted to be fast or to call back.
POLL_INTERVAL_SECONDS = 2.0
POLL_DEADLINE_SECONDS = 900.0

TERMINAL_PROVIDER_STATES = {"produced", "failed", "timeout"}


class GenerationError(RuntimeError):
    """Generation could not complete; nothing was published."""

    def __init__(self, stage: str, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    state: str
    output_path: str | None
    polls: int
    waited_seconds: float


def _cancelled(context: ExecutionContext) -> bool:
    token = getattr(context, "cancellation", None)
    for probe in ("is_cancelled", "cancelled", "is_set"):
        value = getattr(token, probe, None)
        if callable(value) and bool(value()):
            return True
        if isinstance(value, bool) and value:
            return True
    return False


def _context_with_request(context: ExecutionContext, request: dict[str, object]):
    if is_dataclass(context):
        return replace(context, request=request)
    copied = copy.copy(context)
    copied.request = request
    return copied


async def wait_for_provider(
    context: ExecutionContext,
    generator,
    request: GenerationRequest,
    *,
    interval: float = POLL_INTERVAL_SECONDS,
    deadline: float = POLL_DEADLINE_SECONDS,
    sleep=asyncio.sleep,
    now=None,
) -> GenerationOutcome:
    """Poll reconciliation until terminal, cancelled, or the deadline expires.

    Cooperative cancellation is checked before every poll and every sleep, so a
    cancelled job stops waiting without needing the provider to respond.
    """
    clock = now or (lambda: datetime.now(UTC).timestamp())
    started = clock()
    polls = 0
    while True:
        if _cancelled(context):
            raise GenerationError("wait", "cancelled", "cancelled while waiting for the provider")
        elapsed = clock() - started
        if elapsed >= deadline:
            raise GenerationError(
                "wait", "provider_timeout", "the provider did not finish within the deadline"
            )
        state = await generator.reconcile(request)
        polls += 1
        if state.state in TERMINAL_PROVIDER_STATES:
            if state.state != "produced":
                raise GenerationError(
                    "generate",
                    f"provider_{state.state}",
                    state.message or f"the provider reported {state.state}",
                )
            return GenerationOutcome(
                state=state.state,
                output_path=state.output_path,
                polls=polls,
                waited_seconds=clock() - started,
            )
        if _cancelled(context):
            raise GenerationError("wait", "cancelled", "cancelled while waiting for the provider")
        await sleep(interval)


def _target(request: SubtitleGenerateRequestV1) -> MutationTargetV1:
    return MutationTargetV1(
        key=f"subtitle:generated:{request.language_tag}",
        kind="track",
        label=f"Generated {request.language_tag} subtitles",
        operation="subtitle_generate",
        selector_facts={
            "kind": "subtitle",
            "source": "external" if request.publish == "sidecar" else "embedded",
            "language_tag": request.language_tag,
        },
    )


def _failure(
    request: SubtitleGenerateRequestV1,
    before,
    *,
    stage: str,
    reason_code: str,
    message: str,
    provider: str,
    generated: bool = False,
) -> dict[str, object]:
    """B15: a failure states plainly whether anything was generated."""
    target = _target(request)
    base = failed_result(
        targets=[target],
        before=before,
        stage=stage,
        reason_code=reason_code,
        message=message,
        group_id=f"subtitle_generate:{request.media_file_id}",
    ).model_dump(exclude={"actual_inventory"})
    return SubtitleGenerationResultV1(
        **base,
        provider=provider,
        generated=generated,
        embedded=False,
        generation_atomicity=atomicity(
            f"subtitle_generate:{request.media_file_id}:generation", published=False
        ),
    ).model_dump(mode="json")


async def execute_subtitle_generate(context: ExecutionContext) -> dict[str, object]:
    """Generate subtitles and publish them through the same confined protocol."""
    from marquee.core.subtitles.generation import get_generator

    request = SubtitleGenerateRequestV1.model_validate(context.request)
    group_id = f"subtitle_generate:{request.media_file_id}"
    try:
        resolved_file = await _load(context, request.media_file_id)
    except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
        raise TrackMutationError("the media file is unavailable") from exc

    source_path = resolved_file.path
    before = _inventory(source_path, resolved_file.signature)
    boundary, _source = _boundary(source_path)

    generator = get_generator(request.provider_id)
    provider = getattr(generator, "id", "subgen") if generator else "unavailable"
    if generator is None:
        return _failure(
            request, before, stage="preflight", reason_code="provider_unavailable",
            message="no subtitle generator is configured", provider=provider,
        )

    provider_request = GenerationRequest(
        media_file_id=request.media_file_id,
        local_media_path=str(source_path),
        language_hint=request.language_tag,
        output="external",
        task=request.task,
        stream_index=request.source_selector.stream_index_hint
        if request.source_selector
        else None,
    )

    try:
        submission = await generator.submit(provider_request)
        if not submission.accepted:
            raise GenerationError(
                "generate", "provider_rejected", submission.detail or "the provider rejected the request"
            )
        outcome = await wait_for_provider(context, generator, provider_request)
        if not outcome.output_path:
            raise GenerationError(
                "download", "provider_output_missing", "the provider reported no output"
            )
        payload = _read_provider_output(outcome.output_path)
        validate_subtitle_bytes(payload)
    except GenerationError as exc:
        return _failure(
            request, before, stage=exc.stage, reason_code=exc.reason_code,
            message=str(exc)[:200], provider=provider,
        )
    except (SidecarError, OSError) as exc:
        # The provider produced something unusable: generated, nothing published.
        return _failure(
            request, before, stage="validate", reason_code="output_invalid",
            message=str(exc)[:200], provider=provider, generated=True,
        )

    # A late cancellation cannot publish: the owned request identity is stale.
    if _cancelled(context):
        return _failure(
            request, before, stage="publish", reason_code="cancelled",
            message="cancelled before the generated subtitle was published",
            provider=provider, generated=True,
        )

    checksum = hashlib.sha256(payload).hexdigest()
    staged = boundary.from_key("data", f"jmc5/managed-subtitles/.staging-{checksum}.srt")
    boundary.create_directory(boundary.from_key("data", "jmc5/managed-subtitles"), parents=True)
    _physical(staged).write_bytes(payload)
    try:
        _publish_managed(boundary, staged, checksum)
    except SidecarError as exc:
        boundary.delete_file(staged, missing_ok=True)
        return _failure(
            request, before, stage="publish", reason_code="sidecar_conflict",
            message=str(exc)[:200], provider=provider, generated=True,
        )

    asset_id = await _register_managed_asset(
        context,
        checksum=checksum,
        language_tag=request.language_tag,
        title=None,
        is_forced=False,
        is_sdh=False,
    )
    sidecar = ManagedSidecarV1(
        managed_asset_id=asset_id,
        storage_key=managed_key(checksum),
        checksum=checksum,
        size_bytes=len(payload),
        language_tag=request.language_tag,
    )
    target = _target(request)
    if request.publish == "embed":
        embed_request = SubtitleEmbedRequestV1(
            media_file_id=request.media_file_id,
            managed_asset_id=asset_id,
            language_tag=request.language_tag,
        )
        embed_result = await execute_subtitle_embed(
            _context_with_request(context, embed_request.model_dump(mode="json"))
        )
        embedded = embed_result["outcome"] == MutationJobOutcome.SUCCEEDED.value
        if embedded:
            return SubtitleGenerationResultV1(
                outcome=MutationJobOutcome.SUCCEEDED,
                reason_code="generated_and_embedded",
                message="subtitles were generated, published, embedded, and verified by rescan",
                requested_targets=(target,),
                target_outcomes=(
                    MutationTargetOutcomeV1(
                        target=target,
                        status=MutationTargetStatus.SUCCEEDED,
                        stage="rescan",
                        reason_code="generated_and_embedded",
                        message="managed generation was embedded and verified by rescan",
                        bytes_changed=True,
                        product_state_changed=True,
                    ),
                ),
                validation=MutationValidationV1.model_validate(embed_result["validation"]),
                atomicity=embed_result["atomicity"],
                generation_atomicity=atomicity(
                    f"subtitle_generate:{request.media_file_id}:generation", published=True
                ),
                embed_atomicity=embed_result["atomicity"],
                backup=embed_result.get("backup"),
                publish=embed_result.get("publish"),
                before_inventory=before,
                actual_inventory=embed_result.get("actual_inventory"),
                sidecar=sidecar,
                provider=provider,
                source_track=_source_label(request),
                generated=True,
                embedded=True,
            ).model_dump(mode="json")

        return SubtitleGenerationResultV1(
            outcome=MutationJobOutcome.SUCCEEDED,
            reason_code="generated_not_embedded",
            message=(
                "subtitles were generated as a managed sidecar, but embedding was not applied: "
                f"{embed_result['message']}"
            ),
            requested_targets=(target,),
            target_outcomes=(
                MutationTargetOutcomeV1(
                    target=target,
                    status=MutationTargetStatus.SUCCEEDED,
                    stage="sidecar",
                    reason_code="generated_not_embedded",
                    message="the managed sidecar remains available; embedding was not applied",
                    bytes_changed=True,
                    product_state_changed=True,
                ),
            ),
            validation=MutationValidationV1(verdict="passed"),
            atomicity=atomicity(group_id, published=True),
            generation_atomicity=atomicity(
                f"subtitle_generate:{request.media_file_id}:generation", published=True
            ),
            embed_atomicity=embed_result["atomicity"],
            before_inventory=before,
            sidecar=sidecar,
            provider=provider,
            source_track=_source_label(request),
            generated=True,
            embedded=False,
        ).model_dump(mode="json")

    return SubtitleGenerationResultV1(
        outcome=MutationJobOutcome.SUCCEEDED,
        reason_code="generated",
        message="subtitles were generated and published as a managed sidecar",
        requested_targets=(target,),
        target_outcomes=(
            MutationTargetOutcomeV1(
                target=target,
                status=MutationTargetStatus.SUCCEEDED,
                stage="sidecar",
                reason_code="generated",
                message="validated and published as a managed artifact",
                bytes_changed=True,
                product_state_changed=True,
            ),
        ),
        validation=MutationValidationV1(verdict="passed"),
        atomicity=atomicity(group_id, published=True),
        generation_atomicity=atomicity(
            f"subtitle_generate:{request.media_file_id}:generation", published=True
        ),
        before_inventory=before,
        sidecar=sidecar,
        provider=provider,
        source_track=_source_label(request),
        generated=True,
        embedded=False,
    ).model_dump(mode="json")


def _source_label(request: SubtitleGenerateRequestV1) -> str | None:
    """Safe description of the transcribed audio track; never a path or raw index."""
    selector = request.source_selector
    if selector is None:
        return None
    facts = selector.facts
    bits = [facts.language_tag]
    if facts.codec:
        bits.append(facts.codec)
    if facts.channels:
        bits.append(f"{facts.channels}ch")
    return " ".join(bits)


def _read_provider_output(path: str) -> bytes:
    from pathlib import Path

    return Path(path).read_bytes()


register_execution_handler("subtitle_generate", execute_subtitle_generate)
