"""Pure TV letterbox rollups — season/show buckets, verdicts, and uniformity."""

from __future__ import annotations

from dataclasses import dataclass, field

BUCKET_ORDER = (
    "widescreen",
    "sampled_widescreen",
    "candidate",
    "tagged",
    "reencoded",
    "variable",
    "open_matte",
    "pillarbox",
    "ineligible",
    "error",
    "unanalyzed",
)
BAR_BEARING_BUCKETS = {"candidate", "tagged", "reencoded", "variable"}
CONTENT_TYPE_ORDER = ("widescreen", "open_matte", "pillarbox")


def episode_bucket(status: str | None) -> str:
    if status == "not_letterboxed":
        return "widescreen"
    if status == "sampled_clear":
        return "sampled_widescreen"
    if status in {"candidate", "skipped"}:
        return "candidate"
    if status == "tagged":
        return "tagged"
    if status == "reencoded":
        return "reencoded"
    if status == "variable_unsafe":
        return "variable"
    if status == "ineligible":
        return "ineligible"
    if status == "errored":
        return "error"
    return "unanalyzed"


@dataclass(frozen=True)
class EpisodeLetterbox:
    episode_id: int
    season_number: int
    episode_number: int
    title: str | None
    status: str | None
    confidence: str | None
    aspect_label: str | None
    recommended_crop_top: int | None
    recommended_crop_bottom: int | None
    applied_crop_top: int | None
    applied_crop_bottom: int | None
    eligible: bool | None
    reviewed: bool
    media_file_id: int | None = None
    bucket_override: str | None = None
    bucket: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "bucket", self.bucket_override or episode_bucket(self.status))


def _bucket_counts(episodes: list[EpisodeLetterbox]) -> dict[str, int]:
    counts = dict.fromkeys(BUCKET_ORDER, 0)
    for episode in episodes:
        counts[episode.bucket] += 1
    return counts


def _dominant_aspect_label(episodes: list[EpisodeLetterbox]) -> str | None:
    counts: dict[str, int] = {}
    for episode in episodes:
        if episode.bucket in BAR_BEARING_BUCKETS and episode.aspect_label:
            counts[episode.aspect_label] = counts.get(episode.aspect_label, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _presentation_label(episode: EpisodeLetterbox) -> str | None:
    if episode.bucket in {"widescreen", "sampled_widescreen"}:
        return "widescreen"
    if episode.bucket in {"open_matte", "pillarbox"}:
        return episode.bucket
    if episode.bucket in {"candidate", "tagged", "reencoded"}:
        return episode.aspect_label
    return None


def _season_uniformity(episodes: list[EpisodeLetterbox]) -> str | None:
    if any(episode.bucket == "variable" for episode in episodes):
        return "dirty_mixed"

    labels = {_presentation_label(episode) for episode in episodes}
    labels.discard(None)
    if not labels:
        return None
    return "uniform" if len(labels) == 1 else "dirty_mixed"


def _verdict_for_counts(counts: dict[str, int]) -> str:
    if counts["candidate"] or counts["error"]:
        return "needs_action"
    if counts["tagged"] or counts["reencoded"]:
        return "treated"
    if any(
        counts[bucket]
        for bucket in ("widescreen", "sampled_widescreen", "open_matte", "pillarbox", "variable")
    ):
        return "ok"
    return "unanalyzed"


def _content_types(counts: dict[str, int]) -> list[dict[str, int | str]]:
    content_counts = {
        "widescreen": counts["widescreen"] + counts["sampled_widescreen"],
        "open_matte": counts["open_matte"],
        "pillarbox": counts["pillarbox"],
    }
    return [
        {"type": content_type, "count": count}
        for content_type, count in sorted(
            content_counts.items(), key=lambda item: (-item[1], CONTENT_TYPE_ORDER.index(item[0]))
        )
        if count
    ]


def season_rollup(episodes: list[EpisodeLetterbox]) -> dict:
    counts = _bucket_counts(episodes)
    return {
        "bucket_counts": counts,
        "content_types": _content_types(counts),
        "dominant_aspect_label": _dominant_aspect_label(episodes),
        "verdict": _verdict_for_counts(counts),
        "uniformity": _season_uniformity(episodes),
        "episodes_total": len(episodes),
        "has_candidates": counts["candidate"] > 0,
    }


def show_rollup(season_rollups: dict[int, dict]) -> dict:
    non_special = {number: rollup for number, rollup in season_rollups.items() if number != 0}
    counts = dict.fromkeys(BUCKET_ORDER, 0)
    for rollup in non_special.values():
        for bucket, value in rollup["bucket_counts"].items():
            counts[bucket] += value

    voting_seasons = [rollup for rollup in non_special.values() if rollup["uniformity"] is not None]
    if not voting_seasons:
        uniformity = None
    elif any(rollup["uniformity"] != "uniform" for rollup in voting_seasons):
        uniformity = "dirty_mixed"
    else:
        label_sets = {
            tuple(content_type["type"] for content_type in rollup["content_types"])
            or (rollup["dominant_aspect_label"],)
            for rollup in voting_seasons
        }
        uniformity = "uniform" if len(label_sets) == 1 else "clean_mixed"

    dominant_counts: dict[str, int] = {}
    for rollup in non_special.values():
        label = rollup["dominant_aspect_label"]
        if label is not None:
            dominant_counts[label] = dominant_counts.get(label, 0) + 1

    return {
        "bucket_counts": counts,
        "content_types": _content_types(counts),
        "dominant_aspect_label": (
            sorted(dominant_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
            if dominant_counts
            else None
        ),
        "verdict": _verdict_for_counts(counts),
        "uniformity": uniformity,
        "episodes_total": sum(rollup["episodes_total"] for rollup in non_special.values()),
        "has_candidates": counts["candidate"] > 0,
    }
