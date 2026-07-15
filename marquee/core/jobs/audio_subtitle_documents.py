"""Family-specific audio/subtitle mutation documents (JMC5B B04).

A02 forbids the generic ``BuiltInIntentV1``/``BuiltInResultV1`` on any enabled
mutating definition, so every JMC5B leaf gets a typed request and result here.

A request states *what the user asked for* and never accepts execution policy:
no container choice, no tool arguments, no paths, no stream indexes.  Targets are
addressed only by the durable selectors from ``track_selectors``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from marquee.core.jobs.documents import StrictDocument
from marquee.core.jobs.mutation_documents import MutationAtomicityV1, MutationResultV1
from marquee.core.jobs.track_selectors import TrackInventoryV1, TrackSelectorV1


class _MediaFileRequest(StrictDocument):
    """Every audio/subtitle mutation names one durable media file."""

    media_file_id: int = Field(ge=1)


class TrackRemoveRequestV1(_MediaFileRequest):
    """Remove one or more selected tracks (`audio_remove`/`track_remove`/`subtitle_remove`)."""

    selectors: tuple[TrackSelectorV1, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def reject_duplicate_targets(self) -> TrackRemoveRequestV1:
        keys = [selector.track_key for selector in self.selectors]
        if len(set(keys)) != len(keys):
            raise ValueError("a track may only be requested once")
        return self


class AudioReorderRequestV1(_MediaFileRequest):
    """Reorder audio tracks into an explicit, complete desired order."""

    ordered_selectors: tuple[TrackSelectorV1, ...] = Field(min_length=2, max_length=64)

    @model_validator(mode="after")
    def reject_partial_or_duplicate_order(self) -> AudioReorderRequestV1:
        keys = [selector.track_key for selector in self.ordered_selectors]
        if len(set(keys)) != len(keys):
            raise ValueError("a track may only appear once in a desired order")
        if any(selector.facts.kind != "audio" for selector in self.ordered_selectors):
            raise ValueError("audio reorder accepts only audio tracks")
        return self


class SubtitleMetadataEditV1(StrictDocument):
    """One requested metadata change; omitted fields are explicitly unchanged."""

    selector: TrackSelectorV1
    language_tag: str | None = Field(default=None, min_length=1, max_length=40)
    title: str | None = Field(default=None, max_length=300)
    is_default: bool | None = None
    is_forced: bool | None = None
    is_hearing_impaired: bool | None = None

    @model_validator(mode="after")
    def require_at_least_one_change(self) -> SubtitleMetadataEditV1:
        changes = (
            self.language_tag,
            self.title,
            self.is_default,
            self.is_forced,
            self.is_hearing_impaired,
        )
        if all(value is None for value in changes):
            raise ValueError("a metadata edit must request at least one change")
        return self


class SubtitleMetadataRequestV1(_MediaFileRequest):
    """Edit subtitle track metadata without touching stream payloads."""

    edits: tuple[SubtitleMetadataEditV1, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def reject_duplicate_targets(self) -> SubtitleMetadataRequestV1:
        keys = [edit.selector.track_key for edit in self.edits]
        if len(set(keys)) != len(keys):
            raise ValueError("a track may only be edited once")
        if any(edit.selector.facts.kind != "subtitle" for edit in self.edits):
            raise ValueError("subtitle metadata accepts only subtitle tracks")
        return self


class SubtitleExtractRequestV1(_MediaFileRequest):
    """Extract one embedded subtitle track to a managed sidecar (B17).

    Extraction never alters the source; it publishes a managed artifact.
    """

    selector: TrackSelectorV1

    @model_validator(mode="after")
    def only_embedded_subtitles(self) -> SubtitleExtractRequestV1:
        facts = self.selector.facts
        if facts.kind != "subtitle":
            raise ValueError("extraction accepts only subtitle tracks")
        if facts.source != "embedded":
            raise ValueError("only an embedded track can be extracted")
        return self


class SubtitleEmbedRequestV1(_MediaFileRequest):
    """Embed one already-validated managed subtitle asset (§6.2).

    Only a managed asset key is accepted; a caller-supplied path is never a
    source of truth.
    """

    managed_asset_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    language_tag: str = Field(min_length=1, max_length=40)
    title: str | None = Field(default=None, max_length=300)
    is_default: bool = False
    is_forced: bool = False
    is_hearing_impaired: bool = False


GenerationPublish = Literal["sidecar", "embed"]


class SubtitleGenerateRequestV1(_MediaFileRequest):
    """Generate subtitles through the provider and publish the result (B14/B15).

    The job never rewrites its own type or request mid-execution: the publish
    target is fixed at request time.
    """

    language_tag: str = Field(min_length=1, max_length=40)
    publish: GenerationPublish = "sidecar"
    source_selector: TrackSelectorV1 | None = None
    provider_id: str | None = Field(
        default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$"
    )
    task: Literal["transcribe", "translate"] = "transcribe"

    @model_validator(mode="after")
    def source_must_be_audio(self) -> SubtitleGenerateRequestV1:
        if self.source_selector is not None and self.source_selector.facts.kind != "audio":
            raise ValueError("generation transcribes an audio track")
        return self


class SubtitlePolicyRequestV1(_MediaFileRequest):
    """Apply one **already-evaluated** policy plan to a single file (B18).

    The parent freezes the evaluated rule, its version, and the exact per-file
    actions before any child exists.  The child executes that frozen plan and
    never re-reads live policy or configuration, so a policy edited mid-batch
    cannot be silently adopted.
    """

    policy_id: int = Field(ge=1)
    policy_revision: int = Field(ge=0)
    #: Frozen evaluated actions: the tracks this policy decided to remove.
    remove_selectors: tuple[TrackSelectorV1, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def reject_duplicate_targets(self) -> SubtitlePolicyRequestV1:
        keys = [selector.track_key for selector in self.remove_selectors]
        if len(set(keys)) != len(keys):
            raise ValueError("a track may only be actioned once by a policy plan")
        return self


RestoreMode = Literal["replace_source"]


class SubtitleRestoreRequestV1(_MediaFileRequest):
    """Restore a media file from a canonical checksummed backup artifact (B12).

    Only a confined artifact key plus its checksum and original source signature
    are accepted; a caller-supplied path is never a source of truth.
    """

    artifact_key: str = Field(min_length=1, max_length=240)
    checksum: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    #: The source signature the backup was taken from; proves lineage.
    source_signature: str = Field(min_length=1, max_length=160)
    #: The signature the destination is expected to still have.  A newly changed
    #: file needs an explicit fresh plan rather than a silent overwrite.
    expected_destination_signature: str = Field(min_length=1, max_length=160)
    mode: RestoreMode = "replace_source"


class SubtitleBatchRequestV1(StrictDocument):
    """Sealed parent scope for policy application across files."""

    policy_id: int = Field(ge=1)
    policy_revision: int = Field(ge=0)
    scope: Literal["all", "selected"] = "all"
    selection_count: int = Field(ge=0, default=0)


MediaMutationStage = Literal[
    "resolve",
    "preflight",
    "remux",
    "sidecar",
    "validate",
    "backup",
    "publish",
    "rescan",
    "generate",
    "wait",
    "download",
]


class ManagedSidecarV1(StrictDocument):
    """A published managed sidecar, addressed by key and checksum — never a path."""

    managed_asset_id: str = Field(min_length=1, max_length=64)
    storage_key: str = Field(min_length=1, max_length=240)
    checksum: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    language_tag: str = Field(min_length=1, max_length=40)


class SubtitleSidecarResultV1(MutationResultV1):
    """Extraction/external generation result: a registered artifact, no source change."""

    before_inventory: TrackInventoryV1
    sidecar: ManagedSidecarV1 | None = None

    @model_validator(mode="after")
    def published_sidecar_is_recorded(self) -> SubtitleSidecarResultV1:
        published = any(outcome.bytes_changed for outcome in self.target_outcomes)
        if published and self.sidecar is None:
            raise ValueError("a published sidecar must be recorded as a managed artifact")
        return self


class SubtitleGenerationResultV1(SubtitleSidecarResultV1):
    """B15: distinguishes "generated but not embedded" from "nothing published"."""

    provider: str = Field(min_length=1, max_length=60)
    #: Human-safe description of the transcribed audio track, when known (§7).
    source_track: str | None = Field(default=None, max_length=200)
    generated: bool = False
    embedded: bool = False
    generation_atomicity: MutationAtomicityV1
    embed_atomicity: MutationAtomicityV1 | None = None
    actual_inventory: TrackInventoryV1 | None = None

    @model_validator(mode="after")
    def embedding_requires_generation_and_rescan(self) -> SubtitleGenerationResultV1:
        if self.embedded and not self.generated:
            raise ValueError("a track cannot be embedded without being generated")
        if self.embedded and self.actual_inventory is None:
            raise ValueError("an embedded generation must record its actual inventory")
        if self.embedded and self.embed_atomicity is None:
            raise ValueError("an embedded generation must record its remux atomic group")
        return self


class MediaTrackMutationResultV1(MutationResultV1):
    """A track mutation result carries the authoritative before/actual inventories.

    B13: the post-operation probe is authoritative.  Expected deltas alone can
    never declare success, so both inventories are recorded and compared.
    """

    before_inventory: TrackInventoryV1
    actual_inventory: TrackInventoryV1 | None = None

    @model_validator(mode="after")
    def require_actual_inventory_when_bytes_changed(self) -> MediaTrackMutationResultV1:
        changed = any(outcome.bytes_changed for outcome in self.target_outcomes)
        if changed and self.actual_inventory is None:
            raise ValueError("a published track mutation must record its actual inventory")
        return self
