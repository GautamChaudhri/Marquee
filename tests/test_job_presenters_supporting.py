"""JMC2C Phase C2: supporting/parent presenters and complete coverage."""

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

FIXTURES = Path(__file__).parent / "fixtures" / "jmc2c"

BATCH_SNAPSHOT = {
    "version": 1,
    "kind": "aggregate_batch",
    "display_id": "batch:dovi",
    "display_name": "Dolby Vision analysis batch",
    "snapshot_at": "2026-07-13T09:59:00+00:00",
    "batch_type": "dovi_analyze_batch",
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
    assert len(JOB_DEFINITION_REGISTRY) == 62  # +JMC6E dedicated taste enrichment
    for definition in JOB_DEFINITION_REGISTRY:
        presenter = resolve_presenter(definition)
        assert not presenter.generic, definition.job_type
        assert presenter.key == definition.presenter_key
        assert presenter is not GENERIC_PRESENTER
    assert len(JOB_PRESENTER_REGISTRY) == 62  # +JMC6E dedicated taste enrichment


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
    assert any(
        fact.value.type == "badge" and fact.value.text == "Dry run" for fact in facts.facts
    )
    cards = next(s for s in presentation.sections if s.kind == "metric_cards")
    assert any(card.label == "Planned" and card.value.value == 1200 for card in cards.cards)


def test_parent_batch_children_from_live_counts():
    job = make_job(
        type="dovi_analyze_batch",
        feature_area="hdr",
        presentation_family="hdr",
        subject_kind="aggregate_batch",
        subject_snapshot=BATCH_SNAPSHOT,
        phase="running",
        outcome=None,
        terminal_at=None,
    )
    definition = definition_for("dovi_analyze_batch")
    presentation = present_job(
        job,
        definition,
        live={
            "children": {
                "total": 24,
                "queued": 4,
                "running": 2,
                "succeeded": 15,
                "no_change": 2,
                "failed": 1,
                "cancelled": 0,
                "sealed": True,
            }
        },
    )
    assert presentation.action.headline == "Analyze Dolby Vision across 24 movies"
    children = next(s for s in presentation.sections if s.kind == "children")
    assert children.total == 24
    assert children.failed == 1
    assert children.children_link.href.endswith("/children")
    assert presentation.links.children is not None


def test_parent_batch_children_from_stored_summary_and_partial_success():
    job = make_job(
        type="letterbox_detect_batch",
        feature_area="letterbox",
        presentation_family="letterbox",
        subject_kind="aggregate_batch",
        subject_snapshot={**BATCH_SNAPSHOT, "batch_type": "letterbox_detect_batch"},
        outcome="partially_succeeded",
        result={
            "outcome": "partially_succeeded",
            "message": None,
            "summary": {
                "children": {
                    "total": 10,
                    "succeeded": 7,
                    "no_change": 1,
                    "failed": 2,
                    "sealed": True,
                }
            },
        },
    )
    presentation = present_job(job, definition_for("letterbox_detect_batch"))
    assert presentation.status.label == "Partially succeeded"
    assert presentation.status.tone == "warning"
    assert presentation.attention.level == "warning"
    children = next(s for s in presentation.sections if s.kind == "children")
    assert (children.succeeded, children.no_change, children.failed) == (7, 1, 2)


def test_parent_batch_malformed_children_warns():
    job = make_job(
        type="letterbox_apply_batch",
        feature_area="letterbox",
        presentation_family="letterbox",
        subject_kind="aggregate_batch",
        subject_snapshot={**BATCH_SNAPSHOT, "batch_type": "letterbox_apply_batch"},
        result={
            "outcome": "succeeded",
            "message": None,
            "summary": {"children": {"total": "ten"}},
        },
    )
    presentation = present_job(job, definition_for("letterbox_apply_batch"))
    assert not any(s.kind == "children" for s in presentation.sections)
    assert any(w.code == "malformed_evidence" for w in presentation.warnings)


def test_letterbox_mutation_presents_requested_and_freshly_verified_crop():
    probe = {
        "source_signature": "mtime_ns=1:size=2048",
        "container": "Matroska",
        "video_track_id": 0,
        "width": 1920,
        "height": 1080,
        "crop_present": True,
        "crop_top": 140,
        "crop_bottom": 140,
        "crop_left": 0,
        "crop_right": 0,
    }
    target = {
        "key": "media-file:71",
        "kind": "media_file",
        "label": "Movie media file 71",
        "operation": "apply",
        "selector_facts": {"subject_kind": "movie", "subject_ids": [11]},
    }
    job = make_job(
        type="letterbox_apply",
        feature_area="letterbox",
        presentation_family="letterbox",
        request={
            "media_file_id": 71,
            "subject_kind": "movie",
            "subject_ids": [11],
            "before": {
                "source_signature": "mtime_ns=1:size=2048",
                "status": "candidate",
                "confidence": "high",
                "variable_ar": False,
                "source_width": 1920,
                "source_height": 1080,
                "current_crop_top": None,
                "current_crop_bottom": None,
                "recommended_crop_top": 140,
                "recommended_crop_bottom": 140,
            },
            "crop_top": 140,
            "crop_bottom": 140,
            "source": "api",
        },
        result={
            "outcome": "no_change",
            "reason_code": "already_applied",
            "message": "authoritative probe already matches",
            "requested_targets": [target],
            "target_outcomes": [
                {
                    "target": target,
                    "status": "skipped",
                    "stage": "preflight",
                    "reason_code": "already_applied",
                    "message": "authoritative probe already matches",
                    "bytes_changed": False,
                    "product_state_changed": False,
                }
            ],
            "validation": {
                "source_probe": {},
                "output_probe": {},
                "verdict": "passed",
            },
            "atomicity": {
                "group_id": "letterbox-apply:71",
                "boundary": "single_target",
                "published": False,
                "rollback_available": False,
                "uncertain_state": False,
            },
            "before_probe": probe,
            "actual_probe": probe,
        },
        outcome="no_change",
    )

    presentation = present_job(job, definition_for("letterbox_apply"))

    crop_text = "140 px from the top and 140 px from the bottom"
    assert presentation.action.headline == f"Apply the letterbox crop tag: {crop_text}"
    letterbox = next(section for section in presentation.sections if section.title == "Letterbox")
    verified = next(fact for fact in letterbox.facts if fact.label == "Verified crop metadata")
    assert verified.value.text == crop_text


def test_retry_lineage_is_presented():
    job = make_job(retry_of_job_id="job0000000000000000000000000000")
    presentation = present_job(job, definition_for("poster_pipeline"))
    lineage = [
        s for s in presentation.sections if s.kind == "facts" and s.title == "Lineage"
    ]
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
