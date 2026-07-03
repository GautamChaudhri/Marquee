"""Unit tests for Dolby Vision analysis (pure logic + mocked probes)."""

from __future__ import annotations

from pathlib import Path

import pytest

from marquee.core import dovi_analysis
from marquee.core.dovi_analysis import conversion_eligibility
from marquee.core.letterbox_reencode import SourceVideo


def _source(**overrides) -> SourceVideo:
    base = {
        "codec": "hevc",
        "width": 3840,
        "height": 2160,
        "pix_fmt": "yuv420p10le",
        "color_transfer": "smpte2084",
        "color_primaries": "bt2020",
        "color_space": "bt2020nc",
        "duration_s": 120.0,
        "has_hdr": True,
        "has_dovi": True,
        "dovi_profile": 8,
        "dovi_level": 6,
        "dovi_el_present": False,
        "dovi_bl_signal_compatibility_id": 1,
        "video_streams": 1,
        "audio_streams": 1,
        "subtitle_streams": 0,
        "attachment_streams": 0,
    }
    base.update(overrides)
    return SourceVideo(**base)


# ---------------------------------------------------------------------------
# conversion_eligibility
# ---------------------------------------------------------------------------


def test_eligibility_profile_5_is_green_tint_convert():
    result = conversion_eligibility(5, None)
    assert result["eligible"] is True
    assert result["kind"] == "p5_to_p81"
    assert result["target"] == "8.1"


def test_eligibility_profile_7_mel_is_lossless_strip():
    result = conversion_eligibility(7, "MEL")
    assert result["eligible"] is True
    assert result["kind"] == "p7_strip_el"


def test_eligibility_profile_7_fel_is_lossy():
    result = conversion_eligibility(7, "FEL")
    assert result["eligible"] == "lossy"
    assert result["kind"] == "p7_strip_el"


def test_eligibility_profile_7_unknown_el_is_lossy():
    result = conversion_eligibility(7, None)
    assert result["eligible"] == "lossy"
    assert result["kind"] == "p7_strip_el"


def test_eligibility_profile_8_not_eligible():
    assert conversion_eligibility(8, None)["eligible"] is False


def test_eligibility_unanalyzed_not_eligible():
    assert conversion_eligibility(None, None)["eligible"] is False


# ---------------------------------------------------------------------------
# _classify_el_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ("Profile: 7\nEL type: FEL\n", "FEL"),
        ("profile 7 (mel)", "MEL"),
        ("Profile: 8 single layer", None),
        ("", None),
        (None, None),
    ],
)
def test_classify_el_type(summary, expected):
    assert dovi_analysis._classify_el_type(summary) == expected


# ---------------------------------------------------------------------------
# analyze_path (mocked ffprobe / RPU probe)
# ---------------------------------------------------------------------------


async def test_analyze_path_probe_failed(monkeypatch):
    monkeypatch.setattr(dovi_analysis, "inspect_source", lambda _p: None)
    result = await dovi_analysis.analyze_path(Path("/movies/x.mkv"))
    assert result.status == "error"
    assert result.error_reason == "probe_failed"


async def test_analyze_path_not_dovi(monkeypatch):
    monkeypatch.setattr(
        dovi_analysis, "inspect_source", lambda _p: _source(has_dovi=False, dovi_profile=None)
    )
    result = await dovi_analysis.analyze_path(Path("/movies/x.mkv"))
    assert result.status == "not_dovi"
    assert result.has_dovi is False


async def test_analyze_path_profile_5_skips_el_probe(monkeypatch):
    monkeypatch.setattr(
        dovi_analysis,
        "inspect_source",
        lambda _p: _source(
            dovi_profile=5, dovi_el_present=False, dovi_bl_signal_compatibility_id=0
        ),
    )

    async def _boom(*_a, **_k):  # must NOT be called for single-layer profiles
        raise AssertionError("RPU probe should not run for profile 5")

    monkeypatch.setattr(dovi_analysis, "_probe_el_type", _boom)
    result = await dovi_analysis.analyze_path(Path("/movies/x.mkv"))
    assert result.status == "analyzed"
    assert result.profile == 5
    assert result.el_type is None


async def test_analyze_path_profile_7_reads_el_type(monkeypatch):
    monkeypatch.setattr(
        dovi_analysis,
        "inspect_source",
        lambda _p: _source(dovi_profile=7, dovi_el_present=True),
    )

    async def _probe(_path):
        return "FEL", "Profile: 7\nEL type: FEL\n"

    monkeypatch.setattr(dovi_analysis, "_probe_el_type", _probe)
    result = await dovi_analysis.analyze_path(Path("/movies/x.mkv"))
    assert result.status == "analyzed"
    assert result.profile == 7
    assert result.el_present is True
    assert result.el_type == "FEL"
    fields = result.to_state_fields()
    assert fields["dovi_profile"] == 7
    assert fields["el_type"] == "FEL"
    assert fields["status"] == "analyzed"
