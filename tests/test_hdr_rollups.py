"""Unit tests for the pure TV HDR rollup engine (plan 06 phase 2)."""

from marquee.core.hdr_rollups import EpisodeHdr, episode_status, season_rollup, show_rollup


def _ep(season: int, episode: int, raw: str | None, title: str | None = None) -> EpisodeHdr:
    return EpisodeHdr(season_number=season, episode_number=episode, title=title, hdr_type_raw=raw)


def _statuses(episodes: list[EpisodeHdr], *, meet=None, exceed=None, targets=("hdr", "hdr10", "hdr10p", "dovi")) -> list[str]:
    return [
        episode_status(ep.tags if ep.hdr_type_raw is not None else None, targets, meet, exceed)
        for ep in episodes
    ]


class TestEpisodeHdr:
    def test_known_sdr_has_empty_tags_and_sdr_distribution(self):
        ep = _ep(1, 1, "SDR")
        assert ep.tags == ()
        assert ep.bucket == "sdr"
        assert ep.distribution == ("sdr",)

    def test_unknown_raw_is_distinct_from_sdr(self):
        ep = _ep(1, 1, None)
        assert ep.tags == ()
        assert ep.bucket == "unknown"
        assert ep.distribution == ()  # not ("sdr",) — must not masquerade as SDR

    def test_hdr10_tags_and_distribution(self):
        ep = _ep(1, 1, "HDR10")
        assert ep.tags == ("hdr10",)
        assert ep.bucket == "hdr10"
        assert ep.distribution == ("hdr10",)

    def test_dovi_no_fallback_tags(self):
        ep = _ep(1, 1, "DV")
        assert ep.tags == ("dovi", "dovi_no_fallback")
        assert ep.bucket == "dovi"


class TestEpisodeStatus:
    def test_none_tags_sentinel_is_unknown(self):
        assert episode_status(None, {"hdr10"}, "sdr", "hdr10") == "unknown"

    def test_empty_profile_targets_is_no_hdr_target(self):
        assert episode_status((), set(), "sdr", "hdr10") == "no_hdr_target"

    def test_meets_and_exceeds_wrap_preference_status(self):
        assert episode_status(("hdr10",), {"hdr10"}, "sdr", "hdr10") == "exceeds_target"
        assert episode_status((), {"hdr10"}, "sdr", "hdr10") == "meets_target"


class TestSeasonRollup:
    def test_uniform_all_sdr(self):
        episodes = [_ep(1, i, "SDR") for i in range(1, 4)]
        statuses = _statuses(episodes)
        rollup = season_rollup(episodes, statuses)
        assert rollup["uniformity"] == "uniform"
        assert rollup["uniform_tags"] == []
        assert rollup["episodes_total"] == 3
        assert rollup["episodes_known"] == 3
        assert rollup["episodes_unknown"] == 0
        assert rollup["distribution"] == {"sdr": 3}

    def test_mixed_tags_within_season(self):
        episodes = [_ep(1, 1, "SDR"), _ep(1, 2, "HDR10")]
        statuses = _statuses(episodes)
        rollup = season_rollup(episodes, statuses)
        assert rollup["uniformity"] == "mixed"
        assert rollup["uniform_tags"] is None
        assert set(rollup["union_tags"]) == {"hdr10"}

    def test_zero_known_episodes_is_vacuously_uniform(self):
        episodes = [_ep(1, 1, None), _ep(1, 2, None)]
        statuses = _statuses(episodes)
        rollup = season_rollup(episodes, statuses)
        assert rollup["uniformity"] == "uniform"
        assert rollup["uniform_tags"] is None
        assert rollup["episodes_known"] == 0
        assert rollup["episodes_unknown"] == 2

    def test_unknown_episodes_excluded_from_distribution(self):
        episodes = [_ep(1, 1, "HDR10"), _ep(1, 2, None)]
        statuses = _statuses(episodes)
        rollup = season_rollup(episodes, statuses)
        assert rollup["distribution"] == {"hdr10": 1}
        assert rollup["episodes_unknown"] == 1


