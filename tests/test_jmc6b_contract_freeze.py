"""JMC6B B0 freeze for Activity, Operations, and feature-page migration."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
FE = ROOT / "frontend/src"
FIXTURE = json.loads(
    (ROOT / "tests/fixtures/jmc6b/activity_surface_inventory.json").read_text()
)
SCHEMA = json.loads((ROOT / "design/api-schema.json").read_text())


def _existing(paths: list[str]) -> set[str]:
    return {path for path in paths if (FE / path).exists()}


def _files_with(pattern: str) -> set[str]:
    regex = re.compile(pattern)
    found: set[str] = set()
    for path in FE.rglob("*"):
        if path.suffix not in {".ts", ".svelte"} or not path.is_file():
            continue
        if regex.search(path.read_text()):
            found.add(path.relative_to(FE).as_posix())
    return found


def test_jmc6b_base_and_generated_contract_are_frozen() -> None:
    assert FIXTURE["base"] == {
        "tag": "jmc6a-complete",
        "commit": "a0355e07515c8cfee77ed7a2e31fc38d340eb445",
        "tree": "7849db9ab8558d8007d23909c44d6fb6f261e2f7",
        "parent": "c8413aacc2552301e963084bdcad953b7dc1b78a",
    }
    assert SCHEMA["openapi"] == FIXTURE["openapi"]["version"]
    retired = {
        "/api/onboarding/taste-test/movies",
        "/api/onboarding/taste-test/posters/{file}",
        "/api/onboarding/taste-test/rank",
    }
    assert retired.isdisjoint(SCHEMA["paths"])
    assert len(SCHEMA["paths"]) + len(retired) >= FIXTURE["openapi"]["path_count"]


def test_jmc6b_bounded_job_resources_are_present() -> None:
    required = {
        "/api/jobs": {"get"},
        "/api/jobs/attention": {"get"},
        "/api/jobs/events/stream": {"get"},
        "/api/jobs/{job_id}/snapshot": {"get"},
        "/api/jobs/{job_id}/presentation": {"get"},
        "/api/jobs/{job_id}/attempts": {"get"},
        "/api/jobs/{job_id}/events": {"get"},
        "/api/jobs/{job_id}/children": {"get"},
        "/api/jobs/{job_id}/artifacts": {"get"},
        "/api/jobs/{job_id}/attempts/{attempt_id}/logs": {"get"},
        "/api/jobs/{job_id}/attempts/{attempt_id}/logs/stream": {"get"},
        "/api/jobs/{job_id}/attempts/{attempt_id}/logs/download": {"get"},
        "/api/jobs/{job_id}/raw/{kind}": {"get"},
        "/api/jobs/{job_id}/cancel": {"post"},
        "/api/jobs/{job_id}/pause": {"post"},
        "/api/jobs/{job_id}/resume": {"post"},
        "/api/jobs/{job_id}/priority": {FIXTURE["openapi"]["priority_method_target"]},
        "/api/jobs/{job_id}/retry": {"post"},
        "/api/jobs/actions": {"post"},
    }
    for path, methods in required.items():
        assert path in SCHEMA["paths"]
        assert methods <= set(SCHEMA["paths"][path])


def test_jmc6b_operations_contract_is_typed_and_bounded() -> None:
    paths = SCHEMA["paths"]
    for path in FIXTURE["openapi"]["operations_existing_paths"]:
        assert path in paths
        response = paths[path]["get"]["responses"]["200"]["content"]["application/json"]
        if path != FIXTURE["openapi"]["operations_history"]:
            assert response["schema"] == {}

    operations = paths[FIXTURE["openapi"]["operations_target"]]["get"]
    assert operations["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/OperationsSnapshot"
    }
    history = paths[FIXTURE["openapi"]["operations_history"]]["get"]
    assert history["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/OperationsHistoryResponse"
    }


def test_jmc6b_removal_and_migration_inventories_can_only_shrink() -> None:
    for key in (
        "duplicate_activity_ui",
        "projection_room_legacy_components",
        "job_authority_local_storage",
        "broad_file_lint_suppressions",
    ):
        frozen = set(FIXTURE[key])
        assert _existing(FIXTURE[key]) <= frozen

    local_authority = _files_with(r"localStorage\.(?:getItem|setItem).*?(?:job|batch)|ACTIVE_KEY")
    assert local_authority <= set(FIXTURE["job_authority_local_storage"])

    broad_suppressions = _files_with(r"(?m)^<!-- eslint-disable ")
    assert broad_suppressions <= set(FIXTURE["broad_file_lint_suppressions"])


def test_jmc6b_fixture_subject_contract_is_complete() -> None:
    fixture_source = (FE / "lib/activity/components/fixtures.ts").read_text()
    for kind in FIXTURE["fixture_subject_kinds"]:
        assert f"subject('{kind}'" in fixture_source


def test_jmc6b_job_detail_uses_only_lazy_canonical_diagnostics() -> None:
    detail = FE / "routes/projection-room/jobs/[job_id]/+page.svelte"
    loader = FE / "routes/projection-room/jobs/[job_id]/+page.ts"
    source = detail.read_text()
    loader_source = loader.read_text()

    assert "getPresentation" in loader_source
    assert "getJobDetail" not in loader_source
    assert "trackJob" not in source
    assert "$lib/jobs" not in source
    assert "$lib/api/jobs" not in source
    for panel in (
        "PresentationSections",
        "TimelinePanel",
        "LogsPanel",
        "ArtifactsPanel",
        "RawDataPanel",
        "ExecutionPanel",
    ):
        assert panel in source

    diagnostic_sources = "\n".join(
        path.read_text() for path in (FE / "lib/activity/components/detail").glob("*.svelte")
    )
    assert "AbortController" in diagnostic_sources
    assert "content-visibility: auto" in diagnostic_sources
    assert "getJobDetail" not in diagnostic_sources
    assert "trackJob" not in diagnostic_sources


def test_jmc6b_operations_is_lazy_abortable_and_uses_only_typed_resources() -> None:
    shell = (FE / "routes/projection-room/+page.svelte").read_text()
    operations = (FE / "lib/activity/components/OperationsView.svelte").read_text()

    assert "import('$lib/activity/components/OperationsView.svelte')" in shell
    assert "getOperations" in operations
    assert "getOperationsHistory" in operations
    assert "AbortController" in operations
    assert "onDestroy" in operations
    assert "content-visibility: auto" in operations
    assert "resolution: 120" in operations
    for legacy_fanout in ("getStatus", "getMetrics", "getWorkerStatus", "setInterval"):
        assert legacy_fanout not in operations


def test_jmc6b_b5_feature_pages_use_one_shared_activity_authority() -> None:
    panel = (FE / "lib/activity/components/FeatureActivityPanel.svelte").read_text()
    for token in (
        "getJobProgressStore",
        "store.acquireScope",
        "store.track",
        "JobProgressCard",
        "onCancel={requestCancel}",
        'href="/projection-room"',
    ):
        assert token in panel
    for forbidden in ("localStorage", "$lib/jobs", "$lib/api/jobs", "EventSource"):
        assert forbidden not in panel

    consumers = (
        "routes/pipeline/+page.svelte",
        "routes/pipeline/movies/+page.svelte",
        "routes/pipeline/tv/+page.svelte",
        "lib/components/pipeline/RunResultsView.svelte",
        "routes/taste/+page.svelte",
        "routes/onboarding/+page.svelte",
        "routes/hdr/+page.svelte",
        "routes/hdr/movies/+page.svelte",
        "routes/hdr/movies/[id]/+page.svelte",
        "routes/hdr/tv/[id]/+page.svelte",
    )
    combined = ""
    for consumer in consumers:
        source = (FE / consumer).read_text()
        combined += source
        assert "FeatureActivityPanel" in source
        for forbidden in (
            "$lib/jobs",
            "trackJob",
                "RunProgress",
                "eventsUrl",
                "pollMs",
            ):
            assert forbidden not in source, consumer

    assert "jobIds={initiatedJobIds}" in combined
    assert "snapshot.status.outcome" in combined


def test_jmc6b_b5_hdr_feature_pages_do_not_decode_job_documents() -> None:
    page = (FE / "routes/hdr/movies/[id]/+page.svelte").read_text()
    api = (ROOT / "marquee/api/routes/hdr.py").read_text()
    types = (FE / "lib/api/types.ts").read_text()

    assert "conversion_job" not in page
    assert "analysis_job" not in page
    assert "conversion_candidate" in page
    assert "DoviConvertResultV1.model_validate(job.result)" in api
    assert '"conversion_candidate": _dovi_conversion_candidate(conversion_job)' in api
    assert "analysis_jobs:" not in types


def test_jmc6b_b6_all_initiating_pages_use_the_shared_activity_card() -> None:
    consumers = (
        "routes/settings/+page.svelte",
        "routes/audio-subs/+page.svelte",
        "routes/audio-subs/movies/+page.svelte",
        "routes/audio-subs/movies/[id]/+page.svelte",
        "routes/audio-subs/policies/+page.svelte",
        "routes/audio-subs/tv/[id]/+page.svelte",
        "routes/letterbox/+page.svelte",
        "routes/letterbox/movies/+page.svelte",
        "routes/letterbox/tv/+page.svelte",
        "routes/letterbox/tv/[id]/+page.svelte",
        "lib/components/LetterboxDetail.svelte",
    )
    for consumer in consumers:
        source = (FE / consumer).read_text()
        assert "FeatureActivityPanel" in source, consumer
        for forbidden in (
            "$lib/jobs",
            "$lib/api/jobs",
            "$lib/api/media-jobs",
            "trackJob",
            "localStorage",
            "EventSource",
            "/snapshot`",
        ):
            assert forbidden not in source, consumer


def test_jmc6b_b6_legacy_job_authorities_and_feature_aggregates_are_absent() -> None:
    removed = (
        "lib/jobs.ts",
        "lib/api/jobs.ts",
        "lib/api/media-jobs.ts",
        "lib/components/RunProgress.svelte",
        "lib/components/RunningJobCard.svelte",
        "lib/components/WorkerHealthPanel.svelte",
        "lib/components/HistoryTable.svelte",
        "lib/components/QueuedJobRow.svelte",
        "lib/components/ResourcePoolPanel.svelte",
        "lib/components/subtitles/JobList.svelte",
        "lib/components/subtitles/SubgenPanel.svelte",
        "lib/components/subtitles/TrackTable.svelte",
        "lib/components/subtitles/SubtitleMovieDetail.svelte",
    )
    assert not _existing(list(removed))
    assert not _files_with(r"\$lib/(?:api/)?(?:media-)?jobs(?:\.ts)?['\"]")
    assert not _files_with(r"(?m)^<!-- eslint-disable ")

    letterbox_api = (ROOT / "marquee/api/routes/letterbox.py").read_text()
    letterbox_types = (FE / "lib/api/types.ts").read_text()
    assert 'detail["detection_job"]' not in letterbox_api
    assert '"active_job_ids"' not in letterbox_api
    assert "detection_job?:" not in letterbox_types
    assert "active_job_ids: string[]" not in letterbox_types
