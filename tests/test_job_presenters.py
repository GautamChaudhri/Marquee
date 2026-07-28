"""Presenter engine and the primary job families it renders.

Every job family resolves to a registered presenter, and the payload each one produces is
compared against a committed golden fixture so a presentation change cannot land
silently."""

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
from marquee.core.jobs.presenters.posters import POSTER_JOB_TYPES
from marquee.models import Job

FIXTURES = Path(__file__).parent / "fixtures" / "job_presenters"

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
    for job_type in POSTER_JOB_TYPES:
        definition = definition_for(job_type)
        presenter = resolve_presenter(definition)
        assert presenter.key == definition.presenter_key
        assert not presenter.generic


def test_generic_presenter_is_not_registered_for_any_builtin():
    assert GENERIC_PRESENTER not in JOB_PRESENTER_REGISTRY.values()
    assert GENERIC_PRESENTER.generic


def test_unregistered_presenter_key_raises():
    from dataclasses import replace

    definition = replace(definition_for("poster_pipeline"), presenter_key="jobs.not_a_real_type")
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
    assert_matches_golden("poster_pipeline_detail", presentation.model_dump(mode="json"))


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
    assert any("result document" in w.message for w in presentation.warnings)


def test_invalid_subject_snapshot_is_an_integrity_error():
    job = make_job(subject_snapshot={"kind": "movie"})
    with pytest.raises(PresentationIntegrityError):
        load_context(job, definition_for("poster_pipeline"))


def test_missing_live_subject_renders_snapshot_with_notice():
    job = make_job()
    presentation = present_job(job, definition_for("poster_pipeline"), live_subject_missing=True)
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
