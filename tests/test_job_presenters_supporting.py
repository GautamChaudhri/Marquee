"""Supporting and parent presenters, and complete family coverage.

Batch parents, maintenance scopes, and the remaining subject kinds each render through
their own presenter. Together with the primary families this proves no registered job type
can reach the UI without one."""

import json
from pathlib import Path

from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.presenters import (
    GENERIC_PRESENTER,
    JOB_PRESENTER_REGISTRY,
    present_job,
    present_job_row,
    resolve_presenter,
)
from tests.test_job_presenters import definition_for, make_job

FIXTURES = Path(__file__).parent / "fixtures" / "job_presenters"

BATCH_SNAPSHOT = {
    "version": 1,
    "kind": "aggregate_batch",
    "display_id": "batch:posters",
    "display_name": "Dolby Vision analysis batch",
    "snapshot_at": "2026-07-13T09:59:00+00:00",
    "batch_type": "poster_pipeline_batch",
    "child_count": 24,
    "sealed": True,
    "scope_summary": "All movies with Dolby Vision",
}

MAINTENANCE_SNAPSHOT = {
    "version": 1,
    "kind": "maintenance_scope",
    "display_id": "maintenance:retention",
    "display_name": "Job history retention",
    "snapshot_at": "2026-07-13T09:59:00+00:00",
    "scope": "job history",
    "dry_run": True,
}

MODEL_SNAPSHOT = {
    "version": 1,
    "kind": "model_profile_training",
    "display_id": "profile:movies",
    "display_name": "Movie taste profile",
    "snapshot_at": "2026-07-13T09:59:00+00:00",
    "subject_type": "profile",
    "name": "movies",
    "model_name": "clip-vit-b-32",
}

SYSTEM_SNAPSHOT = {
    "version": 1,
    "kind": "system_work",
    "display_id": "system:noop",
    "display_name": "System no-op",
    "snapshot_at": "2026-07-13T09:59:00+00:00",
    "work": "noop",
}


def test_every_builtin_definition_has_a_dedicated_presenter():
    assert len(JOB_DEFINITION_REGISTRY) == 24
    for definition in JOB_DEFINITION_REGISTRY:
        presenter = resolve_presenter(definition)
        assert not presenter.generic, definition.job_type
        assert presenter.key == definition.presenter_key
        assert presenter is not GENERIC_PRESENTER
    assert len(JOB_PRESENTER_REGISTRY) == 24


def test_every_definition_renders_a_minimal_presentation():
    snapshots = {
        "movie": None,  # reuse make_job default
        "aggregate_batch": BATCH_SNAPSHOT,
        "maintenance_scope": MAINTENANCE_SNAPSHOT,
        "model_profile_training": MODEL_SNAPSHOT,
        "system_work": SYSTEM_SNAPSHOT,
    }
    from tests.test_job_presenters import (
        EPISODE_SNAPSHOT,
        MEDIA_FILE_SNAPSHOT,
        MOVIE_SNAPSHOT,
    )

    per_kind = {
        "movie": MOVIE_SNAPSHOT,
        "series": {
            "version": 1,
            "kind": "series",
            "display_id": "series:5",
            "display_name": "The Expanse",
            "snapshot_at": "2026-07-13T09:59:00+00:00",
            "series_id": 5,
            "series_title": "The Expanse",
        },
        "season": {
            "version": 1,
            "kind": "season",
            "display_id": "season:31",
            "display_name": "The Expanse · Season 3",
            "snapshot_at": "2026-07-13T09:59:00+00:00",
            "season_id": 31,
            "series_id": 5,
            "series_title": "The Expanse",
            "season_number": 3,
        },
        "episode": EPISODE_SNAPSHOT,
        "media_file": MEDIA_FILE_SNAPSHOT,
        "track": {
            "version": 1,
            "kind": "track",
            "display_id": "track:9",
            "display_name": "English subtitle",
            "snapshot_at": "2026-07-13T09:59:00+00:00",
            "track_kind": "subtitle",
            "media_file_id": 314,
            "embedded": True,
            "file_name": "The.Expanse.S03E07.mkv",
        },
        "poster_candidate_set": {
            "version": 1,
            "kind": "poster_candidate_set",
            "display_id": "posters:movie:11",
            "display_name": "Blade Runner posters",
            "snapshot_at": "2026-07-13T09:59:00+00:00",
            "media_kind": "movie",
            "subject_id": 11,
            "title": "Blade Runner",
        },
        "poster_subject_group": {
            "version": 1,
            "kind": "poster_subject_group",
            "display_id": "poster-group:movies:test-000",
            "display_name": "Movie poster group 1",
            "snapshot_at": "2026-07-13T09:59:00+00:00",
            "library": "movies",
            "chunk_index": 0,
            "members": [
                {
                    "subject_key": "movie:11",
                    "subject": MOVIE_SNAPSHOT,
                }
            ],
        },
        "aggregate_batch": BATCH_SNAPSHOT,
        "maintenance_scope": MAINTENANCE_SNAPSHOT,
        "model_profile_training": MODEL_SNAPSHOT,
        "system_work": SYSTEM_SNAPSHOT,
    }
    del snapshots
    for definition in JOB_DEFINITION_REGISTRY:
        kind = sorted(definition.subject_kinds)[0]
        job = make_job(
            type=definition.job_type,
            feature_area=definition.feature_area.value,
            presentation_family=definition.presentation_family,
            subject_kind=kind,
            subject_snapshot=per_kind[kind],
            request={} if definition.job_type == "system_noop" else {},
        )
        presentation = present_job(job, definition)
        assert presentation.job_type == definition.job_type
        assert presentation.presenter_key == definition.presenter_key
        assert presentation.status.label == "Succeeded"
        row = present_job_row(job, definition)
        assert row.action_headline


