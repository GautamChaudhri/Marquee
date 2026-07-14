"""JMC2C Phase C1: presenter engine and primary-family presenter coverage."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.presenters import (
    GENERIC_PRESENTER,
    JOB_PRESENTER_REGISTRY,
    PresentationIntegrityError,
    UnregisteredPresenterError,
    load_context,
    present_job,
    present_job_row,
    resolve_presenter,
)
from marquee.core.jobs.presenters.audio_subs import AUDIO_SUBS_JOB_TYPES
from marquee.core.jobs.presenters.hdr import HDR_JOB_TYPES
from marquee.core.jobs.presenters.letterbox import LETTERBOX_JOB_TYPES
from marquee.core.jobs.presenters.posters import POSTER_JOB_TYPES
from marquee.models import Job

FIXTURES = Path(__file__).parent / "fixtures" / "jmc2c"

T0 = datetime(2026, 7, 13, 10, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 13, 10, 5, 0, tzinfo=UTC)
T2 = datetime(2026, 7, 13, 10, 20, 0, tzinfo=UTC)

MOVIE_SNAPSHOT = {
    "version": 1,
    "kind": "movie",
    "display_id": "movie:11",
    "display_name": "Blade Runner",
    "snapshot_at": "2026-07-13T09:59:00+00:00",
    "movie_id": 11,
    "title": "Blade Runner",
    "year": 1982,
    "artwork_key": "movie:11",
    "media_kind": "movie",
}

EPISODE_SNAPSHOT = {
    "version": 1,
    "kind": "episode",
    "display_id": "episode:77",
    "display_name": "The Expanse · S03E07 — Delta-V",
    "snapshot_at": "2026-07-13T09:59:00+00:00",
    "episode_id": 77,
    "series_id": 5,
    "series_title": "The Expanse",
    "season_number": 3,
    "episode_number": 7,
    "episode_code": "S03E07",
    "episode_title": "Delta-V",
    "file_name": "The.Expanse.S03E07.mkv",
    "media_file_id": 314,
}

MEDIA_FILE_SNAPSHOT = {
    "version": 1,
    "kind": "media_file",
    "display_id": "media_file:314",
    "display_name": "The.Expanse.S03E07.mkv",
    "snapshot_at": "2026-07-13T09:59:00+00:00",
    "media_file_id": 314,
    "file_name": "The.Expanse.S03E07.mkv",
    "media_kind": "episode",
    "source": "sonarr",
    "source_key": "314",
    "series_id": 5,
    "series_title": "The Expanse",
    "season_number": 3,
    "episode_number": 7,
    "episode_title": "Delta-V",
    "container": "mkv",
}


def make_job(**overrides) -> Job:
    values = {
        "id": "job0000000000000000000000000001",
        "type": "poster_pipeline",
        "payload_version": 1,
        "result_version": 1,
        "error_version": 1,
        "request": {},
        "plan": None,
        "result": None,
        "error": None,
        "phase": "terminal",
        "outcome": "succeeded",
        "desired_state": "run",
        "fence_token": 1,
        "current_attempt_id": None,
        "priority": 50,
        "eligible_at": T0,
        "pgq_job_id": None,
        "dispatch_generation": 1,
        "retry_policy": None,
        "execution_policy_id": None,
        "configuration_version": None,
        "configuration_snapshot": None,
        "parent_id": None,
        "root_id": "job0000000000000000000000000001",
        "correlation_id": None,
        "retry_of_job_id": None,
        "trigger_kind": "manual",
        "initiator": {"kind": "api", "label": "Library page"},
        "feature_area": "ai_posters",
        "presentation_family": "ai_posters",
        "subject_kind": "movie",
        "subject_reference": "11",
        "subject_snapshot": MOVIE_SNAPSHOT,
        "progress": None,
        "progress_sequence": 0,
        "progress_updated_at": None,
        "current_stage": None,
        "current_subject": None,
        "attention": None,
        "created_at": T0,
        "planned_at": None,
        "queued_at": T0,
        "started_at": T1,
        "stopping_at": None,
        "terminal_at": T2,
    }
    values.update(overrides)
    job = Job()
    for key, value in values.items():
        setattr(job, key, value)
    return job


def definition_for(job_type: str):
    return JOB_DEFINITION_REGISTRY.get(job_type)


def assert_matches_golden(name: str, payload: dict) -> None:
    path = FIXTURES / f"{name}.json"
    assert path.exists(), f"golden fixture {path} is missing"
    expected = json.loads(path.read_text())
    assert payload == expected


# --- registry ---------------------------------------------------------------


def test_primary_family_presenters_are_registered():
    for job_type in (
        POSTER_JOB_TYPES + HDR_JOB_TYPES + AUDIO_SUBS_JOB_TYPES + LETTERBOX_JOB_TYPES
    ):
        definition = definition_for(job_type)
        presenter = resolve_presenter(definition)
        assert presenter.key == definition.presenter_key
        assert not presenter.generic


def test_generic_presenter_is_not_registered_for_any_builtin():
    assert GENERIC_PRESENTER not in JOB_PRESENTER_REGISTRY.values()
    assert GENERIC_PRESENTER.generic


def test_unregistered_presenter_key_raises():
    from dataclasses import replace

    definition = replace(
        definition_for("poster_pipeline"), presenter_key="jobs.not_a_real_type"
    )
    with pytest.raises(UnregisteredPresenterError):
        resolve_presenter(definition)


# --- poster family ----------------------------------------------------------


def test_poster_pipeline_detail_golden():
    job = make_job(
        request={"movie_id": 11, "title": "Blade Runner"},
        result={
            "outcome": "succeeded",
            "message": "Selected candidate 3 of 47.",
            "summary": {
                "candidate_count": 47,
                "source_count": 3,
                "rejected_count": 31,
                "ranked_count": 16,
                "selected_source": "TMDB",
                "score": 0.87,
                "confidence": 0.92,
                "model_version": "clip-vit-b-32",
                "profile_version": "taste-v3",
                "deployed": True,
                "previous_poster": "poster_v1.jpg",
                "selected_poster": "poster_v2.jpg",
            },
        },
    )
    definition = definition_for("poster_pipeline")
    presentation = present_job(job, definition)
    assert presentation.presenter_key == "jobs.poster_pipeline"
    assert presentation.action.headline == "Select a poster from 47 candidates"
    assert presentation.warnings == ()
    assert_matches_golden(
        "poster_pipeline_detail", presentation.model_dump(mode="json")
    )


def test_poster_pipeline_no_candidate_is_distinct_no_change():
    job = make_job(
        outcome="no_change",
        result={
            "outcome": "no_change",
            "message": None,
            "summary": {"candidate_count": 12, "selected": False},
        },
    )
    presentation = present_job(job, definition_for("poster_pipeline"))
    notices = [s for s in presentation.sections if s.kind == "notice"]
    assert any("no candidate" in n.message for n in notices)
    assert presentation.status.label == "No change needed"
    assert presentation.status.tone == "positive"


def test_poster_row_compact_golden():
    job = make_job(
        result={
            "outcome": "succeeded",
            "message": None,
            "summary": {"candidate_count": 47, "deployed": True},
        }
    )
    row = present_job_row(job, definition_for("poster_pipeline"))
    assert row.subject.display_name == "Blade Runner"
    assert_matches_golden("poster_pipeline_row", row.model_dump(mode="json"))


# --- HDR family -------------------------------------------------------------


def test_dovi_convert_failure_attributes_stage():
    job = make_job(
        type="dovi_convert",
        feature_area="hdr",
        presentation_family="hdr",
        subject_kind="media_file",
        subject_reference="314",
        subject_snapshot=MEDIA_FILE_SNAPSHOT,
        outcome="failed",
        error={
            "code": "rpu_injection_failed",
            "summary": "The RPU could not be injected into the output stream.",
            "remediation": "Re-run analysis; if it fails again the source RPU is damaged.",
            "diagnostics": {
                "stage": "conversion",
                "media_changed": False,
                "atomicity_held": True,
            },
        },
        result={
            "outcome": "failed",
            "message": None,
            "summary": {
                "source_profile": "P7",
                "target_profile": "P8.1",
                "codec": "hevc",
                "bit_depth": 10,
                "rpu_action": "extract and inject",
                "encoder": "hevc_nvenc",
                "hardware_path": "NVIDIA NVENC",
                "input_bytes": 4200000000,
            },
        },
    )
    presentation = present_job(job, definition_for("dovi_convert"))
    assert presentation.action.headline == "Convert Dolby Vision P7 to P8.1"
    assert len(presentation.failures) == 1
    failure = presentation.failures[0]
    assert failure.stage == "conversion"
    assert failure.media_changed is False
    assert failure.atomicity_held is True
    assert presentation.attention.level == "error"
    assert presentation.attention.reason == "failed"
    assert any(s.kind == "failures" for s in presentation.sections)
    assert any(
        action.label.startswith("Re-run analysis")
        for action in presentation.suggested_actions
    )


def test_dovi_analyze_detail_golden():
    job = make_job(
        type="dovi_analyze",
        feature_area="hdr",
        presentation_family="hdr",
        subject_kind="media_file",
        subject_reference="314",
        subject_snapshot=MEDIA_FILE_SNAPSHOT,
        request={
            "media_file_id": 314,
            "movie_id": 12,
            "source_signature": "a" * 40,
            "analysis_depth": "standard",
        },
        result={
            "outcome": "succeeded",
            "message": "Profile 7 with enhancement layer.",
            "summary": {
                "source_profile": "P7",
                "hdr_format": "Dolby Vision + HDR10",
                "codec": "hevc",
                "bit_depth": 10,
                "color_primaries": "bt2020",
                "color_transfer": "smpte2084",
            },
        },
    )
    presentation = present_job(job, definition_for("dovi_analyze"))
    assert_matches_golden("dovi_analyze_detail", presentation.model_dump(mode="json"))


# --- audio/subtitles family --------------------------------------------------


def test_remux_failure_marks_all_targets_not_applied():
    tracks = [
        {
            "track_kind": "subtitle",
            "language": "eng",
            "codec": "subrip",
            "title": "English (SDH)",
            "is_sdh": True,
            "embedded": True,
            "requested": "remove",
            "outcome": "not_applied",
            "reason": "The remux failed before any change was written.",
        },
        {
            "track_kind": "audio",
            "language": "fra",
            "codec": "ac3",
            "channels": 6,
            "embedded": True,
            "requested": "remove",
            "outcome": "not_applied",
            "reason": "The remux failed before any change was written.",
        },
    ]
    job = make_job(
        type="subtitle_remove",
        feature_area="audio_subtitles",
        presentation_family="audio_subtitles",
        subject_kind="track",
        subject_reference="track:9",
        subject_snapshot={
            "version": 1,
            "kind": "track",
            "display_id": "track:9",
            "display_name": "English (SDH)",
            "snapshot_at": "2026-07-13T09:59:00+00:00",
            "track_kind": "subtitle",
            "media_file_id": 314,
            "language": "eng",
            "codec": "subrip",
            "is_sdh": True,
            "embedded": True,
            "file_name": "The.Expanse.S03E07.mkv",
            "series_title": "The Expanse",
            "season_number": 3,
            "episode_number": 7,
        },
        outcome="failed",
        error={
            "code": "mkvmerge_failed",
            "summary": "mkvmerge exited with an error while writing the new container.",
            "remediation": "Check free disk space, then retry the removal.",
            "diagnostics": {"stage": "remux", "media_changed": False},
        },
        result={
            "outcome": "failed",
            "message": None,
            "summary": {
                "subtitle_targets": 1,
                "audio_targets": 1,
                "atomic": True,
                "audio_before": 3,
                "audio_after": 3,
                "subtitle_before": 4,
                "subtitle_after": 4,
                "tracks": tracks,
            },
        },
    )
    presentation = present_job(job, definition_for("subtitle_remove"))
    assert (
        presentation.action.headline
        == "Remove 1 subtitle track and 1 audio track"
    )
    table = next(s for s in presentation.sections if s.kind == "track_table")
    assert all(track.outcome == "not_applied" for track in table.tracks)
    notice = [s for s in presentation.sections if s.kind == "notice"]
    assert any("all-or-nothing" in n.message for n in notice)
    changes = next(s for s in presentation.sections if s.kind == "change_list")
    assert {item.outcome for item in changes.items} == {"not_applied"}
    before_after = next(s for s in presentation.sections if s.kind == "before_after")
    assert all(row.changed is False for row in before_after.rows)


def test_subtitle_generate_detail_golden():
    job = make_job(
        type="subtitle_generate",
        feature_area="audio_subtitles",
        presentation_family="audio_subtitles",
        subject_kind="media_file",
        subject_reference="314",
        subject_snapshot=MEDIA_FILE_SNAPSHOT,
        result={
            "outcome": "succeeded",
            "message": "Generated English subtitles.",
            "summary": {
                "language": "English",
                "source_track": "audio stream 3",
                "provider": "Embedded Subgen",
                "model": "whisper-turbo",
                "output_name": "The.Expanse.S03E07.en.srt",
                "external_before": 0,
                "external_after": 1,
            },
        },
    )
    presentation = present_job(job, definition_for("subtitle_generate"))
    assert (
        presentation.action.headline
        == "Generate English subtitles from audio stream 3"
    )
    assert_matches_golden(
        "subtitle_generate_detail", presentation.model_dump(mode="json")
    )


# --- letterbox family --------------------------------------------------------


def test_letterbox_detect_episode_running_progress():
    progress = {
        "version": 1,
        "sequence": 9,
        "job_id": "job0000000000000000000000000001",
        "attempt_id": 4,
        "attempt_number": 1,
        "fence_token": 2,
        "updated_at": "2026-07-13T10:10:00+00:00",
        "headline": "Analyzing sample 4 of 12",
        "stage": {"key": "execute", "label_key": "jobs.letterbox_detect_episode.progress.execute"},
        "overall": {
            "scope_id": "episode:77",
            "mode": "determinate",
            "unit": "samples",
            "completed": 4.0,
            "total": 12.0,
            "percent": 33.3333,
        },
        "current": {"scope_id": "sample:4", "mode": "indeterminate"},
    }
    job = make_job(
        type="letterbox_detect_episode",
        feature_area="letterbox",
        presentation_family="letterbox",
        subject_kind="episode",
        subject_reference="77",
        subject_snapshot=EPISODE_SNAPSHOT,
        phase="running",
        outcome=None,
        terminal_at=None,
        request={"thorough": True, "media_file_id": 77, "episode_ids": [77]},
        progress=progress,
        progress_sequence=9,
        progress_updated_at=datetime(2026, 7, 13, 10, 10, 0, tzinfo=UTC),
    )
    definition = definition_for("letterbox_detect_episode")
    presentation = present_job(job, definition)
    assert (
        presentation.action.headline
        == "Detect letterbox bars in this episode using thorough analysis"
    )
    assert presentation.progress is not None
    assert presentation.progress.overall.percent == 33.3333
    assert presentation.progress.current.mode == "indeterminate"
    assert presentation.status.label == "Running"
    row = present_job_row(job, definition)
    assert row.progress.sequence == 9
    assert row.subject.context == ("The Expanse", "Season 3", "S03E07 — Delta-V")


def test_letterbox_reencode_detail_golden():
    job = make_job(
        type="letterbox_reencode",
        feature_area="letterbox",
        presentation_family="letterbox",
        subject_kind="media_file",
        subject_reference="314",
        subject_snapshot=MEDIA_FILE_SNAPSHOT,
        result={
            "outcome": "succeeded",
            "message": "Cropped and re-encoded.",
            "summary": {
                "crop_top": 132,
                "crop_bottom": 132,
                "source_width": 1920,
                "source_height": 1080,
                "output_width": 1920,
                "output_height": 816,
                "encoder": "hevc_nvenc",
                "confidence": 0.98,
                "validated": True,
                "hdr_preserved": True,
                "input_bytes": 3800000000,
                "output_bytes": 3300000000,
            },
        },
    )
    presentation = present_job(job, definition_for("letterbox_reencode"))
    assert (
        presentation.action.headline
        == "Re-encode to crop 132 px from the top and 132 px from the bottom"
    )
    assert presentation.impact is not None
    assert presentation.impact.storage_delta_bytes == -500000000
    assert_matches_golden(
        "letterbox_reencode_detail", presentation.model_dump(mode="json")
    )


def test_letterbox_no_bars_notice():
    job = make_job(
        type="letterbox_detect",
        feature_area="letterbox",
        presentation_family="letterbox",
        outcome="no_change",
        result={
            "outcome": "no_change",
            "message": None,
            "summary": {"no_bars": True, "samples": 12, "confidence": 0.99},
        },
    )
    presentation = present_job(job, definition_for("letterbox_detect"))
    notices = [s for s in presentation.sections if s.kind == "notice"]
    assert any("No letterbox bars" in n.message for n in notices)


# --- robustness ---------------------------------------------------------------


def test_malformed_optional_evidence_warns_instead_of_failing():
    job = make_job(
        result={
            "outcome": "succeeded",
            "message": None,
            "summary": {
                "candidate_count": "forty-seven",
                "score": {"nested": True},
                "selected_source": "",
            },
        },
        progress={"version": 1, "bogus": True},
    )
    presentation = present_job(job, definition_for("poster_pipeline"))
    assert presentation.progress is None
    codes = {w.code for w in presentation.warnings}
    assert codes == {"malformed_evidence"}
    assert len(presentation.warnings) >= 3
    assert not any(s.kind == "facts" for s in presentation.sections)


def test_malformed_result_document_is_a_warning_not_an_error():
    job = make_job(result={"outcome": "succeeded", "unexpected_field": 1})
    presentation = present_job(job, definition_for("poster_pipeline"))
    assert any(
        "result document" in w.message for w in presentation.warnings
    )


def test_invalid_subject_snapshot_is_an_integrity_error():
    job = make_job(subject_snapshot={"kind": "movie"})
    with pytest.raises(PresentationIntegrityError):
        load_context(job, definition_for("poster_pipeline"))


def test_missing_live_subject_renders_snapshot_with_notice():
    job = make_job()
    presentation = present_job(
        job, definition_for("poster_pipeline"), live_subject_missing=True
    )
    assert presentation.subject.missing_live_subject is True
    assert presentation.subject.display_name == "Blade Runner"
    notices = [s for s in presentation.sections if s.kind == "notice"]
    assert any("no longer present" in n.message for n in notices)


def test_presentation_is_deterministic():
    job = make_job(
        result={
            "outcome": "succeeded",
            "message": None,
            "summary": {"candidate_count": 47},
        }
    )
    definition = definition_for("poster_pipeline")
    first = present_job(job, definition).model_dump(mode="json")
    second = present_job(job, definition).model_dump(mode="json")
    assert first == second


def test_no_raw_machine_labels_as_primary_text():
    job = make_job(outcome="dead_letter", error=None)
    presentation = present_job(job, definition_for("poster_pipeline"))
    assert presentation.status.label == "Needs attention"
    assert "dead_letter" not in presentation.status.label
    assert presentation.failures[0].message.startswith("Needs attention")
