"""Mutation adapter interface + shared request types (design §23.4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


class UnsupportedContainerError(Exception):
    """The container cannot be mutated (read-only)."""


@dataclass
class EmbedSource:
    """An external subtitle to embed, with the flags it should carry."""

    path: Path
    language_tag: str = "und"
    title: str | None = None
    is_default: bool = False
    is_forced: bool = False
    is_sdh: bool = False
    kind: str = "text"


@dataclass
class MetadataEdit:
    """A per-track metadata change (by tool track id / stream index)."""

    track_ref: int
    language_tag: str | None = None
    title: str | None = None
    is_default: bool | None = None
    is_forced: bool | None = None
    is_sdh: bool | None = None


@dataclass
class RemovePlan:
    """Tracks to drop, identified by both numbering schemes."""

    remove_tool_track_ids: list[int] = field(default_factory=list)
    remove_stream_indices: list[int] = field(default_factory=list)
    keep_tool_track_ids: list[int] = field(default_factory=list)


@runtime_checkable
class MutationAdapter(Protocol):
    """Builds tool argument lists for one container family. No execution here."""

    binary: str  # the system tool this adapter drives (e.g. "mkvmerge")

    def build_remove(self, src: Path, out: Path, plan: RemovePlan) -> list[str]: ...

    def build_embed(self, src: Path, out: Path, sources: list[EmbedSource]) -> list[str]: ...

    def build_metadata(self, src: Path, out: Path, edits: list[MetadataEdit]) -> list[str]: ...

    def build_extract(self, src: Path, out: Path, *, stream_index: int, tool_track_id: int | None) -> tuple[str, list[str]]: ...
