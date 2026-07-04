"""Pure TV HDR rollup engine — season/show status + uniformity (plan 06 phase 2).

No DB access. Inputs are plain per-episode dicts the routes build; outputs are
plain dicts consumed directly by the API layer. Reuses the movie-side pure
helpers in ``marquee.core.radarr_overlay`` unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from marquee.core.radarr_overlay import (
    classify_hdr_tags,
    distribution_keys,
    ordered_tags,
    overlay_bucket,
    preference_status,
)


@dataclass(frozen=True)
class EpisodeHdr:
    """One episode's HDR truth plus its derived tag/bucket/distribution view.

    ``hdr_type_raw is None`` means unknown (no mediaInfo captured yet) —
    distinct from a confirmed ``"SDR"`` raw value. Unlike the movie-side
    ``distribution_keys()``, which defaults a missing raw value to ``sdr``
    (movies without a file are filtered out upstream before that helper ever
    sees them), TV episodes *with* a file can still have unknown HDR truth,
    so unknown episodes must contribute nothing to tags/bucket/distribution
    here — they are counted separately via ``episodes_unknown``.
    """

    season_number: int
    episode_number: int
    title: str | None
    hdr_type_raw: str | None
    tags: tuple[str, ...] = field(init=False)
    bucket: str = field(init=False)
    distribution: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        known = self.hdr_type_raw is not None
        object.__setattr__(
            self, "tags", tuple(ordered_tags(classify_hdr_tags(self.hdr_type_raw)))
        )
        object.__setattr__(self, "bucket", overlay_bucket(self.hdr_type_raw) if known else "unknown")
        object.__setattr__(
            self, "distribution", tuple(distribution_keys(self.hdr_type_raw)) if known else ()
        )


def episode_status(
    tags: tuple[str, ...] | None,
    profile_targets: Iterable[str],
    meet_target: str | None,
    exceed_target: str | None,
    excluded_targets: Iterable[str] | None = None,
) -> str:
    """Thin wrapper over ``radarr_overlay.preference_status``.

    ``tags=None`` is the sentinel for "hdr_type_raw is None" — pass it
    explicitly for episodes with no mediaInfo instead of their (empty)
    ``EpisodeHdr.tags``, which is indistinguishable from confirmed SDR.
    """
    if tags is None:
        return "unknown"
    return preference_status(
        file_tags=set(tags),
        profile_targets=set(profile_targets),
        meet_target=meet_target,
        exceed_target=exceed_target,
        excluded_targets=excluded_targets,
    )


def _sum_distribution(distributions: Iterable[tuple[str, ...]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for keys in distributions:
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _sum_status_counts(statuses: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    return counts


def season_rollup(episodes: list[EpisodeHdr], statuses: list[str]) -> dict:
    """Roll up one season's episodes into a uniformity + status summary.

    Uniformity is computed over episodes with known raw only: a season is
    ``uniform`` iff all its known episodes share an identical tag-set (SDR's
    empty set counts as a tag-set — an all-SDR season is uniform). A season
    with zero known episodes contributes nothing to uniformity upstream (at
    the show level) — it is reported here as vacuously ``uniform`` with
    ``uniform_tags=None``, and the show rollup excludes it from comparison.
    """
    episodes_total = len(episodes)
    known_pairs = [(ep, status) for ep, status in zip(episodes, statuses, strict=True) if ep.hdr_type_raw is not None]
    episodes_known = len(known_pairs)
    episodes_unknown = episodes_total - episodes_known

    known_tag_sets = {ep.tags for ep, _ in known_pairs}
    if len(known_tag_sets) <= 1:
        uniformity = "uniform"
        uniform_tags = list(next(iter(known_tag_sets))) if known_tag_sets else None
    else:
        uniformity = "mixed"
        uniform_tags = None

    union_tag_set: set[str] = set()
    for ep in episodes:
        union_tag_set.update(ep.tags)

    return {
        "uniformity": uniformity,
        "uniform_tags": uniform_tags,
        "union_tags": ordered_tags(union_tag_set),
        "distribution": _sum_distribution(ep.distribution for ep in episodes),
        "status_counts": _sum_status_counts(statuses),
        "episodes_total": episodes_total,
        "episodes_known": episodes_known,
        "episodes_unknown": episodes_unknown,
    }


def show_rollup(season_rollups: dict[int, dict]) -> dict:
    """Roll up a show's season rollups into one status + uniformity summary.

    Specials (season 0) are excluded (H3).

    Status is computed over non-specials episodes with known status
    (``unknown`` excluded from the verdict but reported in counts): all
    ``exceeds_target`` -> ``exceeds_target``; all >= meet (a mix of
    meets/exceeds) -> ``meets_target``; some >= meet and some
    ``below_target`` -> ``gaps``; none >= meet -> ``below_target``. All
    ``no_hdr_target`` -> ``no_hdr_target``. No known episodes -> ``unknown``.

    Uniformity: a show is ``uniform`` iff all non-specials seasons are
    uniform *and* share the same tag-set; ``uniform_by_season`` iff each
    season is uniform but sets differ; else ``mixed``. Seasons with zero
    known episodes are excluded from this comparison.
    """
    non_special = {number: rollup for number, rollup in season_rollups.items() if number != 0}

    episodes_total = sum(rollup["episodes_total"] for rollup in non_special.values())
    episodes_known = sum(rollup["episodes_known"] for rollup in non_special.values())
    episodes_unknown = sum(rollup["episodes_unknown"] for rollup in non_special.values())

    distribution: dict[str, int] = {}
    for rollup in non_special.values():
        for key, count in rollup["distribution"].items():
            distribution[key] = distribution.get(key, 0) + count

    status_counts: dict[str, int] = {}
    for rollup in non_special.values():
        for key, count in rollup["status_counts"].items():
            status_counts[key] = status_counts.get(key, 0) + count

    union_tag_set: set[str] = set()
    for rollup in non_special.values():
        union_tag_set.update(rollup["union_tags"])
    union_tags = ordered_tags(union_tag_set)

    contributing = [rollup for rollup in non_special.values() if rollup["episodes_known"] > 0]
    if not contributing:
        uniformity = "uniform"
        uniform_tags = None
    elif all(rollup["uniformity"] == "uniform" for rollup in contributing):
        tag_sets = {tuple(rollup["uniform_tags"] or ()) for rollup in contributing}
        if len(tag_sets) == 1:
            uniformity = "uniform"
            uniform_tags = contributing[0]["uniform_tags"]
        else:
            uniformity = "uniform_by_season"
            uniform_tags = None
    else:
        uniformity = "mixed"
        uniform_tags = None

    known_status_counts = {key: n for key, n in status_counts.items() if key != "unknown"}
    total_known = sum(known_status_counts.values())
    exceeds = known_status_counts.get("exceeds_target", 0)
    meets = known_status_counts.get("meets_target", 0)
    below = known_status_counts.get("below_target", 0)
    meeting = exceeds + meets

    if total_known == 0:
        status = "unknown"
    elif set(known_status_counts) == {"no_hdr_target"}:
        status = "no_hdr_target"
    elif below == 0 and meeting > 0 and meets == 0:
        status = "exceeds_target"
    elif below == 0 and meeting > 0:
        status = "meets_target"
    elif below > 0 and meeting > 0:
        status = "gaps"
    else:
        status = "below_target"

    return {
        "status": status,
        "uniformity": uniformity,
        "union_tags": union_tags,
        "uniform_tags": uniform_tags,
        "distribution": distribution,
        "status_counts": status_counts,
        "episodes_total": episodes_total,
        "episodes_known": episodes_known,
        "episodes_unknown": episodes_unknown,
        "meeting_fraction": {"met": meeting, "of": total_known},
    }
