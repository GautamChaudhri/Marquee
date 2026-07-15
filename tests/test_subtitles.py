"""Subtitle management pure tests — no real media or async database required."""

from __future__ import annotations

from marquee.core.subtitles import capabilities, coverage, external, languages
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
