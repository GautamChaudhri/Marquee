from __future__ import annotations

import inspect
import json
from pathlib import Path

from marquee.api.job_submission import JobSubmissionResponse
from marquee.api.routes.jobs import list_jobs
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads(
    (ROOT / "tests/fixtures/jmc6e/route_page_manifest.json").read_text(encoding="utf-8")
)


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_canonical_seam_routes_never_execute_long_work_inline() -> None:
    route_sources = {
        "marquee/api/routes/subtitles.py": ("scan_media_file(",),
        "marquee/api/routes/taste.py": (
            "multiprocessing.get_context(",
            "load_map(recompute",
            "await asyncio.to_thread(enrich)",
            "await asyncio.to_thread(build_map)",
        ),
        "marquee/api/routes/feedback.py": ("await asyncio.to_thread(_maybe_retrain_head",),
    }
    violations = [
        f"{path}:{needle}"
        for path, needles in route_sources.items()
        for needle in needles
        if needle in _source(path)
    ]
    assert violations == []


def test_enabled_definitions_own_an_overlap_policy() -> None:
    missing = [
        definition.job_type
        for definition in JOB_DEFINITION_REGISTRY
        if definition.enabled and getattr(definition, "overlap_policy", None) is None
    ]
    assert missing == []


def test_exact_recovery_filters_are_public_contract() -> None:
    parameters = inspect.signature(list_jobs).parameters
    assert "subject_reference" in parameters
    assert "types" in parameters
    assert "root_id" in parameters
    assert "correlation_id" in parameters


def test_submission_contract_exposes_disposition_and_activity_links() -> None:
    fields = JobSubmissionResponse.model_fields
    assert {"idempotent", "activity_url", "active_conflict"} <= fields.keys()


def test_detail_panels_do_not_recover_from_feature_area_alone() -> None:
    broad = []
    for path in MANIFEST["feature_panels"]["detail"]:
        source = _source(path)
        if "<FeatureActivityPanel" not in source:
            broad.append(f"{path}:missing-panel")
            continue
        if "subject_reference" not in source and "correlation_id" not in source:
            broad.append(path)
        if "types:" not in source:
            broad.append(f"{path}:missing-job-types")
        if "bind:active" not in source:
            broad.append(f"{path}:missing-server-active-state")
    assert broad == []


def test_subtitle_media_job_compatibility_seam_is_removed() -> None:
    source = _source("frontend/src/lib/api/subtitles.ts")
    types = _source("frontend/src/lib/api/types.ts")
    assert "active_job" not in source
    assert "interface MediaJob" not in types
