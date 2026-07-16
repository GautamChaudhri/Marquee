"""Unit tests for the pure Dolby Vision eligibility policy."""

from __future__ import annotations

from marquee.core.dovi_eligibility import conversion_eligibility


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