def test_library_sync_detail_golden():
    job = make_job(
        type="library_sync",
        feature_area="library_integrations",
        presentation_family="library_integrations",
        subject_kind="maintenance_scope",
        subject_snapshot={
            "version": 1,
            "kind": "maintenance_scope",
            "display_id": "maintenance:library-sync",
            "display_name": "Library synchronization",
            "snapshot_at": "2026-07-13T09:59:00+00:00",
            "scope": "radarr and sonarr",
            "dry_run": False,
        },
        trigger_kind="schedule",
        initiator=None,
        result={
            "outcome": "succeeded",
            "message": "Synchronized both services.",
            "summary": {
                "services": "Radarr, Sonarr",
                "created": 3,
                "updated": 41,
                "retired": 1,
                "unchanged": 812,
                "errors": 0,
            },
        },
    )
    presentation = present_job(job, definition_for("library_sync"))
    assert presentation.trigger.kind == "schedule"
    assert presentation.trigger.label == "Scheduled"
    expected = json.loads((FIXTURES / "library_sync_detail.json").read_text())
    assert presentation.model_dump(mode="json") == expected


def test_taste_rebuild_presents_model_facts():
    job = make_job(
        type="taste_rebuild",
        feature_area="ml_taste",
        presentation_family="ml_taste",
        subject_kind="model_profile_training",
        subject_snapshot=MODEL_SNAPSHOT,
        result={
            "outcome": "succeeded",
            "family": "taste_profile",
            "version": "taste-v4",
            "checksum": "a" * 64,
            "expected_generation": 3,
            "active_generation": 4,
            "activated": True,
            "artifact_ids": [1],
            "metrics": {"input_count": 430, "seed": 7},
        },
    )
    presentation = present_job(job, definition_for("taste_rebuild"))
    facts = next(s for s in presentation.sections if s.kind == "facts")
    labels = {fact.label for fact in facts.facts}
    assert {"Family", "Version", "Checksum", "Generation", "Inputs"} <= labels


def test_maintenance_dry_run_and_metrics():
    job = make_job(
        type="job_retention_purge",
        feature_area="maintenance",
        presentation_family="maintenance",
        subject_kind="maintenance_scope",
        subject_snapshot=MAINTENANCE_SNAPSHOT,
        result={
            "outcome": "succeeded",
            "operation": "job_retention_purge",
            "message": "Dry-run plan sealed without mutation.",
            "dry_run": True,
            "plan_checksum": "a" * 64,
            "planned_count": 1200,
            "processed_count": 0,
            "deleted_count": 0,
            "counts": {"jobs": 1200},
            "cancelled": False,
        },
    )
    presentation = present_job(job, definition_for("job_retention_purge"))
    facts = next(s for s in presentation.sections if s.kind == "facts")
    assert any(fact.value.type == "badge" and fact.value.text == "Dry run" for fact in facts.facts)
    cards = next(s for s in presentation.sections if s.kind == "metric_cards")
    assert any(card.label == "Planned" and card.value.value == 1200 for card in cards.cards)


