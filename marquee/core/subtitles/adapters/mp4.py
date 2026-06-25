"""MP4/MOV mutation adapter — drives ``ffmpeg`` (design §23.4).

Explicit stream mapping + ``-c copy`` so video/audio pass through untouched.
Subtitle embedding converts text → ``mov_text`` (the only widely-supported MP4
subtitle codec); ASS styling and bitmap subtitles are rejected upstream by the
capability matrix before a plan is ever built.
"""

from __future__ import annotations

from pathlib import Path

from marquee.core.subtitles.adapters.base import EmbedSource, MetadataEdit, RemovePlan
from marquee.media import binaries


class Mp4Adapter:
    binary = "ffmpeg"

    def build_remove(self, src: Path, out: Path, plan: RemovePlan) -> list[str]:
        """Map everything, then negate the removed subtitle and audio streams; copy codecs."""
        args = ["-y", "-i", binaries.safe_media_path(src), "-map", "0"]
        for index in plan.remove_stream_indices:
            args += ["-map", f"-0:{index}"]
        for index in plan.remove_audio_stream_indices:
            args += ["-map", f"-0:{index}"]
        args += ["-c", "copy", "-movflags", "+faststart", binaries.safe_media_path(out)]
        return args

    def build_embed(self, src: Path, out: Path, sources: list[EmbedSource]) -> list[str]:
        """Add each external text subtitle as a mov_text track with metadata."""
        args = ["-y", "-i", binaries.safe_media_path(src)]
        for source in sources:
            args += ["-i", binaries.safe_media_path(source.path)]
        args += ["-map", "0"]
        for i, _ in enumerate(sources, start=1):
            args += ["-map", str(i)]
        args += ["-c", "copy", "-c:s", "mov_text"]
        # Metadata + dispositions are positional over the *output* subtitle
        # streams (s:0, s:1, ...) appended after any existing subtitle streams.
        for out_sub_index, source in enumerate(sources):
            args += [f"-metadata:s:s:{out_sub_index}", f"language={source.language_tag}"]
            if source.title:
                args += [f"-metadata:s:s:{out_sub_index}", f"title={source.title}"]
            disp = []
            if source.is_default:
                disp.append("default")
            if source.is_forced:
                disp.append("forced")
            args += [f"-disposition:s:{out_sub_index}", "+".join(disp) if disp else "0"]
        args += ["-movflags", "+faststart", binaries.safe_media_path(out)]
        return args

    def build_metadata(self, src: Path, out: Path, edits: list[MetadataEdit]) -> list[str]:
        """Copy all streams, applying per-audio/subtitle metadata/dispositions."""
        args = ["-y", "-i", binaries.safe_media_path(src), "-map", "0", "-c", "copy"]
        for edit in edits:
            ref = edit.track_ref  # type-relative index (a:N / s:N)
            kind = "a" if edit.stream_type == "audio" else "s"
            if edit.language_tag is not None:
                args += [f"-metadata:s:{kind}:{ref}", f"language={edit.language_tag}"]
            if edit.title is not None:
                args += [f"-metadata:s:{kind}:{ref}", f"title={edit.title}"]
            disp = []
            if edit.is_default:
                disp.append("default")
            if edit.is_forced:
                disp.append("forced")
            if edit.is_sdh:
                disp.append("hearing_impaired")
            if edit.is_commentary:
                disp.append("comment")
            if any(
                flag is not None
                for flag in (
                    edit.is_default,
                    edit.is_forced,
                    edit.is_sdh,
                    edit.is_commentary,
                )
            ):
                args += [f"-disposition:{kind}:{ref}", "+".join(disp) if disp else "0"]
        args += ["-movflags", "+faststart", binaries.safe_media_path(out)]
        return args

    def build_extract(
        self, src: Path, out: Path, *, stream_index: int, tool_track_id: int | None
    ) -> tuple[str, list[str]]:
        """Extract one subtitle stream via ffmpeg (by global stream index)."""
        return "ffmpeg", [
            "-y",
            "-i",
            binaries.safe_media_path(src),
            "-map",
            f"0:{stream_index}",
            binaries.safe_media_path(out),
        ]
