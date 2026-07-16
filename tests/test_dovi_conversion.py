"""Unit tests for canonical Dolby Vision conversion command construction."""

from __future__ import annotations

from pathlib import Path

from marquee.core.jobs.handlers_dovi_conversion import _p5_base_encode_args


def test_profile_5_base_encode_command_is_sealed() -> None:
    source = Path("/movies/source.mkv")
    output = Path("/staging/candidate.mkv")

    args = _p5_base_encode_args(source, output)

    assert args[:5] == ["-y", "-hide_banner", "-nostdin", "-i", str(source)]
    assert args[-1] == str(output)
    assert args[args.index("-c:v:0") + 1] == "libx265"
    assert args[args.index("-progress") + 1] == "pipe:1"
