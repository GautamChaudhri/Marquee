"""Subtitle management pure tests — no real media or async database required."""

from __future__ import annotations

import pytest

from marquee.core.media_files import ResolvedMediaFile
from marquee.core.subtitles import capabilities, coverage, external, languages, mutation
from marquee.core.subtitles.policy import evaluate_policy
from marquee.core.subtitles.service import text_preview


def test_language_normalization_aliases_and_names():
    assert languages.normalize("eng") == ("en", "English")
    assert languages.normalize("fre") == ("fr", "French")
    assert languages.normalize("Brazilian Portuguese") == ("pt-BR", "Portuguese (Brazil)")
    assert languages.normalize("junk-nope") == ("und", "Undetermined")
    assert languages.same_language("eng", "en-US") is True


def test_external_discovery_matches_video_stem_and_pairs_vobsub(tmp_path):
    video = tmp_path / "Movie.Name.2024.mkv"
    video.write_bytes(b"not-real-video")

    (tmp_path / "Movie.Name.2024.eng.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    (tmp_path / "Movie.Name.2024.en.forced.sdh.subgen.ass").write_text("[Script Info]\n")
    (tmp_path / "Movie.Name.2024.jpn.idx").write_text("# idx")
    (tmp_path / "Movie.Name.2024.jpn.sub").write_bytes(b"sub")
    (tmp_path / "Other.Movie.eng.srt").write_text("wrong movie")

    tracks = external.discover(video)
    by_name = {track.path.name: track for track in tracks}

    assert set(by_name) == {
        "Movie.Name.2024.eng.srt",
        "Movie.Name.2024.en.forced.sdh.subgen.ass",
        "Movie.Name.2024.jpn.idx",
    }
    assert by_name["Movie.Name.2024.eng.srt"].language_tag == "en"
    generated = by_name["Movie.Name.2024.en.forced.sdh.subgen.ass"]
    assert generated.is_forced is True
    assert generated.is_sdh is True
    assert generated.is_generated is True
    assert by_name["Movie.Name.2024.jpn.idx"].paired_path == tmp_path / "Movie.Name.2024.jpn.sub"


def test_coverage_separates_full_forced_commentary_and_missing_preferred():
    tracks = [
        {"id": "en-full", "source": "embedded", "language_tag": "en"},
        {"id": "fr-forced", "source": "embedded", "language_tag": "fr", "is_forced": True},
        {"id": "es-commentary", "source": "external", "language_tag": "es", "is_commentary": True},
        {"id": "und-gen", "source": "external", "language_tag": "und", "is_generated": True},
    ]
    audio = [{"language_tag": "en"}, {"language_tag": "ja"}]

    summary = coverage.compute_coverage(tracks, audio, preferred_languages=["en", "ja"])

    assert summary["full_dialogue_languages"] == ["en", "und"]
    assert summary["forced_only_languages"] == ["fr"]
    assert summary["missing_preferred_audio_languages"] == []
    assert summary["missing_preferred_languages"] == ["ja"]
    assert summary["status"] == "gap"
    assert summary["commentary_present"] is True
    assert summary["external_present"] is True
    assert summary["embedded_present"] is True
    assert summary["generated_present"] is True


def test_coverage_uses_split_audio_subtitle_preferences():
    tracks = [{"id": "en-full", "source": "embedded", "language_tag": "en"}]
    audio = [
        {"language_tag": "en", "channels": 6, "channel_layout": "5.1"},
        {"language_tag": "fr", "channels": 2, "channel_layout": "stereo"},
    ]

    summary = coverage.compute_coverage(
        tracks,
        audio,
        preferred_languages=["en"],
        preferred_audio_languages=["fr"],
        preferred_subtitle_languages=["ja"],
    )

    assert summary["audio_channels_by_language"] == {"en": ["5.1"], "fr": ["2.0"]}
    assert summary["missing_preferred_audio_languages"] == []
    assert summary["missing_preferred_languages"] == ["ja"]
    assert summary["audio_status"] == "ok"
    assert summary["subtitle_status"] == "gap"


def test_policy_blocklist_protects_forced_unknown_and_external_by_default():
    tracks = [
        {"id": "en", "source": "embedded", "language_tag": "en"},
        {"id": "fr", "source": "embedded", "language_tag": "fr"},
        {"id": "ja-forced", "source": "embedded", "language_tag": "ja", "is_forced": True},
        {"id": "und", "source": "embedded", "language_tag": "und"},
        {"id": "de-ext", "source": "external", "language_tag": "de"},
    ]
    policy = {
        "mode": "blocklist",
        "languages": ["fr", "ja", "de"],
        "unknown_action": "keep",
        "protect_forced": True,
        "include_external": False,
    }

    result = evaluate_policy(tracks, [], policy)

    assert result.removals == ["fr"]
    assert result.reasons["ja-forced"] == "forced_protected"
    assert result.reasons["und"] == "unknown_kept"
    assert result.reasons["de-ext"] == "external_protected"


def test_policy_protects_last_full_dialogue_track():
    tracks = [{"id": "fr", "source": "embedded", "language_tag": "fr"}]
    policy = {
        "mode": "allowlist",
        "languages": ["en"],
        "protect_last_full_dialogue": True,
    }

    result = evaluate_policy(tracks, [], policy)

    assert result.removals == []
    assert result.reasons["fr"] == "last_full_dialogue_protected"


def test_container_capabilities_are_frontend_ready():
    mkv = capabilities.capabilities_for(capabilities.container_family("matroska,webm"))
    mp4 = capabilities.capabilities_for(capabilities.container_family("mov,mp4,m4a"))
    other = capabilities.capabilities_for(capabilities.container_family("avi"))

    assert mkv["can_embed_text"] is True
    assert mkv["can_embed_image"] is True
    assert mp4["can_embed_text"] is True
    assert mp4["can_embed_image"] is False
    assert mp4["can_embed_image_reason"] == "mp4_no_bitmap_subtitles"
    assert other["can_remove"] is False
    assert other["can_remove_reason"] == "container_not_writable"


def test_text_preview_reads_first_cues(tmp_path):
    sub = tmp_path / "Movie.en.srt"
    sub.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello there\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nGeneral Kenobi\n",
        encoding="utf-8",
    )

    preview = text_preview(sub, max_cues=1)

    assert preview["previewable"] is True
    assert preview["cue_count"] == 2
    assert preview["cues"] == [{"start_ms": 1000, "end_ms": 2000, "text": "Hello there"}]


