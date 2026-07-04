"""Pure unit tests for audio/subtitle rollups (plan 08 phase 2)."""

from marquee.core.audio_subs_rollups import (
    EpisodeCoverage,
    episode_status,
    resolve_episode_coverage,
    season_rollup,
    show_rollup,
)


def test_episode_status_matrix_and_same_language_matching():
    preferred_audio = ["en-US"]
    preferred_subs = ["es-MX"]

    assert (
        episode_status(
            EpisodeCoverage(1, 1, ["eng"], ["spa"], "synced"), preferred_audio, preferred_subs
        )
        == "ok"
    )
    assert (
        episode_status(
            EpisodeCoverage(2, 1, ["jpn"], ["spa"], "synced"), preferred_audio, preferred_subs
        )
        == "audio_gap"
    )
    assert (
        episode_status(
            EpisodeCoverage(3, 1, ["eng"], [], "synced"), preferred_audio, preferred_subs
        )
        == "subtitle_gap"
    )
    assert (
        episode_status(
            EpisodeCoverage(4, 1, ["jpn"], ["fra"], "synced"), preferred_audio, preferred_subs
        )
        == "both_gap"
    )
    assert (
        episode_status(
            EpisodeCoverage(5, 1, None, None, "none"), preferred_audio, preferred_subs
        )
        == "unknown"
    )


def test_resolve_episode_coverage_prefers_tier2_truth():
    resolved = resolve_episode_coverage(
        episode_id=1,
        season_number=1,
        synced_audio_languages=["en"],
        synced_subtitle_languages=["en"],
        probed_coverage={
            "audio_languages": ["ja"],
            "full_dialogue_languages": ["es"],
            "forced_only_languages": ["en"],
            "sdh_languages": ["es"],
        },
    )

    assert resolved.tier == "probed"
    assert resolved.audio_languages == ["ja"]
    assert resolved.subtitle_languages == ["es"]
    assert resolved.forced_only_languages == ["en"]
    assert resolved.sdh_languages == ["es"]


def test_season_rollup_counts_gaps_uniformity_and_tier2_flags():
    episodes = [
        EpisodeCoverage(1, 1, ["en"], ["en"], "probed", forced_only_languages=["en"]),
        EpisodeCoverage(2, 1, ["en"], ["en"], "probed", sdh_languages=["en"]),
        EpisodeCoverage(3, 1, ["ja"], ["en"], "synced"),
        EpisodeCoverage(4, 1, None, None, "none"),
    ]

    rollup = season_rollup(episodes, preferred_audio=["en"], preferred_subs=["en"])

    assert rollup["status_counts"] == {"ok": 2, "audio_gap": 1, "unknown": 1}
    assert rollup["dub_coverage"] == {"ok": 2, "of": 3}
    assert rollup["subtitle_coverage"] == {"ok": 3, "of": 3}
    assert rollup["forced_coverage"] == 1
    assert rollup["sdh_coverage"] == 1
    assert rollup["missing_audio_languages"] == ["en"]
    assert rollup["missing_subtitle_languages"] == []
    assert rollup["unknown_count"] == 1
    assert rollup["uniformity"] == "mixed"
    assert rollup["uniform_status"] is None


def test_show_rollup_excludes_specials_and_reports_uniform_by_season():
    season_zero = season_rollup(
        [EpisodeCoverage(10, 0, ["ja"], ["ja"], "synced")],
        preferred_audio=["en"],
        preferred_subs=["en"],
    )
    season_one = season_rollup(
        [EpisodeCoverage(1, 1, ["en"], ["en"], "synced")],
        preferred_audio=["en"],
        preferred_subs=["en"],
    )
    season_two = season_rollup(
        [EpisodeCoverage(2, 2, ["ja"], ["en"], "synced")],
        preferred_audio=["en"],
        preferred_subs=["en"],
    )

    rollup = show_rollup({0: season_zero, 1: season_one, 2: season_two})

    assert rollup["status"] == "gaps"
    assert rollup["status_counts"] == {"ok": 1, "audio_gap": 1}
    assert rollup["episodes_total"] == 2
    assert rollup["episodes_counted"] == 2
    assert rollup["uniformity"] == "uniform_by_season"
    assert rollup["uniform_status"] is None


def test_show_rollup_reports_none_met_and_unknown():
    none_met = show_rollup(
        {
            1: season_rollup(
                [EpisodeCoverage(1, 1, ["ja"], ["fr"], "synced")],
                preferred_audio=["en"],
                preferred_subs=["en"],
            )
        }
    )
    unknown = show_rollup(
        {
            1: season_rollup(
                [EpisodeCoverage(1, 1, None, None, "none")],
                preferred_audio=["en"],
                preferred_subs=["en"],
            )
        }
    )

    assert none_met["status"] == "none_met"
    assert unknown["status"] == "unknown"
