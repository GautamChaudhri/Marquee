"""Container capability matrix (design §11, §23.4) — pure logic.

Encodes what each container can do so the frontend never has to. MKV is fully
capable; MP4 embeds only text (SRT→mov_text, no ASS styling, no bitmap); other
containers are read-only for mutation. Every "cannot" carries a machine code +
human reason the API passes straight through.
"""

from __future__ import annotations

_MKV_FORMATS = {"matroska", "webm"}
_MP4_FORMATS = {"mov", "mp4", "m4a", "m4v", "3gp", "3g2", "mj2"}


def container_family(container: str | None) -> str:
    """Map an ffprobe ``format_name`` to ``mkv`` | ``mp4`` | ``other``."""
    if not container:
        return "other"
    names = {part.strip() for part in container.lower().split(",")}
    if names & _MKV_FORMATS:
        return "mkv"
    if names & _MP4_FORMATS:
        return "mp4"
    return "other"


def can_remove(family: str) -> tuple[bool, str | None]:
    if family in ("mkv", "mp4"):
        return True, None
    return False, "container_not_writable"


def can_embed(family: str, *, kind: str, codec: str | None = None) -> tuple[bool, str | None]:
    """Whether a subtitle of *kind*/*codec* can be embedded into *family*."""
    if family == "mkv":
        return True, None
    if family == "mp4":
        if kind == "text":
            return True, None  # converted to mov_text
        if kind == "bitmap":
            return False, "mp4_no_bitmap_subtitles"
        return False, "mp4_unsupported_subtitle_kind"
    return False, "container_not_writable"


def capabilities_for(family: str) -> dict:
    """High-level capability summary for the inventory/API response."""
    remove_ok, remove_reason = can_remove(family)
    text_ok, text_reason = can_embed(family, kind="text")
    bitmap_ok, bitmap_reason = can_embed(family, kind="bitmap")
    return {
        "container_family": family,
        "can_remove": remove_ok,
        "can_remove_reason": remove_reason,
        "can_embed_text": text_ok,
        "can_embed_text_reason": text_reason,
        "can_embed_image": bitmap_ok,
        "can_embed_image_reason": bitmap_reason,
        "can_embed_ass_styled": family == "mkv",
        "can_edit_metadata": family in ("mkv", "mp4"),
        "can_extract": family in ("mkv", "mp4"),
        "recommend_mkv": family != "mkv",
        "mov_text_conversion": family == "mp4",
    }