@pytest.mark.asyncio
async def test_remove_plan_blocks_missing_mkv_track_ids(tmp_path):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"fake")
    resolved = ResolvedMediaFile(
        media_file_id=1,
        source="radarr",
        path=media,
        size_bytes=media.stat().st_size,
        mtime_ns=media.stat().st_mtime_ns,
        st_nlink=1,
        signature="sig",
        container="mkv",
        movie_id=1,
    )
    inventory = {
        "container_family": "mkv",
        "capabilities": capabilities.capabilities_for("mkv"),
        "coverage": {},
        "audio_streams": [],
        "tracks": [
            {
                "id": "embedded-no-tool-id",
                "source": "embedded",
                "language_tag": "fr",
                "tool_track_id": None,
            },
            {
                "id": "external",
                "source": "external",
                "language_tag": "en",
                "tool_track_id": None,
            },
        ],
    }

    plan = await mutation.build_plan(
        None,
        resolved,
        inventory,
        operation="subtitle_remove",
        params={"track_ids": ["embedded-no-tool-id", "external"]},
    )

    codes = {warning["code"] for warning in plan["warnings"]}
    assert "mkv_track_ids_unavailable" in codes
    assert plan["capabilities"]["can_execute"] is False


@pytest.mark.asyncio
async def test_external_only_remove_plan_can_execute(tmp_path):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"fake")
    resolved = ResolvedMediaFile(
        media_file_id=1,
        source="radarr",
        path=media,
        size_bytes=media.stat().st_size,
        mtime_ns=media.stat().st_mtime_ns,
        st_nlink=1,
        signature="sig",
        container="mkv",
        movie_id=1,
    )
    inventory = {
        "container_family": "mkv",
        "capabilities": capabilities.capabilities_for("mkv"),
        "coverage": {},
        "audio_streams": [],
        "tracks": [
            {
                "id": "external",
                "source": "external",
                "language_tag": "en",
                "tool_track_id": None,
            },
        ],
    }

    plan = await mutation.build_plan(
        None,
        resolved,
        inventory,
        operation="subtitle_remove",
        params={"track_ids": ["external"]},
    )

    assert plan["warnings"] == [{"code": "all_subtitles_removed", "requires_override": True}]
    assert plan["capabilities"]["can_execute"] is True


