"""Unit tests for Dolby Vision conversion plumbing."""

from __future__ import annotations

from pathlib import Path

from marquee.core import dovi_conversion
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
        "subtitle_streams": 1,
        "attachment_streams": 0,
    }
    base.update(overrides)
    return SourceVideo(**base)


async def test_p7_convert_uses_mode_2_and_discard(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    async def fake_pipe(source_mkv: Path, dovi_args: list[str], *, timeout: float = 3600):
        captured["source"] = source_mkv
        captured["args"] = dovi_args
        captured["timeout"] = timeout

    monkeypatch.setattr(dovi_conversion, "_pipe_hevc_to_dovi", fake_pipe)
    out = tmp_path / "converted.hevc"

    await dovi_conversion._convert_hevc_stream(
        Path("/movies/source.mkv"), out, mode=2, discard_el=True
    )

    assert captured["args"] == ["--mode", "2", "convert", "--discard", "-", "-o", str(out)]


async def test_p5_inject_uses_mode_3(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    async def fake_pipe(source_mkv: Path, dovi_args: list[str], *, timeout: float = 3600):
        captured["source"] = source_mkv
        captured["args"] = dovi_args

    monkeypatch.setattr(dovi_conversion, "_pipe_hevc_to_dovi", fake_pipe)
    rpu = tmp_path / "RPU.bin"
    out = tmp_path / "injected.hevc"

    await dovi_conversion._inject_converted_rpu(Path("/movies/base.mkv"), rpu, out, mode=3)

    assert captured["args"] == [
        "--mode",
        "3",
        "inject-rpu",
        "-i",
        "-",
        "--rpu-in",
        str(rpu),
        "-o",
        str(out),
    ]


def test_validate_output_accepts_profile_8_candidate(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mkv"
    output_path = tmp_path / "candidate.mkv"
    source_path.write_bytes(b"source")
    output_path.write_bytes(b"candidate")

    monkeypatch.setattr(dovi_conversion, "inspect_source", lambda _p: _source(dovi_profile=8))

    assert (
        dovi_conversion._validate_output(
            source_path, output_path, _source(dovi_profile=5), "p5_to_p81"
        )
        == []
    )


def test_validate_output_rejects_profile_7_el_still_present(monkeypatch, tmp_path):
    source_path = tmp_path / "source.mkv"
    output_path = tmp_path / "candidate.mkv"
    source_path.write_bytes(b"source")
    output_path.write_bytes(b"candidate")

    monkeypatch.setattr(
        dovi_conversion,
        "inspect_source",
        lambda _p: _source(dovi_profile=7, dovi_el_present=True),
    )

    problems = dovi_conversion._validate_output(
        source_path, output_path, _source(dovi_profile=7, dovi_el_present=True), "p7_strip_el"
    )

    assert "output Dolby Vision profile is 7, expected profile 8" in problems
    assert "enhancement layer still detected after Profile 7 strip" in problems
