"""Pure TV audio/subtitle coverage rollups (plan 08 phase 2).

Episode inputs carry either tier-1 Sonarr sync truth (``tier="synced"``) or
tier-2 inventory truth (``tier="probed"``). Tier-2 subtitle truth uses full-
dialogue languages only; forced-only tracks never satisfy subtitle coverage.

Rules:
- ``tier="none"`` means unknown; those episodes are excluded from the gap
  verdict but counted in ``unknown_count``.
- Language matching always uses ``languages.same_language()``, never raw string
  equality.
- Season uniformity is evaluated over the episode status axis.
- Show rollups exclude specials (season 0) from verdicts and uniformity.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from marquee.core.subtitles import languages


@dataclass(frozen=True)
class EpisodeCoverage:
    episode_id: int
    season_number: int
    audio_languages: list[str] | None
    subtitle_languages: list[str] | None
    tier: str
    forced_only_languages: list[str] | None = None
    sdh_languages: list[str] | None = None


def resolve_episode_coverage(
    *,
    episode_id: int,
    season_number: int,
    synced_audio_languages: list[str] | None,
    synced_subtitle_languages: list[str] | None,
    probed_coverage: Mapping[str, object] | None = None,
) -> EpisodeCoverage:
    """Return the tier-2-wins episode coverage view consumed by rollups."""
    if probed_coverage is not None:
        return EpisodeCoverage(
            episode_id=episode_id,
            season_number=season_number,
            audio_languages=_listish(probed_coverage.get("audio_languages")),
            subtitle_languages=_listish(probed_coverage.get("full_dialogue_languages")),
            forced_only_languages=_listish(probed_coverage.get("forced_only_languages")),
            sdh_languages=_listish(probed_coverage.get("sdh_languages")),
            tier="probed",
        )
    if synced_audio_languages is not None or synced_subtitle_languages is not None:
        return EpisodeCoverage(
            episode_id=episode_id,
            season_number=season_number,
            audio_languages=synced_audio_languages or [],
            subtitle_languages=synced_subtitle_languages or [],
            tier="synced",
        )
    return EpisodeCoverage(
        episode_id=episode_id,
        season_number=season_number,
        audio_languages=None,
        subtitle_languages=None,
        tier="none",
    )


def episode_status(
    cov: EpisodeCoverage,
    preferred_audio: Iterable[str],
    preferred_subs: Iterable[str],
) -> str:
    """Classify one episode against preferred audio/subtitle languages."""
    if cov.tier == "none":
        return "unknown"

    audio_ok = _covers(cov.audio_languages or [], preferred_audio)
    subtitle_ok = _covers(cov.subtitle_languages or [], preferred_subs)
    if audio_ok and subtitle_ok:
        return "ok"
    if not audio_ok and not subtitle_ok:
        return "both_gap"
    if not audio_ok:
        return "audio_gap"
    return "subtitle_gap"


def season_rollup(
    episodes: list[EpisodeCoverage],
    preferred_audio: Iterable[str],
    preferred_subs: Iterable[str],
) -> dict:
    """Roll up one season's episodes into status, uniformity, and coverage counts."""
    statuses = [episode_status(ep, preferred_audio, preferred_subs) for ep in episodes]
    counted_statuses = [status for status in statuses if status != "unknown"]

    status_counts = _sum_status_counts(statuses)
    audio_ok_count = sum(status in {"ok", "subtitle_gap"} for status in counted_statuses)
    subtitle_ok_count = sum(status in {"ok", "audio_gap"} for status in counted_statuses)
    counted = len(counted_statuses)
    unknown_count = len(statuses) - counted

    if not counted_statuses or len(set(counted_statuses)) == 1:
        uniformity = "uniform"
        uniform_status = counted_statuses[0] if counted_statuses else None
    else:
        uniformity = "mixed"
        uniform_status = None

    missing_audio: set[str] = set()
    missing_subtitles: set[str] = set()
    forced_coverage = 0
    sdh_coverage = 0
    for ep, status in zip(episodes, statuses, strict=True):
        if status == "unknown":
            continue
        if status in {"audio_gap", "both_gap"}:
            missing_audio.update(_missing_languages(ep.audio_languages or [], preferred_audio))
        if status in {"subtitle_gap", "both_gap"}:
            missing_subtitles.update(_missing_languages(ep.subtitle_languages or [], preferred_subs))
        if ep.tier == "probed" and ep.forced_only_languages:
            forced_coverage += 1
        if ep.tier == "probed" and ep.sdh_languages:
            sdh_coverage += 1

    return {
        "status_counts": status_counts,
        "missing_audio_languages": _ordered(missing_audio),
        "missing_subtitle_languages": _ordered(missing_subtitles),
        "missing_languages": _ordered(missing_audio | missing_subtitles),
        "dub_coverage": {"ok": audio_ok_count, "of": counted},
        "subtitle_coverage": {"ok": subtitle_ok_count, "of": counted},
        "forced_coverage": forced_coverage,
        "sdh_coverage": sdh_coverage,
        "episodes_total": len(episodes),
        "episodes_counted": counted,
        "unknown_count": unknown_count,
        "uniformity": uniformity,
        "uniform_status": uniform_status,
    }