@pytest.mark.asyncio
async def test_audio_remove_plan(tmp_path):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"fake")
    resolved = ResolvedMediaFile(
        media_file_id=1,
        source="radarr",
        path=media,
        size_bytes=media.stat().st_size,
        mtime_ns=media.stat().st_mtime_ns,
        st_nlink=1,
        signature="sig",
        container="mkv",
        movie_id=1,
    )
    inventory = {
        "container_family": "mkv",
        "capabilities": capabilities.capabilities_for("mkv"),
        "coverage": {
            "audio_languages": ["en", "fr"],
        },
        "audio_streams": [
            {"index": 1, "language_tag": "en", "tool_track_id": 1},
            {"index": 2, "language_tag": "fr", "tool_track_id": 2},
        ],
        "tracks": [],
    }

    # Remove the French audio stream (index 2)
    plan = await mutation.build_plan(
        None,
        resolved,
        inventory,
        operation="audio_remove",
        params={"track_ids": [], "audio_stream_indices": [2]},
    )

    assert plan["capabilities"]["can_execute"] is True
    # The coverage after should not contain French audio
    assert plan["after"]["coverage"]["audio_languages"] == ["en"]

    # Now remove all audio streams
    plan_all = await mutation.build_plan(
        None,
        resolved,
        inventory,
        operation="audio_remove",
        params={"track_ids": [], "audio_stream_indices": [1, 2]},
    )
    # Check that it warns about all audio removed
    assert any(w["code"] == "all_audio_removed" for w in plan_all["warnings"])


@pytest.mark.asyncio
async def test_track_remove_plan_supports_mixed_subtitle_and_audio_removal(tmp_path):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"fake")
    resolved = ResolvedMediaFile(
        media_file_id=1,
        source="radarr",
        path=media,
        size_bytes=media.stat().st_size,
        mtime_ns=media.stat().st_mtime_ns,
        st_nlink=1,
        signature="sig",
        container="mkv",
        movie_id=1,
    )
    inventory = {
        "container_family": "mkv",
        "capabilities": capabilities.capabilities_for("mkv"),
        "coverage": {
            "audio_languages": ["en", "fr"],
            "full_dialogue_languages": ["en", "es"],
        },
        "audio_streams": [
            {"index": 1, "language_tag": "en", "tool_track_id": 1},
            {"index": 2, "language_tag": "fr", "tool_track_id": 2},
        ],
        "tracks": [
            {
                "id": "sub-es",
                "source": "embedded",
                "language_tag": "es",
                "tool_track_id": 7,
            }
        ],
    }

    plan = await mutation.build_plan(
        None,
        resolved,
        inventory,
        operation="track_remove",
        params={"track_ids": ["sub-es"], "audio_stream_indices": [2]},
    )

    assert plan["after"]["coverage"]["audio_languages"] == ["en"]
    assert plan["after"]["tracks"] == []


