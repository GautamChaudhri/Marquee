"""Scheduler tests for the nightly audio/subtitles deep scan."""

from marquee.core.jobs.scheduler import _enabled_schedules
from marquee.core.subtitles.config import subtitle_settings


def test_audio_subs_deep_scan_schedule_enabled(monkeypatch):
    monkeypatch.setattr(subtitle_settings, "AUDIO_SUBS_DEEP_SCAN_ENABLED", True)

    schedules = _enabled_schedules()

    assert schedules["audio-subs-deep-scan"] == ("audio_subs_deep_scan", 86400)
