"""Strict versioned request/result/error document adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


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
    assets: tuple[str, ...] = ()
    include_embeddings: bool | None = None
    include_archives: bool | None = None
    dry_run: bool | None = None
    kind: str | None = Field(default=None, max_length=80)

    @field_validator("episode_ids", "movie_ids", "confidence_levels", "assets")
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
