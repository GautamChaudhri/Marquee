"""Generator protocol + value types (design §18.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class GeneratorHealth:
    healthy: bool
    version: str | None = None
    detail: str | None = None


@dataclass
class GeneratorCapabilities:
    id: str
    name: str
    provider: str
    mode: str  # transcribe | translate
    model_label: str
    supports_language_hint: bool = True
    supports_per_request_model: bool = False
    supports_percent_progress: bool = False
    transport: str = "shared_path"  # how the provider receives the media


@dataclass
class GenerationRequest:
    media_file_id: int
    local_media_path: str
    language_hint: str | None = None  # None = auto-detect
    output: str = "external"  # external | embedded


@dataclass
class ProviderSubmission:
    accepted: bool
    submitted_at: str
    expected_output_dir: str | None = None
    detail: str | None = None


@dataclass
class ProviderState:
    state: str  # pending | running | produced | failed | timeout
    output_path: str | None = None
    message: str | None = None
    diagnostics: dict = field(default_factory=dict)


@runtime_checkable
class SubtitleGenerator(Protocol):
    id: str

    async def health(self) -> GeneratorHealth: ...

    def capabilities(self) -> GeneratorCapabilities: ...

    async def submit(self, request: GenerationRequest) -> ProviderSubmission: ...

    async def reconcile(self, request: GenerationRequest) -> ProviderState: ...
