"""Pure TV letterbox rollups — season/show buckets, verdicts, and uniformity."""

from __future__ import annotations

from dataclasses import dataclass, field

BUCKET_ORDER = (
    "clear",
    "sampled_clear",
    "candidate",
    "tagged",
    "reencoded",
    "variable",
    "ineligible",
    "error",
    "unanalyzed",
)


def episode_bucket(status: str | None) -> str:
    if status == "not_letterboxed":
        return "clear"
    if status == "sampled_clear":
        return "sampled_clear"
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
    bucket: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "bucket", episode_bucket(self.status))


def _bucket_counts(episodes: list[EpisodeLetterbox]) -> dict[str, int]:
    counts = dict.fromkeys(BUCKET_ORDER, 0)
    for episode in episodes:
        counts[episode.bucket] += 1
    return counts


def _dominant_aspect_label(episodes: list[EpisodeLetterbox]) -> str | None:
    counts: dict[str, int] = {}
    for episode in episodes:
        if episode.aspect_label:
            counts[episode.aspect_label] = counts.get(episode.aspect_label, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _season_uniformity(episodes: list[EpisodeLetterbox]) -> str:
    labels = {
        episode.aspect_label
        for episode in episodes
        if episode.bucket in {"candidate", "tagged", "reencoded", "variable"} and episode.aspect_label
    }
    return "uniform" if len(labels) <= 1 else "mixed"


def _verdict_for_counts(counts: dict[str, int]) -> str:
    present = {bucket for bucket, count in counts.items() if count > 0}
    if not present or present <= {"unanalyzed", "ineligible"}:
        return "unanalyzed"
    if present <= {"clear", "sampled_clear"}:
        return "clean"
    if present <= {"clear", "sampled_clear", "tagged", "reencoded"}:
        return "treated"
    if present <= {"clear", "sampled_clear", "candidate"}:
        return "needs_action"
    return "mixed"


def season_rollup(episodes: list[EpisodeLetterbox]) -> dict:
    counts = _bucket_counts(episodes)
    return {
        "bucket_counts": counts,
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

    contributing = [
        rollup
        for rollup in non_special.values()
        if any(rollup["bucket_counts"][bucket] > 0 for bucket in ("candidate", "tagged", "reencoded", "variable"))
    ]
    if not contributing:
        uniformity = "uniform"
    elif all(rollup["uniformity"] == "uniform" for rollup in contributing):
        labels = {rollup["dominant_aspect_label"] for rollup in contributing}
        uniformity = "uniform" if len(labels) <= 1 else "uniform_by_season"
    else:
        uniformity = "mixed"

    return {
        "bucket_counts": counts,
        "dominant_aspect_label": _dominant_aspect_label(
            [
                EpisodeLetterbox(
                    episode_id=0,
                    season_number=0,
                    episode_number=0,
                    title=None,
                    status=None,
                    confidence=None,
                    aspect_label=rollup["dominant_aspect_label"],
                    recommended_crop_top=None,
                    recommended_crop_bottom=None,
                    applied_crop_top=None,
                    applied_crop_bottom=None,
                    eligible=None,
                    reviewed=False,
                )
                for rollup in non_special.values()
                if rollup["dominant_aspect_label"] is not None
            ]
        ),
        "verdict": _verdict_for_counts(counts),
        "uniformity": uniformity,
        "episodes_total": sum(rollup["episodes_total"] for rollup in non_special.values()),
        "has_candidates": counts["candidate"] > 0,
    }
