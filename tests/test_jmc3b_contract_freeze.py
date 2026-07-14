from __future__ import annotations

import inspect
import json
import re
from dataclasses import fields
from pathlib import Path

from marquee.core.jobs.delivery import ExecutionContext
from marquee.core.jobs.fenced_writer import FencedWriter
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.process_launcher import ProcessLauncher
from marquee.core.jobs.progress import (
    ConcurrentSubject,
    JobProgress,
    ProgressMeasurement,
    ProgressMeasurementUpdate,
    ProgressMetrics,
    ProgressWait,
)
from marquee.models.job import JobEvent
from marquee.models.job_evidence import JobArtifact, JobLog

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "jmc3b" / "b0_contract_freeze.json"


def _contract() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_jmc3b_starts_from_the_certified_production_registry() -> None:
    registry = _contract()["registry"]
    assert isinstance(registry, dict)
    assert JOB_DEFINITION_REGISTRY.enabled_types == set(registry["enabled_types"])
    assert JOB_DEFINITION_REGISTRY.enabled_types == {
        "system_noop",
        "library_sync",
        "letterbox_detect",
        "letterbox_detect_episode",
        "letterbox_detect_tv_scope",
        "subtitle_scan",
        "subtitle_policy_audit",
        "dovi_analyze",
    }


def test_jmc2_contract_tests_are_explicit_inputs_to_jmc3b() -> None:
    paths = _contract()["frozen_contract_tests"]
    assert isinstance(paths, list)
    assert paths == sorted(set(paths))
    for value in paths:
        path = ROOT / value
        assert path.is_file(), f"stale JMC2 contract input: {value}"


def test_evidence_table_shapes_are_frozen_before_migration() -> None:
    tables = _contract()["evidence_tables"]
    assert isinstance(tables, dict)
    models = (JobEvent, JobLog, JobArtifact)
    assert {
        model.__name__: [column.name for column in model.__table__.columns] for model in models
    } == tables


def test_progress_schema_shapes_are_frozen_before_writer_integration() -> None:
    expected = _contract()["progress_models"]
    assert isinstance(expected, dict)
    models = (
        ProgressMeasurement,
        ProgressMeasurementUpdate,
        ProgressMetrics,
        ProgressWait,
        ConcurrentSubject,
        JobProgress,
    )
    assert {model.__name__: list(model.model_fields) for model in models} == expected
    assert "percent" not in ProgressMeasurementUpdate.model_fields


def test_jmc3a_evidence_extension_points_are_frozen() -> None:
    extension_points = _contract()["jmc3a_extension_points"]
    assert isinstance(extension_points, dict)
    assert [field.name for field in fields(ExecutionContext)] == extension_points[
        "execution_context_fields"
    ]
    assert (
        sorted(
            name
            for name, value in inspect.getmembers(FencedWriter, predicate=inspect.isfunction)
            if not name.startswith("_")
        )
        == extension_points["fenced_writer_methods"]
    )
    assert (
        sorted(
            name
            for name, value in inspect.getmembers(ProcessLauncher, predicate=inspect.isfunction)
            if not name.startswith("_")
        )
        == extension_points["process_launcher_methods"]
    )


def test_all_inventoried_direct_event_constructors_were_removed_in_b1() -> None:
    inventory = _contract()["direct_event_writers"]
    assert isinstance(inventory, dict)
    assert sum(inventory.values()) == 13
    actual: dict[str, int] = {}
    for path in (ROOT / "marquee").rglob("*.py"):
        if path == ROOT / "marquee" / "models" / "job.py":
            continue
        count = len(re.findall(r"\bJobEvent\(", path.read_text(encoding="utf-8")))
        if count:
            actual[str(path.relative_to(ROOT))] = count
    assert actual == {"marquee/core/jobs/event_service.py": 1}


def test_jobs_api_has_exactly_one_public_event_stream() -> None:
    source = (ROOT / "marquee" / "api" / "routes" / "jobs.py").read_text(encoding="utf-8")
    routes = sorted(re.findall(r'@router\.get\(\s*"([^"]*)"', source))
    assert routes == _contract()["existing_jobs_get_routes"]
    assert routes.count("/events/stream") == 1
    assert source.count('"text/event-stream"') == 4


def test_obsolete_helpers_have_an_explicit_removal_or_replacement_plan() -> None:
    plans = _contract()["obsolete_helper_plan"]
    assert isinstance(plans, list)
    paths = [plan["path"] for plan in plans]
    assert paths == sorted(set(paths))
    for plan in plans:
        assert (ROOT / plan["path"]).is_file()
        assert plan["contract"]
        assert plan["disposition"]


def test_logging_and_artifact_integration_surfaces_are_inventoried() -> None:
    contract = _contract()
    for key in ("logging_surfaces", "artifact_surfaces"):
        paths = contract[key]
        assert isinstance(paths, list)
        assert paths == sorted(set(paths))
        for value in paths:
            assert (ROOT / value).is_file(), f"stale B0 {key} entry: {value}"


def test_redaction_corpus_covers_required_secret_shapes_without_real_credentials() -> None:
    redaction = _contract()["redaction"]
    assert isinstance(redaction, dict)
    assert redaction["marker"] == "[REDACTED]"
    text_names = {case["name"] for case in redaction["text_cases"]}
    argument_names = {case["name"] for case in redaction["argument_cases"]}
    assert text_names == {
        "api_key_header",
        "authorization_bearer",
        "cross_chunk_secret",
        "exact_configured_value",
        "safe_text",
        "url_userinfo_and_query",
    }
    assert argument_names == {
        "database_url_argument",
        "joined_token_argument",
        "split_password_argument",
    }
    serialized = json.dumps(redaction)
    for forbidden in ("sk-", "ghp_", "AKIA", "BEGIN PRIVATE KEY"):
        assert forbidden not in serialized