@pytest.mark.asyncio
async def test_subtitle_remove_plan_keeps_legacy_audio_delete_compatibility(tmp_path):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"fake")
    resolved = ResolvedMediaFile(
        media_file_id=1,
        source="radarr",
        path=media,
        size_bytes=media.stat().st_size,
        mtime_ns=media.stat().st_mtime_ns,
        st_nlink=1,
        signature="sig",
        container="mkv",
        movie_id=1,
    )
    inventory = {
        "container_family": "mkv",
        "capabilities": capabilities.capabilities_for("mkv"),
        "coverage": {
            "audio_languages": ["en", "fr"],
        },
        "audio_streams": [
            {"index": 1, "language_tag": "en", "tool_track_id": 1},
            {"index": 2, "language_tag": "fr", "tool_track_id": 2},
        ],
        "tracks": [],
    }

    plan = await mutation.build_plan(
        None,
        resolved,
        inventory,
        operation="subtitle_remove",
        params={"track_ids": [], "audio_stream_indices": [2]},
    )

    assert plan["after"]["coverage"]["audio_languages"] == ["en"]


@pytest.mark.asyncio
async def test_metadata_plan_updates_subtitle_and_audio_flags(tmp_path):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"fake")
    resolved = ResolvedMediaFile(
        media_file_id=1,
        source="radarr",
        path=media,
        size_bytes=media.stat().st_size,
        mtime_ns=media.stat().st_mtime_ns,
        st_nlink=1,
        signature="sig",
        container="mkv",
        movie_id=1,
    )
    inventory = {
        "container_family": "mkv",
        "capabilities": capabilities.capabilities_for("mkv"),
        "coverage": {},
        "audio_streams": [
            {
                "index": 1,
                "language_tag": "en",
                "tool_track_id": 1,
                "is_default": False,
                "is_commentary": False,
            }
        ],
        "tracks": [
            {
                "id": "sub-en",
                "source": "embedded",
                "language_tag": "en",
                "tool_track_id": 2,
                "is_default": False,
                "is_forced": False,
                "is_sdh": False,
                "is_commentary": False,
            }
        ],
    }

    plan = await mutation.build_plan(
        None,
        resolved,
        inventory,
        operation="subtitle_metadata",
        params={
            "edits": [
                {"track_id": "sub-en", "is_default": True, "is_sdh": True},
                {
                    "stream_type": "audio",
                    "audio_stream_index": 1,
                    "is_default": True,
                    "is_commentary": True,
                },
            ]
        },
    )

    assert plan["capabilities"]["can_execute"] is True
    assert plan["after"]["tracks"][0]["is_default"] is True
    assert plan["after"]["tracks"][0]["is_sdh"] is True
    assert plan["after"]["audio_streams"][0]["is_default"] is True
    assert plan["after"]["audio_streams"][0]["is_commentary"] is True


@pytest.mark.asyncio
async def test_audio_reorder_plan_reorders_preview(tmp_path):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"fake")
    resolved = ResolvedMediaFile(
        media_file_id=1,
        source="radarr",
        path=media,
        size_bytes=media.stat().st_size,
        mtime_ns=media.stat().st_mtime_ns,
        st_nlink=1,
        signature="sig",
        container="mkv",
        movie_id=1,
    )
    inventory = {
        "container_family": "mkv",
        "capabilities": capabilities.capabilities_for("mkv"),
        "coverage": {},
        "audio_streams": [
            {"index": 1, "language_tag": "en", "tool_track_id": 1},
            {"index": 2, "language_tag": "fr", "tool_track_id": 2},
        ],
        "tracks": [],
    }

    plan = await mutation.build_plan(
        None,
        resolved,
        inventory,
        operation="audio_reorder",
        params={"audio_stream_order": [2, 1]},
    )

    assert [stream["index"] for stream in plan["after"]["audio_streams"]] == [2, 1]
    assert plan["capabilities"]["can_execute"] is True


