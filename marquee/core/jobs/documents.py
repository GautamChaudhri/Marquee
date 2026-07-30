"""Strict versioned request/result/error document adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from marquee.core.jobs.poster_group_limits import MAX_POSTER_GROUP_MEMBERS


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
    source_descriptors: tuple[PosterSourceDescriptorV1, ...] = Field(default=(), max_length=12)
    profile_version: str | None = Field(default=None, max_length=128)
    model_version: str | None = Field(default=None, max_length=128)
    prior_poster_checksum: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]+$"
    )

    @model_validator(mode="after")
    def require_one_subject(self) -> PosterPipelineRequestV1:
        if (
            sum(
                value is not None
                for value in (self.movie_id, self.series_id, self.season_id, self.episode_id)
            )
            != 1
        ):
            raise ValueError("poster analysis requires exactly one subject")
        return self


PosterPipelineSubjectKey = Annotated[
    str,
    Field(
        min_length=7,
        max_length=200,
        pattern=r"^(movie|series|season):[1-9][0-9]*$",
    ),
]


def poster_pipeline_subject_identity(
    request: PosterPipelineRequestV1,
) -> tuple[Literal["movie", "series", "season", "episode"], int]:
    """Return the canonical kind/id identity sealed by a single request."""

    for kind, identifier in (
        ("movie", request.movie_id),
        ("series", request.series_id),
        ("season", request.season_id),
        ("episode", request.episode_id),
    ):
        if identifier is not None:
            return kind, identifier
    raise ValueError("poster analysis requires exactly one subject")


def poster_pipeline_subject_key(request: PosterPipelineRequestV1) -> str:
    kind, identifier = poster_pipeline_subject_identity(request)
    return f"{kind}:{identifier}"


class PosterPipelineGroupRequestV1(StrictDocument):
    """One bounded, same-library selection processed stage-major by a GPU leaf."""

    library: Literal["movies", "tv"]
    chunk_index: int = Field(ge=0)
    batch_mode: Literal["chunked", "all_at_once"] = "chunked"
    members: tuple[PosterPipelineRequestV1, ...] = Field(
        min_length=1, max_length=MAX_POSTER_GROUP_MEMBERS
    )

    @model_validator(mode="after")
    def require_one_library_and_unique_subjects(self) -> PosterPipelineGroupRequestV1:
        identities = tuple(poster_pipeline_subject_identity(member) for member in self.members)
        if any(kind == "episode" for kind, _ in identities):
            raise ValueError("poster groups support movie, series, and season subjects only")
        expected_library = "movies" if identities[0][0] == "movie" else "tv"
        if self.library != expected_library or any(
            (kind == "movie") != (expected_library == "movies") for kind, _ in identities
        ):
            raise ValueError("poster group members must belong to the declared library")
        if len(set(identities)) != len(identities):
            raise ValueError("poster group members must have unique subjects")
        return self


class PosterBatchRequestV1(StrictDocument):
    """Sealed server-selected scope for a canonical poster-analysis batch."""

    scope: Literal["selected", "missing", "all", "series"] = "selected"
    selection_count: int = Field(ge=0, le=10_000)
    grouping_mode: Literal["individual", "chunked", "all_at_once"] | None = None
    configured_chunk_size: int | None = Field(default=None, ge=1, le=16)


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


class PosterPipelineGroupResultV1(StrictDocument):
    """Bounded aggregate; full per-member evidence lives in ``group-result.json``."""

    outcome: Literal["succeeded", "no_change", "review_required"] = "succeeded"
    library: Literal["movies", "tv"]
    chunk_index: int = Field(ge=0)
    member_count: int = Field(ge=1, le=MAX_POSTER_GROUP_MEMBERS)
    succeeded_count: int = Field(default=0, ge=0, le=MAX_POSTER_GROUP_MEMBERS)
    no_change_count: int = Field(default=0, ge=0, le=MAX_POSTER_GROUP_MEMBERS)
    review_required_count: int = Field(default=0, ge=0, le=MAX_POSTER_GROUP_MEMBERS)
    failed_count: int = Field(default=0, ge=0, le=MAX_POSTER_GROUP_MEMBERS)
    projected_count: int = Field(ge=1, le=MAX_POSTER_GROUP_MEMBERS)
    run_ids: tuple[Annotated[str, Field(min_length=1, max_length=64)], ...] = Field(
        min_length=1, max_length=MAX_POSTER_GROUP_MEMBERS
    )
    failed_subject_keys: tuple[PosterPipelineSubjectKey, ...] = Field(
        default=(), max_length=MAX_POSTER_GROUP_MEMBERS
    )
    message: str | None = Field(default=None, max_length=1000)
    warnings: tuple[Annotated[str, Field(min_length=1, max_length=300)], ...] = Field(
        default=(), max_length=20
    )
    artifact_ids: tuple[Annotated[int, Field(ge=1)], ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def require_complete_projection_and_consistent_outcome(self) -> PosterPipelineGroupResultV1:
        terminal_count = (
            self.succeeded_count
            + self.no_change_count
            + self.review_required_count
            + self.failed_count
        )
        if terminal_count != self.member_count:
            raise ValueError("poster group member outcome counts must sum to member_count")
        if self.projected_count != self.member_count or len(self.run_ids) != self.member_count:
            raise ValueError("poster group results require one projection and run id per member")
        if len(self.failed_subject_keys) != self.failed_count:
            raise ValueError("failed_subject_keys must identify every failed member")
        expected_outcome = (
            "review_required"
            if self.failed_count or self.review_required_count
            else "no_change"
            if self.no_change_count == self.member_count
            else "succeeded"
        )
        if self.outcome != expected_outcome:
            raise ValueError("poster group outcome does not match its member outcomes")
        return self


class TasteRebuildRequestV1(StrictDocument):
    source: Literal["training_dir", "library", "canonical_revision"] = "training_dir"
    library: Literal["movies", "tv"] = "movies"
    revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    profile_build_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    expected_generation: int = Field(ge=0)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def require_canonical_revision(self) -> TasteRebuildRequestV1:
        if (self.source == "canonical_revision") != (self.revision is not None):
            raise ValueError("canonical taste rebuild source requires exactly one revision digest")
        if (self.source == "canonical_revision") != (self.profile_build_id is not None):
            raise ValueError("canonical taste rebuild source requires one profile build lineage")
        return self


class TasteMapRequestV1(StrictDocument):
    library: Literal["movies", "tv"] = "movies"
    expected_generation: int = Field(default=0, ge=0)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    profile_revision: str | None = Field(default=None, min_length=1, max_length=128)
    trigger_reference: str | None = Field(default=None, min_length=1, max_length=200)


class TasteEnrichRequestV1(StrictDocument):
    library: Literal["movies", "tv"] = "movies"
    expected_generation: int = Field(default=0, ge=0)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    profile_revision: str | None = Field(default=None, min_length=1, max_length=128)


class RankingResidualTrainRequestV1(StrictDocument):
    library: Literal["movies", "tv"] = "movies"
    expected_generation: int = Field(default=0, ge=0)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    evidence_revision: str = Field(default="manual:unspecified", min_length=1, max_length=128)
    mutation: Literal["apply", "undo", "manual"] = "manual"
    baseline_signature: str | None = Field(default=None, min_length=1, max_length=128)
    profile_checksum: str | None = Field(default=None, min_length=64, max_length=64)
    profile_generation: int | None = Field(default=None, ge=0)


class MlPublicationResultV1(StrictDocument):
    outcome: Literal["succeeded", "no_change", "superseded"] = "succeeded"
    family: Literal["taste_profile", "taste_map", "ranking_residual"]
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

    episode_ids: tuple[int, ...] = ()
    media_file_id: int | None = None
    season_number: int | None = None
    episode_id: int | None = None
    force: bool | None = None
    source: str | None = Field(default=None, max_length=80)
    library: str | None = Field(default=None, max_length=80)
    scope: str | None = Field(default=None, max_length=80)
    series_id: int | None = None
    radarr_id: int | None = None
    tmdb_id: int | None = None
    folder: str | None = Field(default=None, max_length=500)
    movie_ids: tuple[int, ...] = ()
    series_ids: tuple[int, ...] = ()
    assets: tuple[str, ...] = ()
    include_embeddings: bool | None = None
    include_archives: bool | None = None
    dry_run: bool | None = None
    kind: str | None = Field(default=None, max_length=80)

    @field_validator("episode_ids", "movie_ids", "series_ids", "assets")
    @classmethod
    def bound_collections(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if len(value) > 10_000:
            raise ValueError("request collections are bounded to 10,000 entries")
        return value


class BuiltInResultV1(StrictDocument):
    outcome: Literal[
        "succeeded",
        "partially_succeeded",
        "no_change",
        "failed",
        "cancelled",
        "superseded",
        "unsafe",
    ] = "succeeded"
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
            if len(key) > 80 or any(
                token in normalized for token in ("secret", "token", "password")
            ):
                raise ValueError("diagnostic keys must be bounded and non-secret")
            if isinstance(item, str) and (len(item) > 500 or item.startswith(("/", "\\\\"))):
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

    def validate(
        self, document: Mapping[str, Any] | StrictDocument, *, version: int
    ) -> StrictDocument:
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
                raise InvalidUpcastError(
                    f"upcaster has no target model for version {target_version}"
                )
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
