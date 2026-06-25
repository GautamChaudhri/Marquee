"""External (sidecar) subtitle discovery + filename inference (design §20.4).

Scans the media file's directory for subtitle files that belong to it, parsing
language and role tokens from the filename (``Movie (2020).eng.forced.srt``).
Strict matching: a sidecar must share the video's basename (optionally followed
by dot-separated tokens) so we never attribute another movie's subtitle. IDX+SUB
VobSub pairs are collapsed into one logical track.

Raw paths stay server-side; the API exposes only IDs + display-safe names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from marquee.core.subtitles import languages

TEXT_EXTS = {".srt", ".ass", ".ssa", ".vtt"}
BITMAP_EXTS = {".sup", ".idx"}  # .sub handled via pairing
ALL_EXTS = TEXT_EXTS | BITMAP_EXTS | {".sub"}

_FORCED_TOKENS = {"forced"}
_SDH_TOKENS = {"sdh", "hi"}
_CC_TOKENS = {"cc"}
_COMMENTARY_TOKENS = {"commentary", "comm"}
_GENERATED_TOKENS = {"subgen", "ai", "generated", "whisper"}


@dataclass
class ExternalSub:
    path: Path
    ext: str
    kind: str  # text | bitmap | unknown
    paired_path: Path | None = None
    language_tag: str = languages.UNDETERMINED
    language_source: str = "unknown"  # filename | unknown
    is_forced: bool = False
    is_sdh: bool = False
    is_commentary: bool = False
    is_generated: bool = False
    size_bytes: int | None = None
    tokens: list[str] = field(default_factory=list)


def _kind_for_ext(ext: str) -> str:
    if ext in TEXT_EXTS:
        return "text"
    if ext in {".sup", ".idx", ".sub"}:
        return "bitmap"
    return "unknown"


def parse_tokens(tokens: list[str]) -> dict:
    """Classify dot-separated filename tokens into language + role flags."""
    result = {
        "language_tag": languages.UNDETERMINED,
        "language_source": "unknown",
        "is_forced": False,
        "is_sdh": False,
        "is_commentary": False,
        "is_generated": False,
    }
    for token in tokens:
        low = token.lower()
        if low in _FORCED_TOKENS:
            result["is_forced"] = True
        elif low in _SDH_TOKENS:
            result["is_sdh"] = True
        elif low in _CC_TOKENS:
            result["is_sdh"] = True  # closed captions ≈ SDH for our purposes
        elif low in _COMMENTARY_TOKENS:
            result["is_commentary"] = True
        elif low in _GENERATED_TOKENS:
            result["is_generated"] = True
        elif result["language_tag"] == languages.UNDETERMINED:
            tag, _ = languages.normalize(token)
            if tag != languages.UNDETERMINED:
                result["language_tag"] = tag
                result["language_source"] = "filename"
    return result


def _matching_tokens(video_stem: str, sub_stem: str) -> list[str] | None:
    """Return the metadata tokens if *sub_stem* belongs to *video_stem*, else None."""
    if sub_stem == video_stem:
        return []
    prefix = video_stem + "."
    if sub_stem.startswith(prefix):
        return [t for t in sub_stem[len(prefix) :].split(".") if t]
    return None


def discover(video_path: Path) -> list[ExternalSub]:
    """Find sidecar subtitles belonging to *video_path* in its directory."""
    folder = video_path.parent
    video_stem = video_path.stem
    if not folder.is_dir():
        return []

    # Index candidate subtitle files by extension.
    by_ext: dict[str, list[Path]] = {}
    for entry in sorted(folder.iterdir()):
        if not entry.is_file():
            continue
        ext = entry.suffix.lower()
        if ext in ALL_EXTS and _matching_tokens(video_stem, entry.stem) is not None:
            by_ext.setdefault(ext, []).append(entry)

    results: list[ExternalSub] = []
    paired_subs: set[Path] = set()

    # VobSub: pair each .idx with a same-stem .sub.
    for idx_path in by_ext.get(".idx", []):
        sub_partner = idx_path.with_suffix(".sub")
        sub = _make_external(video_stem, idx_path, kind="bitmap")
        if sub_partner.is_file():
            sub.paired_path = sub_partner
            paired_subs.add(sub_partner)
        results.append(sub)

    for ext, paths in by_ext.items():
        if ext == ".idx":
            continue
        for path in paths:
            if ext == ".sub" and path in paired_subs:
                continue  # already represented by its .idx
            results.append(_make_external(video_stem, path, kind=_kind_for_ext(ext)))

    return results


def _make_external(video_stem: str, path: Path, *, kind: str) -> ExternalSub:
    tokens = _matching_tokens(video_stem, path.stem) or []
    parsed = parse_tokens(tokens)
    size = path.stat().st_size if path.is_file() else None
    return ExternalSub(
        path=path,
        ext=path.suffix.lower(),
        kind=kind,
        size_bytes=size,
        tokens=tokens,
        **parsed,
    )
