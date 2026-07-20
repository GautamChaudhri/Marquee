"""Permanent letterbox re-encode backend tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from marquee.core import letterbox_transcode as lr
from marquee.core.media_files import ResolvedMediaFile, compute_signature


def test_choose_encoder_prefers_nvidia(monkeypatch):
    monkeypatch.setattr(lr, "ffmpeg_encoders", lambda: {"hevc_nvenc", "hevc_qsv", "libx265"})
    choice = lr.choose_encoder("hevc", allow_cpu=True)
    assert choice["encoder"] == "hevc_nvenc"
    assert choice["family"] == "nvidia"
    assert choice["used_cpu_fallback"] is False


def test_choose_encoder_uses_cpu_fallback_when_allowed(monkeypatch):
    monkeypatch.setattr(lr, "ffmpeg_encoders", lambda: {"libx265"})
    choice = lr.choose_encoder("hevc", allow_cpu=True)
    assert choice["encoder"] == "libx265"
    assert choice["family"] == "cpu"
    assert choice["used_cpu_fallback"] is True


def test_choose_encoder_rejects_cpu_when_disabled(monkeypatch):
    monkeypatch.setattr(lr, "ffmpeg_encoders", lambda: {"libx265"})
    with pytest.raises(lr.ReencodePlanError) as exc:
        lr.choose_encoder("hevc", allow_cpu=False)
    assert exc.value.code == "encoder_unavailable"


def test_choose_encoder_defaults_set_family_preset(monkeypatch):
    monkeypatch.setattr(lr, "ffmpeg_encoders", lambda: {"hevc_nvenc", "libx265"})
    choice = lr.choose_encoder("hevc", allow_cpu=True)
    assert choice["preset"] == "p7"  # nvidia default
    assert choice["quality"] == 16


def test_choose_encoder_honors_explicit_encoder_override(monkeypatch):
    monkeypatch.setattr(lr, "ffmpeg_encoders", lambda: {"hevc_nvenc", "libx265"})
    # Source is HEVC and NVENC is available, but the user forces CPU libx265.
    choice = lr.choose_encoder("hevc", allow_cpu=True, requested_encoder="libx265")
    assert choice["encoder"] == "libx265"
    assert choice["family"] == "cpu"
    assert choice["used_cpu_fallback"] is True


def test_choose_encoder_honors_quality_and_preset_override(monkeypatch):
    monkeypatch.setattr(lr, "ffmpeg_encoders", lambda: {"hevc_nvenc"})
    choice = lr.choose_encoder("hevc", allow_cpu=True, requested_quality=22, requested_preset="p4")
    assert choice["quality"] == 22
    assert choice["preset"] == "p4"


def test_choose_encoder_rejects_unavailable_override(monkeypatch):
    monkeypatch.setattr(lr, "ffmpeg_encoders", lambda: {"libx265"})
    with pytest.raises(lr.ReencodePlanError) as exc:
        lr.choose_encoder("hevc", allow_cpu=True, requested_encoder="hevc_nvenc")
    assert exc.value.code == "encoder_unavailable"


def test_choose_encoder_codec_override_targets_h264(monkeypatch):
    monkeypatch.setattr(lr, "ffmpeg_encoders", lambda: {"h264_nvenc", "hevc_nvenc"})
    # Source HEVC but user forces H.264 output.
    choice = lr.choose_encoder("hevc", allow_cpu=True, requested_codec="h264")
    assert choice["codec"] == "h264"
    assert choice["encoder"] == "h264_nvenc"


def test_build_ffmpeg_args_honors_custom_preset(tmp_path):
    plan = {
        "crop": {"top": 100, "bottom": 100},
        "source": {"has_hdr": False, "pix_fmt": "yuv420p"},
        "encoder": {"encoder": "libx265", "family": "cpu", "quality": 20, "preset": "veryslow"},
    }
    args = lr.build_ffmpeg_args(tmp_path / "in.mkv", tmp_path / "out.mkv", plan)
    assert args[args.index("-preset") + 1] == "veryslow"
    assert args[args.index("-crf") + 1] == "20"


def test_build_ffmpeg_args_reencodes_video_and_copies_other_streams(tmp_path):
    src = tmp_path / "input.mkv"
    out = tmp_path / "out.mkv"
    plan = {
        "crop": {"top": 268, "bottom": 268},
        "source": {
            "has_hdr": True,
            "pix_fmt": "yuv420p10le",
            "color_primaries": "bt2020",
            "color_transfer": "smpte2084",
            "color_space": "bt2020nc",
        },
        "encoder": {"encoder": "hevc_nvenc", "family": "nvidia", "quality": 16},
    }
    args = lr.build_ffmpeg_args(src, out, plan)
    assert args[args.index("-c") + 1] == "copy"
    assert args[args.index("-c:v:0") + 1] == "hevc_nvenc"
    assert "crop=iw:ih-536:0:268,format=p010le" in args
    assert args[args.index("-color_trc") + 1] == "smpte2084"
    assert str(out) == args[-1]


def test_nvidia_acceleration_plan_preflights_matching_decoder(tmp_path, monkeypatch):
    source = tmp_path / "input.mkv"
    source.write_bytes(b"video")
    calls: list[list[str]] = []
    monkeypatch.setattr(lr.settings, "LETTERBOX_REENCODE_NVIDIA_ACCELERATION", "auto")
    monkeypatch.setattr(lr, "ffmpeg_hwaccels", lambda: {"cuda"})
    monkeypatch.setattr(lr, "ffmpeg_decoders", lambda: {"hevc_cuvid"})
    monkeypatch.setattr(
        lr.binaries,
        "run",
        lambda _name, args, timeout: calls.append(args) or SimpleNamespace(ok=True, stderr=""),
    )

    acceleration = lr.nvidia_acceleration_plan(
        source, "hevc", {"family": "nvidia", "encoder": "hevc_nvenc"}
    )

    assert acceleration == {
        "enabled": True,
        "mode": "nvidia_zero_copy",
        "decoder": "hevc_cuvid",
        "reason": None,
    }
    assert calls[0][calls[0].index("-c:v:0") + 1] == "hevc_cuvid"
    assert "-hwaccel_output_format" in calls[0]


def test_nvidia_acceleration_plan_reports_missing_decoder(monkeypatch, tmp_path):
    monkeypatch.setattr(lr.settings, "LETTERBOX_REENCODE_NVIDIA_ACCELERATION", "auto")
    monkeypatch.setattr(lr, "ffmpeg_hwaccels", lambda: {"cuda"})
    monkeypatch.setattr(lr, "ffmpeg_decoders", set)

    acceleration = lr.nvidia_acceleration_plan(
        tmp_path / "input.mkv", "hevc", {"family": "nvidia", "encoder": "hevc_nvenc"}
    )

    assert acceleration["enabled"] is False
    assert acceleration["decoder"] is None
    assert "hevc_cuvid" in acceleration["reason"]


def test_build_ffmpeg_args_uses_zero_copy_nvidia_pipeline(tmp_path):
    src = tmp_path / "input.mkv"
    out = tmp_path / "out.mkv"
    plan = {
        "crop": {"top": 68, "bottom": 68},
        "source": {
            "has_hdr": True,
            "pix_fmt": "yuv420p10le",
            "color_primaries": "bt2020",
            "color_transfer": "smpte2084",
            "color_space": "bt2020nc",
        },
        "encoder": {
            "codec": "hevc",
            "encoder": "hevc_nvenc",
            "family": "nvidia",
            "quality": 24,
        },
        "acceleration": {
            "enabled": True,
            "mode": "nvidia_zero_copy",
            "decoder": "hevc_cuvid",
            "reason": None,
        },
    }

    args = lr.build_ffmpeg_args(src, out, plan)

    assert args[args.index("-hwaccel") + 1] == "cuda"
    assert args[args.index("-hwaccel_output_format") + 1] == "cuda"
    assert args[args.index("-c:v:0") + 1] == "hevc_cuvid"
    assert args[args.index("-crop") + 1] == "68x68x0x0"
    assert args.index("-hwaccel") < args.index("-i")
    assert "-filter:v:0" not in args
    assert "p010le" not in args
    assert args[args.index("-color_trc") + 1] == "smpte2084"


def test_parse_progress_exposes_speed_and_fps():
    values: dict[str, str] = {}
    for line in ("out_time_ms=12000000", "fps=59.94", "speed=2.50x"):
        assert lr._parse_progress(line, 60, values) is None

    assert lr._parse_progress("progress=continue", 60, values) == {
        "out_time_seconds": 12.0,
        "percent": 20.0,
        "fps": 59.94,
        "speed": 2.5,
    }


def _encode_source_info() -> lr.SourceVideo:
    return lr.SourceVideo(
        codec="hevc",
        width=1920,
        height=1080,
        pix_fmt="yuv420p",
        color_transfer=None,
        color_primaries=None,
        color_space=None,
        duration_s=60,
        has_hdr=False,
        has_dovi=False,
        dovi_profile=None,
        dovi_level=None,
        dovi_el_present=None,
        dovi_bl_signal_compatibility_id=None,
        video_streams=1,
        audio_streams=0,
        subtitle_streams=0,
        attachment_streams=0,
    )


def test_inspect_source_extracts_dovi_details(monkeypatch):
    monkeypatch.setattr(
        lr,
        "_ffprobe_json",
        lambda path: {
            "format": {"duration": "100.0"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 3840,
                    "height": 2160,
                    "pix_fmt": "yuv420p10le",
                    "color_transfer": "smpte2084",
                    "side_data_list": [
                        {
                            "side_data_type": "DOVI configuration record",
                            "dv_profile": 8,
                            "dv_level": 6,
                            "el_present_flag": 0,
                            "dv_bl_signal_compatibility_id": 1,
                        }
                    ],
                }
            ],
        },
    )
    source = lr.inspect_source("/movie.mkv")
    assert source.has_dovi is True
    assert source.dovi_profile == 8
    assert source.dovi_level == 6
    assert source.dovi_el_present is False
    assert lr.dovi_info(source)["preservation"]["status"] in {"preserve_planned", "tool_missing"}


def test_dovi_plan_supported_when_tool_present(monkeypatch):
    monkeypatch.setattr(
        lr.binaries, "resolve", lambda name: "/usr/bin/dovi_tool" if name == "dovi_tool" else None
    )
    source = lr.SourceVideo(
        codec="hevc",
        width=3840,
        height=2160,
        pix_fmt="yuv420p10le",
        color_transfer="smpte2084",
        color_primaries="bt2020",
        color_space="bt2020nc",
        duration_s=100,
        has_hdr=True,
        has_dovi=True,
        dovi_profile=8,
        dovi_level=6,
        dovi_el_present=False,
        dovi_bl_signal_compatibility_id=1,
        video_streams=1,
        audio_streams=1,
        subtitle_streams=1,
        attachment_streams=0,
    )
    dovi, warnings = lr._dovi_plan(source, "hevc")
    assert dovi["status"] == "preserve_planned"
    assert dovi["supported"] is True
    assert warnings[0]["code"] == "dovi_preserve_planned"


def test_dovi_plan_rejects_profile_7_as_not_fully_preservable(monkeypatch):
    monkeypatch.setattr(
        lr.binaries, "resolve", lambda name: "/usr/bin/dovi_tool" if name == "dovi_tool" else None
    )
    source = lr.SourceVideo(
        codec="hevc",
        width=3840,
        height=2160,
        pix_fmt="yuv420p10le",
        color_transfer="smpte2084",
        color_primaries="bt2020",
        color_space="bt2020nc",
        duration_s=100,
        has_hdr=True,
        has_dovi=True,
        dovi_profile=7,
        dovi_level=6,
        dovi_el_present=True,
        dovi_bl_signal_compatibility_id=6,
        video_streams=1,
        audio_streams=1,
        subtitle_streams=1,
        attachment_streams=0,
    )
    dovi, warnings = lr._dovi_plan(source, "hevc")
    assert dovi["status"] == "unsupported_profile"
    assert dovi["supported"] is False
    assert "profile 7" in warnings[0]["message"]


def test_build_dovi_remux_args_replaces_only_video_stream(tmp_path):
    encoded = tmp_path / "encoded.mkv"
    injected = tmp_path / "injected.hevc"
    out = tmp_path / "out.mkv"
    args = lr.build_dovi_remux_args(encoded, injected, out)
    assert args[args.index("-map") + 1] == "1:v:0"
    assert "0:a?" in args
    assert "0:s?" in args
    assert "0:t?" in args
    assert args[-1] == str(out)


@pytest.mark.asyncio
async def test_build_plan_rejects_non_mkv(tmp_path):
    path = tmp_path / "Movie.mp4"
    path.write_bytes(b"video")
    resolved = ResolvedMediaFile(
        media_file_id=1,
        source="radarr",
        path=path,
        size_bytes=path.stat().st_size,
        mtime_ns=path.stat().st_mtime_ns,
        st_nlink=1,
        signature=compute_signature(path),
        container="mp4",
        movie_id=1,
    )
    with pytest.raises(lr.ReencodePlanError) as exc:
        await lr.build_plan(None, resolved, top=10, bottom=10)
    assert exc.value.code == "not_mkv"
