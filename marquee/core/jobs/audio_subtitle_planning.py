"""Canonical planning helpers for JMC5B audio/subtitle routes.

The API supplies a family-specific request document.  This module resolves the
server-owned media subject, builds the immutable before inventory and target
documents, and delegates lifecycle ownership to :mod:`mutation_planning`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.audio_subtitle_documents import (
    AudioReorderRequestV1,
    SubtitleEmbedRequestV1,
    SubtitleExtractRequestV1,
    SubtitleGenerateRequestV1,
    SubtitleMetadataRequestV1,
    SubtitlePolicyRequestV1,
    SubtitleRestoreRequestV1,
    TrackRemoveRequestV1,
)
from marquee.core.jobs.mutation_documents import MutationTargetV1
from marquee.core.jobs.mutation_planning import MutationPlan, plan_mutation
from marquee.core.jobs.submission import Initiator, SubjectLocator, SubmissionResult
from marquee.core.jobs.track_inventory_adapter import inventory_from_probe
from marquee.core.jobs.track_selectors import (
    TrackInventoryV1,
    TrackSelectorV1,
    resolve_all,
    selector_for,
)
from marquee.core.media_files import ResolvedMediaFile, resolve_media_file
from marquee.models import SubtitleInventory, SubtitleTrack

REQUEST_MODELS: dict[str, type[BaseModel]] = {
    "audio_remove": TrackRemoveRequestV1,
    "track_remove": TrackRemoveRequestV1,
    "subtitle_remove": TrackRemoveRequestV1,
    "audio_reorder": AudioReorderRequestV1,
    "subtitle_metadata": SubtitleMetadataRequestV1,
    "subtitle_extract": SubtitleExtractRequestV1,
    "subtitle_embed": SubtitleEmbedRequestV1,
    "subtitle_generate": SubtitleGenerateRequestV1,
    "subtitle_policy": SubtitlePolicyRequestV1,
    "subtitle_restore": SubtitleRestoreRequestV1,
}

EMBEDDED_SELECTOR_JOB_TYPES = frozenset(
    {"audio_remove", "track_remove", "subtitle_remove", "audio_reorder", "subtitle_metadata"}
)


class AudioSubtitlePlanError(ValueError):
    """A public mutation request cannot be converted to a safe frozen plan."""


def validate_request(job_type: str, request: Mapping[str, object], media_file_id: int) -> BaseModel:
    model = REQUEST_MODELS.get(job_type)
    if model is None:
        raise AudioSubtitlePlanError("unsupported audio/subtitle mutation type")
    document = model.model_validate(request)
    if getattr(document, "media_file_id", None) != media_file_id:
        raise AudioSubtitlePlanError("request media_file_id does not match the route subject")
    return document


def _selector_target(selector: TrackSelectorV1, operation: str) -> MutationTargetV1:
    facts = selector.facts
    label = facts.title or f"{facts.language_tag or 'und'} {facts.kind} track"
    return MutationTargetV1(
        key=selector.track_key,
        kind=f"{facts.kind}_track",
        label=label,
        operation=operation,
        selector_facts=selector.model_dump(mode="json"),
    )


def _synthetic_target(*, key: str, kind: str, label: str, operation: str, facts: dict[str, Any]) -> MutationTargetV1:
    return MutationTargetV1(
        key=key,
        kind=kind,
        label=label,
        operation=operation,
        selector_facts=facts,
    )


def requested_targets(document: BaseModel, job_type: str) -> tuple[MutationTargetV1, ...]:
    if isinstance(document, TrackRemoveRequestV1):
        return tuple(_selector_target(selector, job_type) for selector in document.selectors)
    if isinstance(document, AudioReorderRequestV1):
        return tuple(_selector_target(selector, job_type) for selector in document.ordered_selectors)
    if isinstance(document, SubtitleMetadataRequestV1):
        return tuple(_selector_target(edit.selector, job_type) for edit in document.edits)
    if isinstance(document, SubtitleExtractRequestV1):
        return (_selector_target(document.selector, job_type),)
    if isinstance(document, SubtitleEmbedRequestV1):
        return (
            _synthetic_target(
                key=f"asset:{document.managed_asset_id}",
                kind="managed_subtitle",
                label=document.title or f"{document.language_tag} managed subtitle",
                operation=job_type,
                facts={"managed_asset_id": document.managed_asset_id, "language_tag": document.language_tag},
            ),
        )
    if isinstance(document, SubtitleGenerateRequestV1):
        source = document.source_selector
        return (
            _synthetic_target(
                key=f"generation:{document.media_file_id}:{document.language_tag}",
                kind="generated_subtitle",
                label=f"{document.language_tag} generated subtitle",
                operation=job_type,
                facts={
                    "language_tag": document.language_tag,
                    "publish": document.publish,
                    "source_selector": source.model_dump(mode="json") if source else None,
                },
            ),
        )
    if isinstance(document, SubtitlePolicyRequestV1):
        selectors = tuple(_selector_target(selector, job_type) for selector in document.remove_selectors)
        return selectors or (
            _synthetic_target(
                key=f"policy:{document.policy_id}:{document.media_file_id}",
                kind="subtitle_policy",
                label=f"subtitle policy {document.policy_id}",
                operation=job_type,
                facts={"policy_id": document.policy_id, "policy_revision": document.policy_revision},
            ),
        )
    if isinstance(document, SubtitleRestoreRequestV1):
        return (
            _synthetic_target(
                key=f"backup:{document.checksum}",
                kind="media_backup",
                label="verified media backup",
                operation=job_type,
                facts={"artifact_key": document.artifact_key, "checksum": document.checksum},
            ),
        )
    raise AudioSubtitlePlanError("request document has no mutation target adapter")


def request_selectors(document: BaseModel) -> tuple[TrackSelectorV1, ...]:
    if isinstance(document, TrackRemoveRequestV1):
        return document.selectors
    if isinstance(document, AudioReorderRequestV1):
        return document.ordered_selectors
    if isinstance(document, SubtitleMetadataRequestV1):
        return tuple(edit.selector for edit in document.edits)
    if isinstance(document, SubtitleExtractRequestV1):
        return (document.selector,)
    if isinstance(document, SubtitleGenerateRequestV1) and document.source_selector is not None:
        return (document.source_selector,)
    if isinstance(document, SubtitlePolicyRequestV1):
        return document.remove_selectors
    return ()


def before_targets(inventory: TrackInventoryV1) -> tuple[MutationTargetV1, ...]:
    return tuple(
        _selector_target(selector_for(entry, inventory), "observe") for entry in inventory.entries
    )


async def load_before_inventory(
    session: AsyncSession, media_file_id: int
) -> tuple[ResolvedMediaFile, TrackInventoryV1]:
    resolved = await resolve_media_file(session, media_file_id)
    inventory = await session.scalar(
        select(SubtitleInventory).where(
            SubtitleInventory.media_file_id == media_file_id
        )
    )
    if (
        inventory is None
        or inventory.error is not None
        or inventory.file_signature != resolved.signature
    ):
        raise AudioSubtitlePlanError(
            "the subtitle inventory is missing or stale; scan the media file first"
        )
    audio = inventory.audio_streams_json or []
    if isinstance(audio, str):
        audio = json.loads(audio)
    if not isinstance(audio, list):
        raise AudioSubtitlePlanError("the stored audio inventory is invalid")

    audio = [stream for stream in audio if isinstance(stream, dict)]
    tracks = (
        await session.scalars(
            select(SubtitleTrack)
            .where(SubtitleTrack.inventory_id == inventory.id)
            .order_by(SubtitleTrack.stream_index, SubtitleTrack.id)
        )
    ).all()
    subtitles: list[dict[str, object | None]] = []
    for track in tracks:
        managed_key = track.external_path
        if managed_key and (managed_key.startswith("/") or ".." in managed_key):
            managed_key = None
        subtitles.append(
            {
                "source": track.source,
                "language_tag": track.language_tag,
                "codec": track.codec,
                "title": track.title,
                "is_default": track.is_default,
                "is_forced": track.is_forced,
                "is_sdh": track.is_sdh,
                "managed_key": managed_key,
                "stream_index": track.stream_index,
                "tool_track_id": track.tool_track_id,
            }
        )
    return resolved, inventory_from_probe(
        signature=inventory.file_signature,
        container=inventory.container,
        audio_streams=audio,
        subtitles=subtitles,
    )


async def plan_audio_subtitle_mutation(
    session: AsyncSession,
    *,
    job_type: str,
    request: Mapping[str, object],
    media_file_id: int,
    idempotency_key: str,
    initiator: Initiator | None,
) -> tuple[SubmissionResult, ResolvedMediaFile, TrackInventoryV1]:
    document = validate_request(job_type, request, media_file_id)
    resolved, inventory = await load_before_inventory(session, media_file_id)
    selectors = request_selectors(document)
    if selectors:
        resolved_entries = resolve_all(selectors, inventory)
        if job_type in EMBEDDED_SELECTOR_JOB_TYPES and any(
            entry.facts.source != "embedded" for entry in resolved_entries
        ):
            raise AudioSubtitlePlanError(
                f"{job_type} only accepts selectors for embedded tracks"
            )
    targets = requested_targets(document, job_type)
    result = await plan_mutation(
        session,
        job_type=job_type,
        request=document.model_dump(mode="json"),
        subject=SubjectLocator(kind="media_file", reference=str(media_file_id)),
        initiator=initiator,
        idempotency_key=idempotency_key,
        plan=MutationPlan(
            operation_kind=job_type,
            media_file_id=media_file_id,
            media_snapshot={
                "media_file_id": media_file_id,
                "size_bytes": resolved.size_bytes,
                "container": resolved.container,
                "inventory": inventory.model_dump(mode="json"),
            },
            before_targets=before_targets(inventory),
            requested_targets=targets,
            expected_targets=targets,
            input_signature=resolved.signature,
            confirmation_requirements={"source_unchanged": True, "targets_unchanged": True},
        ),
    )
    return result, resolved, inventory


def selector_by_stream_index(
    inventory: TrackInventoryV1, *, kind: str, stream_index: int
) -> TrackSelectorV1:
    matches = [
        entry
        for entry in inventory.entries
        if entry.facts.kind == kind and entry.stream_index == stream_index
    ]
    if len(matches) != 1:
        raise AudioSubtitlePlanError("the requested stream is stale or ambiguous")
    return selector_for(matches[0], inventory)


def selectors_by_stream_indices(
    inventory: TrackInventoryV1, *, kind: str, stream_indices: Sequence[int]
) -> tuple[TrackSelectorV1, ...]:
    return tuple(
        selector_by_stream_index(inventory, kind=kind, stream_index=index)
        for index in stream_indices
    )