class TestShowRollupStatus:
    def test_all_exceeds_target(self):
        episodes = [_ep(1, i, "DV HDR10") for i in range(1, 4)]
        statuses = _statuses(episodes, meet="sdr", exceed="dovi_fallback")
        rollup = season_rollup(episodes, statuses)
        show = show_rollup({1: rollup})
        assert show["status"] == "exceeds_target"
        assert show["meeting_fraction"] == {"met": 3, "of": 3}

    def test_all_meets_mix_of_meets_and_exceeds(self):
        episodes = [_ep(1, 1, "HDR10"), _ep(1, 2, "DV HDR10")]
        statuses = _statuses(episodes, meet="hdr10", exceed="dovi_fallback")
        assert statuses == ["meets_target", "exceeds_target"]
        rollup = season_rollup(episodes, statuses)
        show = show_rollup({1: rollup})
        assert show["status"] == "meets_target"
        assert show["meeting_fraction"] == {"met": 2, "of": 2}

    def test_gaps_some_meet_some_below(self):
        episodes = [_ep(1, 1, "HDR10"), _ep(1, 2, "SDR")]
        statuses = _statuses(episodes, meet="hdr10", exceed="dovi_fallback")
        assert statuses == ["meets_target", "below_target"]
        rollup = season_rollup(episodes, statuses)
        show = show_rollup({1: rollup})
        assert show["status"] == "gaps"
        assert show["meeting_fraction"] == {"met": 1, "of": 2}

    def test_below_target_none_meet(self):
        episodes = [_ep(1, i, "SDR") for i in range(1, 3)]
        statuses = _statuses(episodes, meet="hdr10", exceed="dovi_fallback")
        assert statuses == ["below_target", "below_target"]
        rollup = season_rollup(episodes, statuses)
        show = show_rollup({1: rollup})
        assert show["status"] == "below_target"
        assert show["meeting_fraction"] == {"met": 0, "of": 2}

    def test_no_hdr_target_when_profile_has_no_targets(self):
        episodes = [_ep(1, i, "SDR") for i in range(1, 3)]
        statuses = _statuses(episodes, meet=None, exceed=None, targets=())
        assert statuses == ["no_hdr_target", "no_hdr_target"]
        rollup = season_rollup(episodes, statuses)
        show = show_rollup({1: rollup})
        assert show["status"] == "no_hdr_target"

    def test_unknown_when_no_known_episodes_anywhere(self):
        episodes = [_ep(1, 1, None), _ep(1, 2, None)]
        statuses = _statuses(episodes)
        rollup = season_rollup(episodes, statuses)
        show = show_rollup({1: rollup})
        assert show["status"] == "unknown"
        assert show["meeting_fraction"] == {"met": 0, "of": 0}
        assert show["episodes_unknown"] == 2

    def test_unknown_episodes_excluded_from_verdict_but_counted(self):
        episodes = [_ep(1, 1, "HDR10"), _ep(1, 2, "HDR10"), _ep(1, 3, None)]
        statuses = _statuses(episodes, meet="hdr10", exceed="dovi_fallback")
        rollup = season_rollup(episodes, statuses)
        show = show_rollup({1: rollup})
        assert show["status"] == "meets_target"
        assert show["episodes_known"] == 2
        assert show["episodes_unknown"] == 1
        assert show["episodes_total"] == 3


class TestShowRollupUniformity:
    def test_uniform_show_single_season(self):
        episodes = [_ep(1, i, "SDR") for i in range(1, 4)]
        statuses = _statuses(episodes)
        rollup = season_rollup(episodes, statuses)
        show = show_rollup({1: rollup})
        assert show["uniformity"] == "uniform"
        assert show["uniform_tags"] == []

    def test_uniform_by_season_when_seasons_uniform_but_differ(self):
        s1 = [_ep(1, i, "SDR") for i in range(1, 3)]
        s2 = [_ep(2, i, "HDR10") for i in range(1, 3)]
        r1 = season_rollup(s1, _statuses(s1))
        r2 = season_rollup(s2, _statuses(s2))
        show = show_rollup({1: r1, 2: r2})
        assert show["uniformity"] == "uniform_by_season"
        assert show["uniform_tags"] is None

    def test_mixed_when_a_season_is_internally_mixed(self):
        s1 = [_ep(1, i, "SDR") for i in range(1, 3)]
        s2 = [_ep(2, 1, "SDR"), _ep(2, 2, "HDR10")]
        r1 = season_rollup(s1, _statuses(s1))
        r2 = season_rollup(s2, _statuses(s2))
        show = show_rollup({1: r1, 2: r2})
        assert show["uniformity"] == "mixed"

    def test_specials_excluded_from_uniformity_and_status(self):
        specials = [_ep(0, 1, "DV")]  # would break uniformity/status if included
        s1 = [_ep(1, i, "SDR") for i in range(1, 3)]
        r0 = season_rollup(specials, _statuses(specials, meet="hdr10", exceed="dovi_fallback"))
        r1 = season_rollup(s1, _statuses(s1, meet="hdr10", exceed="dovi_fallback"))
        show = show_rollup({0: r0, 1: r1})
        assert show["uniformity"] == "uniform"
        assert show["uniform_tags"] == []
        assert show["status"] == "below_target"
        assert show["episodes_total"] == 2  # specials excluded from totals too

    def test_season_with_zero_known_excluded_from_uniformity_comparison(self):
        s1 = [_ep(1, i, "SDR") for i in range(1, 3)]
        s2 = [_ep(2, 1, None), _ep(2, 2, None)]  # unanalyzed season
        r1 = season_rollup(s1, _statuses(s1))
        r2 = season_rollup(s2, _statuses(s2))
        show = show_rollup({1: r1, 2: r2})
        assert show["uniformity"] == "uniform"
        assert show["uniform_tags"] == []
        assert show["episodes_unknown"] == 2

    def test_single_season_show_all_unknown(self):
        episodes = [_ep(1, 1, None)]
        rollup = season_rollup(episodes, _statuses(episodes))
        show = show_rollup({1: rollup})
        assert show["status"] == "unknown"
        assert show["uniformity"] == "uniform"
        assert show["uniform_tags"] is None