def show_rollup(seasons: Mapping[int, dict]) -> dict:
    """Roll up season summaries into one show summary, excluding specials."""
    non_special = {number: rollup for number, rollup in seasons.items() if number != 0}
    counted = sum(rollup.get("episodes_counted", 0) for rollup in non_special.values())
    unknown_count = sum(rollup.get("unknown_count", 0) for rollup in non_special.values())
    episodes_total = sum(rollup.get("episodes_total", 0) for rollup in non_special.values())

    status_counts: dict[str, int] = {}
    missing_audio: set[str] = set()
    missing_subtitles: set[str] = set()
    forced_coverage = 0
    sdh_coverage = 0
    audio_ok = 0
    subtitle_ok = 0
    for rollup in non_special.values():
        for key, count in rollup.get("status_counts", {}).items():
            status_counts[key] = status_counts.get(key, 0) + count
        missing_audio.update(rollup.get("missing_audio_languages", []))
        missing_subtitles.update(rollup.get("missing_subtitle_languages", []))
        forced_coverage += int(rollup.get("forced_coverage", 0))
        sdh_coverage += int(rollup.get("sdh_coverage", 0))
        audio_ok += int((rollup.get("dub_coverage") or {}).get("ok", 0))
        subtitle_ok += int((rollup.get("subtitle_coverage") or {}).get("ok", 0))

    contributing = [
        rollup for rollup in non_special.values() if int(rollup.get("episodes_counted", 0)) > 0
    ]
    if not contributing:
        uniformity = "uniform"
        uniform_status = None
    elif all(rollup.get("uniformity") == "uniform" for rollup in contributing):
        statuses = {rollup.get("uniform_status") for rollup in contributing}
        if len(statuses) == 1:
            uniformity = "uniform"
            uniform_status = next(iter(statuses))
        else:
            uniformity = "uniform_by_season"
            uniform_status = None
    else:
        uniformity = "mixed"
        uniform_status = None

    ok_count = status_counts.get("ok", 0)
    gap_count = counted - ok_count
    if counted == 0:
        status = "unknown"
    elif gap_count == 0:
        status = "ok"
    elif ok_count == 0:
        status = "none_met"
    else:
        status = "gaps"

    return {
        "status": status,
        "status_counts": status_counts,
        "missing_audio_languages": _ordered(missing_audio),
        "missing_subtitle_languages": _ordered(missing_subtitles),
        "missing_languages": _ordered(missing_audio | missing_subtitles),
        "dub_coverage": {"ok": audio_ok, "of": counted},
        "subtitle_coverage": {"ok": subtitle_ok, "of": counted},
        "forced_coverage": forced_coverage,
        "sdh_coverage": sdh_coverage,
        "episodes_total": episodes_total,
        "episodes_counted": counted,
        "unknown_count": unknown_count,
        "uniformity": uniformity,
        "uniform_status": uniform_status,
    }


def _covers(available: Iterable[str], preferred: Iterable[str]) -> bool:
    preferred = list(preferred)
    if not preferred:
        return True
    available = list(available)
    return all(any(languages.same_language(candidate, want) for candidate in available) for want in preferred)


def _missing_languages(available: Iterable[str], preferred: Iterable[str]) -> list[str]:
    available = list(available)
    missing: list[str] = []
    for want in preferred:
        if not any(languages.same_language(candidate, want) for candidate in available):
            missing.append(want)
    return missing


def _sum_status_counts(statuses: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    return counts


def _ordered(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


def _listish(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
