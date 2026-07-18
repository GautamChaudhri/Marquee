from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

from marquee.core.configuration import CONFIGURATION_CATALOG
from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.terminal_decision import (
    AttemptOutcome,
    DispatchDisposition,
    JobOutcome,
)

_MANIFEST = (
    Path(__file__).parent / "fixtures" / "jmc6g" / "enabled_definition_closure.json"
)


def test_enabled_definition_closure_manifest_is_complete_and_frozen():
    document = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    entries = {entry["job_type"]: entry for entry in document["definitions"]}

    assert document["version"] == 1
    assert document["plan_base"] == "d2dd72a4e01ca93883ae04b2af12f94c7be29f3a"
    assert document["audit_state"] == "g6_integrated_execution_evidence_certified"
    assert set(entries) == set(JOB_DEFINITION_REGISTRY.enabled_types)
    assert set(entries) == set(EXECUTION_HANDLERS)

    required = {
        "handler",
        "result_model",
        "allowed_outcomes",
        "retry",
        "timeout_seconds",
        "configuration_keys",
        "progress",
        "external_tools",
        "large_file_io",
        "projection",
        "artifacts",
        "presenter",
        "tests",
    }
    for job_type, entry in entries.items():
        definition = JOB_DEFINITION_REGISTRY.get(job_type)
        handler = EXECUTION_HANDLERS[job_type]
        assert required <= set(entry), job_type
        assert f"{handler.__module__}.{handler.__name__}".endswith(entry["handler"]), job_type
        assert entry["result_model"] == definition.result.models[
            definition.result.current_version
        ].__name__
        assert set(entry["allowed_outcomes"]) == definition.terminal_policy.result_outcomes
        for raw_outcome, canonical_outcome in definition.terminal_policy.outcome_mapping:
            assert raw_outcome in entry["allowed_outcomes"]
            assert isinstance(canonical_outcome, JobOutcome)
        assert entry["retry"] == {
            "max_attempts": definition.retry_policy.max_attempts,
            "delays": list(definition.retry_policy.transient_delays_seconds),
        }
        assert entry["timeout_seconds"] == definition.timeout.seconds
        declared_configuration = entry["configuration_keys"]
        if isinstance(declared_configuration, dict):
            set_name = declared_configuration["set"]
            predicate = document["configuration_sets"][set_name]
            expected_configuration = {
                key
                for key, catalog_entry in CONFIGURATION_CATALOG.items()
                if catalog_entry.owner == predicate["owner"]
                and catalog_entry.scope == predicate["scope"]
                and catalog_entry.database_owned == predicate["database_owned"]
                and catalog_entry.sensitivity == predicate["sensitivity"]
            }
        else:
            expected_configuration = set(declared_configuration)
        assert expected_configuration == definition.configuration_keys
        assert definition.configuration_audit == (
            "snapshot" if expected_configuration else "audited_empty"
        )
        assert entry["progress"] == {
            "strategy": definition.progress_policy.strategy.value,
            "stages": [key for key, _label in definition.progress_policy.stages],
        }
        assert entry["presenter"] == definition.presenter_key
        assert entry["projection"]
        assert entry["artifacts"]
        assert "missing" not in entry["projection"]
        assert "incomplete" not in entry["projection"]
        assert "missing" not in entry["artifacts"]
        assert "incomplete" not in entry["artifacts"]
        assert entry["tests"]


def test_terminal_policies_cover_every_enabled_result_family_without_forced_success():
    families: dict[str, tuple[tuple[str, JobOutcome], ...]] = {}
    for definition in JOB_DEFINITION_REGISTRY:
        if not definition.enabled:
            continue
        model = definition.result.models[definition.result.current_version]
        mapping = definition.terminal_policy.outcome_mapping
        prior = families.setdefault(model.__name__, mapping)
        assert prior == mapping

    assert families
    assert dict(families["PosterPipelineResultV1"])["review_required"] == (
        JobOutcome.PARTIALLY_SUCCEEDED
    )
    generic = dict(families["BuiltInResultV1"])
    assert generic["failed"] == JobOutcome.FAILED
    assert generic["no_change"] == JobOutcome.NO_CHANGE
    assert generic["unsafe"] == JobOutcome.UNSAFE
    assert {item.value for item in AttemptOutcome} == {
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
        "retrying",
    }
    assert {item.value for item in DispatchDisposition} == {
        "succeeded",
        "failed",
        "cancelled",
        "stale",
        "superseded",
        "dead_lettered",
    }


def test_enabled_handlers_cannot_read_database_owned_execution_configuration_live():
    database_execution_keys = {
        key
        for key, entry in CONFIGURATION_CATALOG.items()
        if entry.database_owned and entry.scope == "execution"
    }
    violations: list[str] = []
    for handler in EXECUTION_HANDLERS.values():
        source_path = inspect.getsourcefile(handler)
        assert source_path is not None
        source = Path(source_path).read_text(encoding="utf-8")
        module = ast.parse(source, filename=source_path)
        if "configuration_provider" in source:
            violations.append(f"{handler.__module__}:configuration_provider")
        for node in ast.walk(module):
            if isinstance(node, ast.Attribute) and node.attr in database_execution_keys:
                violations.append(f"{handler.__module__}:{node.lineno}:{node.attr}")
    assert violations == []


def test_enabled_handler_modules_have_no_untracked_process_or_blocking_file_shortcuts():
    violations: list[str] = []
    checked: set[Path] = set()
    forbidden = ("subprocess", ".read_bytes(", ".write_bytes(", ".copy_file(")
    for handler in EXECUTION_HANDLERS.values():
        source_path = inspect.getsourcefile(handler)
        assert source_path is not None
        path = Path(source_path)
        if path in checked:
            continue
        checked.add(path)
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                violations.append(f"{handler.__module__}:{token}")
    assert violations == []


def test_long_running_definitions_own_semantic_progress_and_execution_surfaces():
    document = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    entries = {entry["job_type"]: entry for entry in document["definitions"]}
    for definition in JOB_DEFINITION_REGISTRY:
        if not definition.enabled or definition.job_type == "system_noop":
            continue
        assert definition.progress_policy.strategy.value != "none", definition.job_type
        assert definition.progress_policy.stages, definition.job_type
        entry = entries[definition.job_type]
        if entry["external_tools"]:
            assert entry["tests"], definition.job_type
        if entry["large_file_io"]:
            assert entry["tests"], definition.job_type
