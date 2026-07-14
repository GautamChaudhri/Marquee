"""Strict versioned request/result/error document adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class DocumentKind(StrEnum):
    REQUEST = "request"
    RESULT = "result"
    ERROR = "error"


class DocumentVersionError(ValueError):
    """Base class for permanent document-version failures."""


class UnsupportedDocumentVersionError(DocumentVersionError):
    def __init__(self, *, kind: DocumentKind, version: int, supported: tuple[int, ...]) -> None:
        super().__init__(
            f"unsupported {kind.value} document version {version}; supported versions: {supported}"
        )
        self.kind = kind
        self.version = version
        self.supported = supported


class InvalidUpcastError(DocumentVersionError):
    """Raised when an explicit upcaster violates its target model contract."""


class StrictDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmptyDocumentV1(StrictDocument):
    pass


class SystemNoopRequestV1(StrictDocument):
    """The intentionally tiny payload supported by the sole enabled definition."""

    echo: JsonValue | None = None


class LibrarySyncRequestV1(StrictDocument):
    """Radarr/Sonarr/TMDB library synchronization intent; credentials come from env."""

    source: Literal["manual", "schedule"] = "manual"


class SubtitleScanRequestV1(StrictDocument):
    """Read-only audio/subtitle inventory intent for one media file (subject-scoped)."""

    force: bool = False


class SubtitlePolicyAuditRequestV1(StrictDocument):
    """Read-only subtitle-policy dry-run over existing inventory.

    The policy snapshot and revision freeze policy content at enqueue so later edits
    cannot alter this audit. No mutation plan is created and no change is applied.
    """

    policy_id: int = Field(ge=1)
    policy_revision: int = Field(ge=1)
    policy_snapshot: dict[str, JsonValue] = Field(default_factory=dict)
    scope: Literal["all", "movies", "tv"] = "all"

    @field_validator("policy_snapshot")
    @classmethod
    def bound_snapshot(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if len(value) > 64:
            raise ValueError("policy snapshot is bounded to 64 keys")
        return value


class LetterboxDetectionConfigV1(StrictDocument):
    """Bounded detection settings frozen with a read-only letterbox intent."""

    method: Literal["cropdetect", "trim"] = "cropdetect"
    trim_fuzz: tuple[int, ...] = Field(default=(5, 15, 25), min_length=1, max_length=8)
    movie_samples_min: int = Field(default=5, ge=0, le=240)
    movie_samples_max: int = Field(default=60, ge=1, le=480)
    movie_sample_step: int = Field(default=5, ge=1, le=60)
    tv_quick_windows: int = Field(default=3, ge=1, le=32)
    tv_thorough_windows: int = Field(default=8, ge=1, le=64)
    tv_head_skip_pct: int = Field(default=12, ge=0, le=95)
    tv_tail_skip_pct: int = Field(default=12, ge=0, le=95)
    window_seconds: int = Field(default=2, ge=1, le=30)
    cropdetect_limit: int = Field(default=24, ge=0, le=255)
    cropdetect_hdr_limit: int = Field(default=80, ge=0, le=255)
    cropdetect_round: int = Field(default=2, ge=1, le=64)
    noise_px: int = Field(default=4, ge=0, le=256)
    min_bar_px: int = Field(default=8, ge=0, le=512)
    agree_px: int = Field(default=2, ge=0, le=128)
    medium_spread_px: int = Field(default=20, ge=0, le=512)
    variable_gap_px: int = Field(default=40, ge=0, le=1024)
    variable_min_fraction: float = Field(default=0.2, ge=0.0, le=1.0)
    asym_px: int = Field(default=2, ge=0, le=256)
    asymmetric: bool = False
    early_stop_windows: int = Field(default=3, ge=1, le=32)

    @field_validator("trim_fuzz")
    @classmethod
    def bound_trim_fuzz(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(fuzz < 0 or fuzz > 100 for fuzz in value):
            raise ValueError("trim fuzz percentages must be between 0 and 100")
        return value


class LetterboxDetectRequestV1(StrictDocument):
    """Read-only movie/file letterbox observation; crop application is not part of this intent."""

    movie_id: int = Field(ge=1)
    media_file_id: int = Field(ge=1)
    thorough: bool = False
    detection_config: LetterboxDetectionConfigV1 = Field(default_factory=LetterboxDetectionConfigV1)


class LetterboxDetectEpisodeRequestV1(StrictDocument):
    """Read-only observation of one physical episode file and its linked episodes."""

    media_file_id: int = Field(ge=1)
    episode_ids: tuple[int, ...] = Field(min_length=1, max_length=500)
    thorough: bool = False
    detection_config: LetterboxDetectionConfigV1 = Field(default_factory=LetterboxDetectionConfigV1)


class LetterboxDetectTvScopeRequestV1(StrictDocument):
    """Read-only series/season/episode scope; no automatic crop path is represented."""

    series_id: int = Field(ge=1)
    season_number: int | None = Field(default=None, ge=0)
    episode_id: int | None = Field(default=None, ge=1)
    exhaustive: bool = False
    force: bool = False
    include_open_matte: bool = False
    detection_config: LetterboxDetectionConfigV1 = Field(default_factory=LetterboxDetectionConfigV1)


class DoviAnalyzeRequestV1(StrictDocument):
    """Immutable read-only Dolby Vision probe intent for one physical file."""

    media_file_id: int = Field(ge=1)
    movie_id: int | None = Field(default=None, ge=1)
    episode_id: int | None = Field(default=None, ge=1)
    source_signature: str = Field(min_length=40, max_length=128, pattern=r"^[0-9a-f]+$")
    source_codec: str | None = Field(default=None, max_length=32)
    source_hdr_type: str | None = Field(default=None, max_length=80)
    analysis_depth: Literal["standard", "deep"] = "standard"

    @model_validator(mode="after")
    def require_one_domain_subject(self) -> DoviAnalyzeRequestV1:
        if (self.movie_id is None) == (self.episode_id is None):
            raise ValueError("Dolby Vision analysis requires exactly one movie or episode")
        return self


class PosterSourceDescriptorV1(StrictDocument):
    """Server-owned, path-free descriptor for one configured candidate source."""

    provider: Literal["tmdb", "fanart", "local_cache"] = "tmdb"
    reference: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")


class PosterPipelineRequestV1(StrictDocument):
    """Immutable, non-deploying poster-analysis intent for one domain subject."""

    movie_id: int | None = Field(default=None, ge=1)
    series_id: int | None = Field(default=None, ge=1)
    season_id: int | None = Field(default=None, ge=1)
    episode_id: int | None = Field(default=None, ge=1)
    tmdb_id: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=300)
    source_descriptors: tuple[PosterSourceDescriptorV1, ...] = Field(
        default=(), max_length=12
    )
    profile_version: str | None = Field(default=None, max_length=128)
    model_version: str | None = Field(default=None, max_length=128)
    prior_poster_checksum: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]+$"
    )

    @model_validator(mode="after")
    def require_one_subject(self) -> PosterPipelineRequestV1:
        if sum(value is not None for value in (self.movie_id, self.series_id, self.season_id, self.episode_id)) != 1:
            raise ValueError("poster analysis requires exactly one subject")
        return self


class PosterBatchRequestV1(StrictDocument):
    """Sealed server-selected scope for a canonical poster-analysis batch."""

    scope: Literal["selected", "missing", "all", "series"] = "selected"
    selection_count: int = Field(ge=0, le=10_000)


class PosterCandidateSummaryV1(StrictDocument):
    candidate_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")
    source: str = Field(min_length=1, max_length=80)
    decision: Literal["recommended", "accepted", "rejected", "unavailable"]
    gate: str | None = Field(default=None, max_length=80)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    artifact_key: str | None = Field(default=None, max_length=300)


class PosterPipelineResultV1(StrictDocument):
    """Typed analysis result while retaining bounded legacy summary readability."""

    outcome: Literal["succeeded", "no_change", "review_required"] = "succeeded"
    message: str | None = Field(default=None, max_length=1000)
    summary: dict[str, JsonValue] = Field(default_factory=dict)
    subject_label: str = Field(default="Unknown subject", min_length=1, max_length=300)
    source_count: int = Field(default=0, ge=0, le=100)
    candidate_count: int = Field(default=0, ge=0, le=100)
    accepted_count: int = Field(default=0, ge=0, le=100)
    rejected_by_gate: dict[str, int] = Field(default_factory=dict)
    recommendation: PosterCandidateSummaryV1 | None = None
    profile_version: str | None = Field(default=None, max_length=128)
    model_version: str | None = Field(default=None, max_length=128)
    prior_poster_checksum: str | None = Field(default=None, max_length=64)
    review_reason: str | None = Field(default=None, max_length=300)
    warnings: tuple[str, ...] = Field(default=(), max_length=20)
    artifact_ids: tuple[int, ...] = Field(default=(), max_length=12)


class TasteRebuildRequestV1(StrictDocument):
    source: Literal["training_dir", "library"] = "training_dir"
    library: Literal["movies", "tv"] = "movies"
    expected_generation: int = Field(default=0, ge=0)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)


class TasteMapRequestV1(StrictDocument):
    library: Literal["movies", "tv"] = "movies"
    expected_generation: int = Field(default=0, ge=0)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)


class LearnedHeadTrainRequestV1(StrictDocument):
    library: Literal["movies", "tv"] = "movies"
    expected_generation: int = Field(default=0, ge=0)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)


class MlPublicationResultV1(StrictDocument):
    outcome: Literal["succeeded", "no_change", "superseded"] = "succeeded"
    family: Literal["taste_profile", "taste_map", "learned_head"]
    version: str = Field(min_length=1, max_length=128)
    checksum: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    expected_generation: int = Field(ge=0)
    active_generation: int = Field(ge=0)
    activated: bool
    artifact_ids: tuple[int, ...] = Field(default=(), max_length=12)
    metrics: dict[str, float | int] = Field(default_factory=dict)


class PosterRescanRequestV1(StrictDocument):
    scope: Literal["all", "movie", "series"] = "all"
    movie_id: int | None = Field(default=None, ge=1)
    series_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_scoped_subject(self) -> PosterRescanRequestV1:
        if self.scope == "all" and (self.movie_id is not None or self.series_id is not None):
            raise ValueError("all-poster rescan cannot carry a subject id")
        if self.scope == "movie" and (self.movie_id is None or self.series_id is not None):
            raise ValueError("movie-poster rescan requires only movie_id")
        if self.scope == "series" and (self.series_id is None or self.movie_id is not None):
            raise ValueError("series-poster rescan requires only series_id")
        return self


class PosterRescanResultV1(StrictDocument):
    outcome: Literal["succeeded", "no_change"] = "succeeded"
    observed: int = Field(ge=0)
    changed: int = Field(ge=0)
    missing: int = Field(ge=0)
    artifact_ids: tuple[int, ...] = Field(default=(), max_length=4)
    warnings: tuple[str, ...] = Field(default=(), max_length=20)


class BuiltInIntentV1(StrictDocument):
    """Strict union of current legacy intent fields; server policy is never accepted."""

    detector: str | None = Field(default=None, max_length=80)
    thorough: bool | None = None
    episode_ids: tuple[int, ...] = ()
    media_file_id: int | None = None
    season_number: int | None = None
    episode_id: int | None = None
    exhaustive: bool | None = None
    force: bool | None = None
    include_open_matte: bool | None = None
    confidence_levels: tuple[str, ...] = ()
    source: str | None = Field(default=None, max_length=80)
    library: str | None = Field(default=None, max_length=80)
    scope: str | None = Field(default=None, max_length=80)
    series_id: int | None = None
    radarr_id: int | None = None
    tmdb_id: int | None = None
    folder: str | None = Field(default=None, max_length=500)
    movie_ids: tuple[int, ...] = ()
    series_ids: tuple[int, ...] = ()
    analysis_depth: Literal["standard", "deep"] | None = None
    detection_config: LetterboxDetectionConfigV1 | None = None
    assets: tuple[str, ...] = ()
    include_embeddings: bool | None = None
    include_archives: bool | None = None
    dry_run: bool | None = None
    kind: str | None = Field(default=None, max_length=80)

    @field_validator("episode_ids", "movie_ids", "series_ids", "confidence_levels", "assets")
    @classmethod
    def bound_collections(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if len(value) > 10_000:
            raise ValueError("request collections are bounded to 10,000 entries")
        return value


class BuiltInResultV1(StrictDocument):
    outcome: str = Field(default="succeeded", pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    message: str | None = Field(default=None, max_length=1000)
    summary: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("summary")
    @classmethod
    def bound_summary(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if len(value) > 128:
            raise ValueError("result summary is bounded to 128 entries")
        if any(len(key) > 100 for key in value):
            raise ValueError("result summary keys are bounded")
        return value


class SafeJobErrorV1(StrictDocument):
    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    summary: str = Field(min_length=1, max_length=500)
    remediation: str | None = Field(default=None, max_length=1000)
    diagnostics: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("diagnostics")
    @classmethod
    def validate_diagnostics(
        cls, value: dict[str, str | int | float | bool | None]
    ) -> dict[str, str | int | float | bool | None]:
        if len(value) > 32:
            raise ValueError("diagnostics must contain at most 32 entries")
        for key, item in value.items():
            normalized = key.lower()
            if len(key) > 80 or any(token in normalized for token in ("secret", "token", "password")):
                raise ValueError("diagnostic keys must be bounded and non-secret")
            if isinstance(item, str) and (
                len(item) > 500 or item.startswith(("/", "\\\\"))
            ):
                raise ValueError("diagnostic strings must be bounded and path-free")
        return value


DocumentT = TypeVar("DocumentT", bound=StrictDocument)
Upcaster = Callable[[StrictDocument], StrictDocument | Mapping[str, Any]]


@dataclass(frozen=True)
class DocumentAdapter:
    """Validate a stored version and upcast explicitly to the current model."""

    kind: DocumentKind
    current_version: int
    models: Mapping[int, type[StrictDocument]]
    upcasters: Mapping[int, Upcaster]

    def __post_init__(self) -> None:
        models = MappingProxyType(dict(self.models))
        upcasters = MappingProxyType(dict(self.upcasters))
        object.__setattr__(self, "models", models)
        object.__setattr__(self, "upcasters", upcasters)
        if self.current_version < 1 or self.current_version not in models:
            raise ValueError("current document version must have a model")
        if any(version < 1 or version > self.current_version for version in models):
            raise ValueError("document model versions must be positive and not future versions")
        if any(version not in models or version >= self.current_version for version in upcasters):
            raise ValueError("upcasters must start at a supported historical version")

    @property
    def supported_versions(self) -> tuple[int, ...]:
        return tuple(sorted(self.models))

    def validate(self, document: Mapping[str, Any] | StrictDocument, *, version: int) -> StrictDocument:
        model = self.models.get(version)
        if model is None:
            raise UnsupportedDocumentVersionError(
                kind=self.kind,
                version=version,
                supported=self.supported_versions,
            )
        value = document if isinstance(document, model) else model.model_validate(document)
        current_version = version
        while current_version < self.current_version:
            upcaster = self.upcasters.get(current_version)
            if upcaster is None:
                raise UnsupportedDocumentVersionError(
                    kind=self.kind,
                    version=version,
                    supported=(self.current_version,),
                )
            converted = upcaster(value)
            target_version = current_version + 1
            target_model = self.models.get(target_version)
            if target_model is None:
                raise InvalidUpcastError(f"upcaster has no target model for version {target_version}")
            try:
                value = (
                    converted
                    if isinstance(converted, target_model)
                    else target_model.model_validate(converted)
                )
            except (TypeError, ValueError) as exc:
                raise InvalidUpcastError(
                    f"{self.kind.value} upcaster {current_version}->{target_version} failed"
                ) from exc
            current_version = target_version
        return value


def current_adapter(kind: DocumentKind, model: type[StrictDocument]) -> DocumentAdapter:
    return DocumentAdapter(kind=kind, current_version=1, models={1: model}, upcasters={})
