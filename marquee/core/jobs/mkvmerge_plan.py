"""Build mkvmerge argument arrays from a frozen plan (JMC5B §6.1).

§6.1 requires command arrays to be built from the frozen plan, never from client
arguments: a request carries durable selectors only, and the resolved tool track
identities are derived here from the authoritative probe.

Everything not explicitly targeted is preserved — attachments, chapters, tags,
non-target streams, and the metadata of surviving tracks — because mkvmerge
copies them by default and this builder never passes a flag that would drop them.
"""

from __future__ import annotations

from collections.abc import Sequence

from marquee.core.jobs.track_selectors import TrackEntryV1, TrackInventoryV1

#: `--gui-mode` is mandatory so B09 can parse native progress.
BASE_ARGS = ("mkvmerge", "--gui-mode")


class MkvmergePlanError(RuntimeError):
    """A remux plan cannot be expressed safely as mkvmerge arguments."""


def _tool_ids(entries: Sequence[TrackEntryV1]) -> list[int]:
    ids: list[int] = []
    for entry in entries:
        if entry.tool_track_id is None:
            raise MkvmergePlanError("a remux target has no resolved tool track identity")
        ids.append(entry.tool_track_id)
    return ids


def _kept(inventory: TrackInventoryV1, removed_keys: set[str], kind: str) -> list[TrackEntryV1]:
    return [
        entry
        for entry in inventory.entries
        if entry.facts.kind == kind and entry.track_key not in removed_keys
    ]


def _selection_flag(
    args: list[str], flag: str, no_flag: str, kept: Sequence[TrackEntryV1], present: bool
) -> None:
    if not present:
        return
    if not kept:
        args.append(no_flag)
        return
    args += [flag, ",".join(str(value) for value in _tool_ids(kept))]


def build_remove_args(
    *,
    source: str,
    destination: str,
    inventory: TrackInventoryV1,
    removed: Sequence[TrackEntryV1],
) -> tuple[str, ...]:
    """Express a removal as an explicit keep-list over the authoritative inventory."""
    if not removed:
        raise MkvmergePlanError("a removal plan must remove at least one track")
    removed_keys = {entry.track_key for entry in removed}
    if len(removed_keys) != len(removed):
        raise MkvmergePlanError("a track may only be removed once")
    known = {entry.track_key for entry in inventory.entries}
    if not removed_keys <= known:
        raise MkvmergePlanError("a removal target is absent from the resolved inventory")

    has_audio = any(entry.facts.kind == "audio" for entry in inventory.entries)
    has_subtitles = any(entry.facts.kind == "subtitle" for entry in inventory.entries)
    kept_audio = _kept(inventory, removed_keys, "audio")
    kept_subtitles = _kept(inventory, removed_keys, "subtitle")
    if not kept_audio and not kept_subtitles and not _kept(inventory, removed_keys, "video"):
        raise MkvmergePlanError("a removal plan may not empty the file")

    args = [*BASE_ARGS, "-o", destination]
    # Only constrain a kind that the source actually has, so mkvmerge keeps
    # everything else (video, attachments, chapters, tags) untouched.
    _selection_flag(args, "--audio-tracks", "--no-audio", kept_audio, has_audio)
    _selection_flag(args, "--subtitle-tracks", "--no-subtitles", kept_subtitles, has_subtitles)
    args.append(source)
    return tuple(args)


def build_reorder_args(
    *,
    source: str,
    destination: str,
    inventory: TrackInventoryV1,
    ordered_audio: Sequence[TrackEntryV1],
) -> tuple[str, ...]:
    """Express an audio reorder as an explicit complete track order."""
    audio = [entry for entry in inventory.entries if entry.facts.kind == "audio"]
    if len(ordered_audio) != len(audio):
        raise MkvmergePlanError("an audio reorder must order every audio track")
    ordered_keys = {entry.track_key for entry in ordered_audio}
    if ordered_keys != {entry.track_key for entry in audio}:
        raise MkvmergePlanError("an audio reorder must reference exactly the present audio tracks")

    order = ",".join(f"0:{value}" for value in _tool_ids(ordered_audio))
    return (*BASE_ARGS, "-o", destination, "--track-order", order, source)


#: mkvpropedit type-relative selector prefixes; these are 1-based within a kind.
_SELECTOR_PREFIX = {"audio": "a", "subtitle": "s", "video": "v"}


def _type_relative_selector(entry: TrackEntryV1, inventory: TrackInventoryV1) -> str:
    """Address a track the way mkvpropedit documents it: 1-based within its kind.

    ``track:@N`` selects by Matroska ``TrackNumber``, which is *not* mkvmerge's
    0-based track id, so it is deliberately avoided here.
    """
    prefix = _SELECTOR_PREFIX.get(entry.facts.kind)
    if prefix is None:
        raise MkvmergePlanError("a metadata target has an unsupported track kind")
    same_kind = [item for item in inventory.entries if item.facts.kind == entry.facts.kind]
    for position, item in enumerate(same_kind, start=1):
        if item.track_key == entry.track_key:
            return f"track:{prefix}{position}"
    raise MkvmergePlanError("a metadata target is absent from the resolved inventory")


def build_metadata_args(
    *,
    source: str,
    inventory: TrackInventoryV1,
    edits: Sequence[tuple[TrackEntryV1, dict[str, object]]],
) -> tuple[str, ...]:
    """Express subtitle metadata edits as an in-place mkvpropedit invocation.

    Metadata never rewrites stream payloads, so this uses mkvpropedit rather than
    a full remux.
    """
    if not edits:
        raise MkvmergePlanError("a metadata plan must change at least one track")
    args: list[str] = ["mkvpropedit", source]
    for entry, changes in edits:
        if not changes:
            raise MkvmergePlanError("a metadata target must request at least one change")
        args += ["--edit", _type_relative_selector(entry, inventory)]
        for name, value in sorted(changes.items()):
            if isinstance(value, bool):
                args += ["--set", f"{name}={1 if value else 0}"]
            elif value is None:
                args += ["--delete", name]
            else:
                args += ["--set", f"{name}={value}"]
    return tuple(args)
