"""Matroska mutation adapter — drives ``mkvmerge`` (design §23.4).

mkvmerge writes a fresh container selecting/copying tracks; it uses its own
*track IDs* (captured at inventory time), not ffprobe stream indices. All flags
(language, name, default/forced) are applied per appended subtitle input.
"""

from __future__ import annotations

from pathlib import Path

from marquee.core.subtitles.adapters.base import (
    EmbedSource,
    MetadataEdit,
    RemovePlan,
    UnsupportedContainerError,
)
from marquee.media import binaries


def _yesno(flag: bool) -> str:
    return "yes" if flag else "no"


class MatroskaAdapter:
    binary = "mkvmerge"

    def build_remove(self, src: Path, out: Path, plan: RemovePlan) -> list[str]:
        """Keep only the surviving subtitle tracks (or drop all subtitles)."""
        if not plan.keep_tool_track_ids and not plan.remove_tool_track_ids:
            raise UnsupportedContainerError(
                "Matroska subtitle removal requires mkvmerge track IDs; rescan with mkvmerge installed"
            )
        args = ["-o", binaries.safe_media_path(out)]
        if plan.keep_tool_track_ids:
            args += ["--subtitle-tracks", ",".join(str(i) for i in plan.keep_tool_track_ids)]
        elif plan.remove_tool_track_ids:
            # Negated form: keep everything except these.
            args += ["--subtitle-tracks", "!" + ",".join(str(i) for i in plan.remove_tool_track_ids)]
        else:
            args += ["--no-subtitles"]
        args += [binaries.safe_media_path(src)]
        return args

    def build_embed(self, src: Path, out: Path, sources: list[EmbedSource]) -> list[str]:
        """Copy the source then append each external subtitle as a new track."""
        args = ["-o", binaries.safe_media_path(out), binaries.safe_media_path(src)]
        for source in sources:
            # Flags target track 0 of each appended subtitle input.
            args += ["--language", f"0:{source.language_tag}"]
            if source.title:
                args += ["--track-name", f"0:{source.title}"]
            args += ["--default-track", f"0:{_yesno(source.is_default)}"]
            args += ["--forced-track", f"0:{_yesno(source.is_forced)}"]
            if source.is_sdh:
                args += ["--hearing-impaired-flag", "0:yes"]
            args += [binaries.safe_media_path(source.path)]
        return args

    def build_metadata(self, src: Path, out: Path, edits: list[MetadataEdit]) -> list[str]:
        """Remux applying per-track metadata (track_ref = mkvmerge track id)."""
        args = ["-o", binaries.safe_media_path(out)]
        for edit in edits:
            ref = edit.track_ref
            if edit.language_tag is not None:
                args += ["--language", f"{ref}:{edit.language_tag}"]
            if edit.title is not None:
                args += ["--track-name", f"{ref}:{edit.title}"]
            if edit.is_default is not None:
                args += ["--default-track", f"{ref}:{_yesno(edit.is_default)}"]
            if edit.is_forced is not None:
                args += ["--forced-track", f"{ref}:{_yesno(edit.is_forced)}"]
            if edit.is_sdh is not None:
                args += ["--hearing-impaired-flag", f"{ref}:{_yesno(edit.is_sdh)}"]
        args += [binaries.safe_media_path(src)]
        return args

    def build_extract(
        self, src: Path, out: Path, *, stream_index: int, tool_track_id: int | None
    ) -> tuple[str, list[str]]:
        """Extract one subtitle track via ``mkvextract`` (track-id based)."""
        track = tool_track_id if tool_track_id is not None else stream_index
        return "mkvextract", [
            binaries.safe_media_path(src),
            "tracks",
            f"{track}:{binaries.safe_media_path(out)}",
        ]