def test_parent_batch_malformed_children_warns():
    job = make_job(
        type="poster_pipeline_tv_batch",
        feature_area="ai_posters",
        presentation_family="ai_posters",
        subject_kind="aggregate_batch",
        subject_snapshot={**BATCH_SNAPSHOT, "batch_type": "poster_pipeline_tv_batch"},
        result={
            "outcome": "succeeded",
            "message": None,
            "summary": {"children": {"total": "ten"}},
        },
    )
    presentation = present_job(job, definition_for("poster_pipeline_tv_batch"))
    assert not any(s.kind == "children" for s in presentation.sections)
    assert any(w.code == "malformed_evidence" for w in presentation.warnings)


def test_retry_lineage_is_presented():
    job = make_job(retry_of_job_id="job0000000000000000000000000000")
    presentation = present_job(job, definition_for("poster_pipeline"))
    lineage = [s for s in presentation.sections if s.kind == "facts" and s.title == "Lineage"]
    assert len(lineage) == 1
    link = lineage[0].facts[0].value
    assert link.type == "link"
    assert link.href == "/projection-room/jobs/job0000000000000000000000000000"
    row = present_job_row(job, definition_for("poster_pipeline"))
    assert row.retry_of_job_id == "job0000000000000000000000000000"


def test_system_noop_presents_cleanly():
    job = make_job(
        type="system_noop",
        feature_area="system",
        presentation_family="system",
        subject_kind="system_work",
        subject_snapshot=SYSTEM_SNAPSHOT,
        trigger_kind="system",
        request={"echo": {"ping": 1}},
        result={"outcome": "succeeded", "message": "ok", "summary": {}},
    )
    presentation = present_job(job, definition_for("system_noop"))
    assert presentation.action.headline == "Run a system health check"
    assert presentation.warnings == ()


TV_GROUP_SNAPSHOT = {
    "version": 1,
    "kind": "poster_subject_group",
    "display_id": "poster-group:tv:tv-abc-2",
    "display_name": "TV poster group 3 (3 subjects)",
    "snapshot_at": "2026-07-13T09:59:00+00:00",
    "library": "tv",
    "chunk_index": 2,
    "members": [
        {
            "subject_key": "series:71",
            "subject": {
                "version": 1,
                "kind": "series",
                "display_id": "series:71",
                "display_name": "Avatar: The Last Airbender",
                "snapshot_at": "2026-07-13T09:59:00+00:00",
                "series_id": 71,
                "series_title": "Avatar: The Last Airbender",
            },
        },
        {
            "subject_key": "season:382",
            "subject": {
                "version": 1,
                "kind": "season",
                "display_id": "season:382",
                "display_name": "Avatar: The Last Airbender · Season 1",
                "snapshot_at": "2026-07-13T09:59:00+00:00",
                "season_id": 382,
                "series_id": 71,
                "series_title": "Avatar: The Last Airbender",
                "season_number": 1,
            },
        },
        {
            "subject_key": "season:383",
            "subject": {
                "version": 1,
                "kind": "season",
                "display_id": "season:383",
                "display_name": "Avatar: The Last Airbender · Season 2",
                "snapshot_at": "2026-07-13T09:59:00+00:00",
                "season_id": 383,
                "series_id": 71,
                "series_title": "Avatar: The Last Airbender",
                "season_number": 2,
            },
        },
    ],
}


MOVIE_GROUP_SNAPSHOT = {
    "version": 1,
    "kind": "poster_subject_group",
    "display_id": "poster-group:movies:movies-abc-0",
    "display_name": "Movie poster group 1 (1 subject)",
    "snapshot_at": "2026-07-13T09:59:00+00:00",
    "library": "movies",
    "chunk_index": 0,
    "members": [
        {
            "subject_key": "movie:12",
            "subject": {
                "version": 1,
                "kind": "movie",
                "display_id": "movie:12",
                "display_name": "Arrival",
                "snapshot_at": "2026-07-13T09:59:00+00:00",
                "movie_id": 12,
                "title": "Arrival",
                "year": 2016,
            },
        },
    ],
}


def _group_job(**overrides):
    values = {
        "type": "poster_pipeline_group",
        "subject_kind": "poster_subject_group",
        "subject_reference": "tv-abc-2",
        "subject_snapshot": TV_GROUP_SNAPSHOT,
        "request": {
            "library": "tv",
            "chunk_index": 2,
            "members": [
                {"series_id": 71, "title": "Avatar: The Last Airbender"},
                {"season_id": 382, "title": "Avatar: The Last Airbender · Season 1"},
                {"season_id": 383, "title": "Avatar: The Last Airbender · Season 2"},
            ],
        },
    }
    values.update(overrides)
    return make_job(**values)