def test_is_network_filesystem(monkeypatch):
    from collections import namedtuple  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    import psutil  # noqa: PLC0415

    Partition = namedtuple("Partition", ["device", "mountpoint", "fstype", "opts"])

    def mock_partitions(all=False):
        return [
            Partition("/dev/sda2", "/boot", "xfs", "rw"),
            Partition("192.168.4.200:/Marquee", "/mnt/ARK", "nfs4", "rw"),
            Partition("192.168.4.200:/PLUNDER", "/mnt/PLUNDER", "nfs", "rw"),
            Partition("/dev/mapper/fedora-root", "/", "ext4", "rw"),
        ]

    monkeypatch.setattr(psutil, "disk_partitions", mock_partitions)

    assert mutation.is_network_filesystem(Path("/mnt/ARK/Movies/F1.mkv")) is True
    assert mutation.is_network_filesystem(Path("/mnt/PLUNDER/Movies/Arrival.mkv")) is True
    assert mutation.is_network_filesystem(Path("/boot/grub/grub.cfg")) is False
    assert mutation.is_network_filesystem(Path("/etc/resolv.conf")) is False


def test_nice_ionice_prefix_with_network_bypass(monkeypatch):
    import shutil  # noqa: PLC0415
    from collections import namedtuple  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    import psutil  # noqa: PLC0415

    Partition = namedtuple("Partition", ["device", "mountpoint", "fstype", "opts"])

    def mock_partitions(all=False):
        return [
            Partition("192.168.4.200:/Marquee", "/mnt/ARK", "nfs4", "rw"),
            Partition("/dev/mapper/fedora-root", "/", "ext4", "rw"),
        ]

    monkeypatch.setattr(psutil, "disk_partitions", mock_partitions)
    monkeypatch.setattr(shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    # For local paths, we get both nice and ionice
    prefix_local = mutation._nice_ionice_prefix(Path("/home/quartermaster/local.mkv"))
    assert "/usr/bin/nice" in prefix_local
    assert "/usr/bin/ionice" in prefix_local

    # For network paths, we get nice but ionice is bypassed
    prefix_network = mutation._nice_ionice_prefix(Path("/mnt/ARK/Movies/network.mkv"))
    assert "/usr/bin/nice" in prefix_network
    assert "/usr/bin/ionice" not in prefix_network


def test_temp_output_path_ssd_toggle(monkeypatch, tmp_path):
    from pathlib import Path  # noqa: PLC0415

    from marquee.core.subtitles.config import subtitle_settings  # noqa: PLC0415

    # Default (off)
    monkeypatch.setattr(subtitle_settings, "SUBTITLE_MUTATION_USE_TEMP_DIR", False)
    src = Path("/movies/Movie.mkv")
    out = mutation._temp_output_path(src, "job123")
    assert out.parent == src.parent
    assert out.name.startswith(".Movie.mkv.marquee.job123.partial")

    # Enabled (on) without custom dir -> falls back to tempfile.gettempdir()
    monkeypatch.setattr(subtitle_settings, "SUBTITLE_MUTATION_USE_TEMP_DIR", True)
    monkeypatch.setattr(subtitle_settings, "SUBTITLE_MUTATION_TEMP_DIR", None)
    import tempfile  # noqa: PLC0415

    expected_temp = Path(tempfile.gettempdir())
    out_toggle = mutation._temp_output_path(src, "job123")
    assert out_toggle.parent == expected_temp
    assert out_toggle.name == "Movie.mkv.marquee.job123.partial.mkv"

    # Enabled (on) with custom dir
    custom_temp_dir = tmp_path / "ssd/temp"
    monkeypatch.setattr(subtitle_settings, "SUBTITLE_MUTATION_TEMP_DIR", str(custom_temp_dir))
    out_custom = mutation._temp_output_path(src, "job123")
    assert out_custom.parent == custom_temp_dir
    assert out_custom.name == "Movie.mkv.marquee.job123.partial.mkv"