def _group_presentation(job):
    return present_job(job, definition_for("poster_pipeline_group"))


def test_systemic_group_failure_uses_lazy_work_item_summary():
    presentation = _group_presentation(
        _group_job(
            outcome="failed",
            result=None,
            error={
                "code": "runtime_error",
                "summary": "poster group runner did not succeed",
                "remediation": None,
                "diagnostics": {},
            },
        )
    )

    assert presentation.subject.display_name == "TV poster chunk 3"
    assert presentation.work_items is not None
    assert presentation.work_items.total == 3
    assert presentation.work_items.counts.failed == 3
    assert not any(section.kind == "change_list" for section in presentation.sections)


def test_group_subject_carries_a_library_monogram():
    """The artwork tile cannot initial "TV poster chunk 3" into anything useful."""
    tv = _group_presentation(_group_job())
    assert tv.subject.display_name == "TV poster chunk 3"
    assert tv.subject.monogram == "TVP"

    movies = _group_presentation(
        _group_job(
            subject_reference="movies-abc-0",
            subject_snapshot=MOVIE_GROUP_SNAPSHOT,
            request={
                "library": "movies",
                "chunk_index": 0,
                "members": [{"movie_id": 12, "title": "Arrival"}],
            },
        )
    )
    assert movies.subject.display_name == "Movie poster chunk 1"
    assert movies.subject.monogram == "FP"


def test_ordinary_subjects_have_no_monogram():
    """Every other subject keeps the client's first-character fallback."""
    presentation = present_job(
        make_job(
            type="system_noop",
            subject_kind="system_work",
            subject_reference="noop",
            subject_snapshot=SYSTEM_SNAPSHOT,
            request={"echo": {"ping": 1}},
            result={"outcome": "succeeded", "message": "ok", "summary": {}},
        ),
        definition_for("system_noop"),
    )
    assert presentation.subject.monogram is None


def test_partial_group_uses_exact_failure_summary():
    presentation = _group_presentation(
        _group_job(
            outcome="partially_succeeded",
            result={
                "outcome": "review_required",
                "library": "tv",
                "chunk_index": 2,
                "member_count": 3,
                "succeeded_count": 2,
                "no_change_count": 0,
                "review_required_count": 0,
                "failed_count": 1,
                "projected_count": 3,
                "run_ids": ["a" * 32, "b" * 32, "c" * 32],
                "failed_subject_keys": ["season:383"],
                "message": "Grouped poster analysis completed with members requiring review.",
            },
        )
    )

    assert presentation.status.label == "Partially failed"
    assert presentation.attention.message == "Partially failed: 1 of 3 posters needs attention."
    assert presentation.work_items is not None
    assert presentation.work_items.counts.failed == 1
    assert presentation.work_items.counts.succeeded == 2
    assert not any(section.kind == "change_list" for section in presentation.sections)


def test_review_only_group_reads_ready_for_review_instead_of_failed():
    presentation = _group_presentation(
        _group_job(
            outcome="partially_succeeded",
            result={
                "outcome": "review_required",
                "library": "tv",
                "chunk_index": 2,
                "member_count": 3,
                "succeeded_count": 0,
                "no_change_count": 0,
                "review_required_count": 3,
                "failed_count": 0,
                "projected_count": 3,
                "run_ids": ["a" * 32, "b" * 32, "c" * 32],
                "failed_subject_keys": [],
                "message": "3 posters are ready for review.",
            },
        )
    )

    assert presentation.status.label == "Ready for review"
    assert presentation.attention.reason.value == "review"
    assert presentation.attention.message == "3 posters are ready for review."


def test_all_member_group_failure_reads_failed_with_singular_grammar():
    snapshot = {
        **TV_GROUP_SNAPSHOT,
        "members": [TV_GROUP_SNAPSHOT["members"][0]],
    }
    presentation = _group_presentation(
        _group_job(
            subject_snapshot=snapshot,
            outcome="partially_succeeded",
            result={
                "outcome": "review_required",
                "library": "tv",
                "chunk_index": 2,
                "member_count": 1,
                "succeeded_count": 0,
                "no_change_count": 0,
                "review_required_count": 0,
                "failed_count": 1,
                "projected_count": 1,
                "run_ids": ["a" * 32],
                "failed_subject_keys": ["series:71"],
                "message": "Failed: all 1 poster needs attention.",
            },
        )
    )

    assert presentation.status.label == "Failed"
    assert presentation.status.tone == "negative"
    assert presentation.attention.message == "Failed: all 1 poster needs attention."
