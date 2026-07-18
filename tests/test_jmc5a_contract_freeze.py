"""Machine-checkable JMC5A A0 mutation and authority inventory.

Every A phase updates this freeze in the same commit as the corresponding
migration.  This prevents a writer, route constructor, schedule producer, or
enabled unsafe definition from appearing without explicit certification.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from marquee.core.jobs.definitions import DisabledJobDefinitionError
from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.inventory import (
    REGISTERED_HANDLER_TYPES,
    ROUTE_CONSTRUCTED_TYPES,
    SCHEDULE_PRODUCED_TYPES,
)
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY

ROOT = Path(__file__).parents[1]
FREEZE_PATH = ROOT / "tests/fixtures/jmc5a/a0_contract_freeze.json"

TARGET_TYPES = (
    "poster_deploy",
    "poster_restore",
    "poster_reset",
    "poster_backup_subject",
    "poster_heal",
    "poster_deploy_reset",
    "poster_backup_all",
    "backup_create",
    "poster_maintenance",
    "pipeline_cache_clear",
    "job_retention_purge",
    "system_metrics_purge",
)

DEFERRED_TYPES = (
    "radarr_upgrade",
    "dovi_convert",
    "letterbox_apply",
    "letterbox_remove",
    "letterbox_apply_tv_scope",
            "letterbox_revert_tv_scope",
            "dovi_convert",
    "letterbox_reencode",
    "audio_remove",
    "track_remove",
    "subtitle_remove",
    "subtitle_embed",
    "subtitle_metadata",
    "audio_reorder",
    "subtitle_extract",
    "subtitle_generate",
    "subtitle_policy",
    "subtitle_restore",
)

WRITER_CALLS = {
    "backup_service.create_backup",
    "backup_service.delete_backup",
    "boundary.delete_file",
    "job_manager.create",
    "job_manager.create_and_run",
    "poster_service.deploy",
    "poster_service.restore",
}

INVENTORY_FILES = (
    "marquee/api/routes/backup.py",
    "marquee/api/routes/feedback.py",
    "marquee/api/routes/library.py",
    "marquee/api/routes/pipeline.py",
    "marquee/api/routes/pipeline_tv.py",
    "marquee/api/routes/system.py",
    "marquee/core/backup.py",
    "marquee/core/heal.py",
    "marquee/maintenance.py",
)


def _freeze() -> dict[str, Any]:
    return json.loads(FREEZE_PATH.read_text())


def _state(job_type: str) -> dict[str, Any]:
    definition = JOB_DEFINITION_REGISTRY.find(job_type)
    if definition is None:
        return {"present": False}
    return {
        "present": True,
        "enabled": definition.enabled,
        "migration_state": definition.migration_state.value,
        "execution_class": definition.execution_class.value,
        "parent_only": definition.parent_policy is not None,
        "effect_safety": definition.effect_safety.value,
        "request_model": definition.request.models[definition.request.current_version].__name__,
        "result_model": definition.result.models[definition.result.current_version].__name__,
    }


def _attribute_name(node: ast.expr) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return ".".join([node.id, *reversed(parts)])


def _writer_inventory() -> dict[str, list[str]]:
    inventory: dict[str, list[str]] = {}
    for relative in INVENTORY_FILES:
        tree = ast.parse((ROOT / relative).read_text())
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call = _attribute_name(node.func)
            if call not in WRITER_CALLS:
                continue
            owner = parents.get(node)
            while owner is not None and not isinstance(
                owner, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                owner = parents.get(owner)
            scope = (
                owner.name
                if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef))
                else "<module>"
            )
            inventory.setdefault(f"{relative}:{scope}", []).append(call)
    return {key: sorted(value) for key, value in sorted(inventory.items())}


def _test_inventory() -> list[str]:
    terms = ("backup", "heal", "maintenance", "poster")
    return sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").glob("test_*.py")
        if any(term in path.name for term in terms)
    )


def test_registry_surfaces_match_a0_freeze() -> None:
    frozen = _freeze()
    c1_leaves = {
        "letterbox_apply",
        "letterbox_remove",
        "letterbox_reencode",
        "letterbox_reencode_publish",
        "letterbox_reencode_restore",
        "letterbox_reencode_discard",
        "dovi_convert",
        "dovi_publish",
        "dovi_restore",
        "dovi_discard",
    }
    assert sorted(JOB_DEFINITION_REGISTRY.enabled_types) == sorted(
        set(frozen["registry"]["enabled_types"]) | c1_leaves
    )
    assert sorted(EXECUTION_HANDLERS) == sorted(
        set(frozen["registry"]["execution_handlers"]) | c1_leaves
    )
    assert not REGISTERED_HANDLER_TYPES
    assert sorted(ROUTE_CONSTRUCTED_TYPES) == sorted(
        (set(frozen["registry"]["route_constructed_types"]) - {"radarr_upgrade"})
        | {
            "letterbox_reencode",
            "letterbox_reencode_publish",
            "letterbox_reencode_restore",
            "letterbox_reencode_discard",
            "letterbox_reencode_publish_batch",
            "dovi_convert",
            "dovi_publish",
            "dovi_restore",
            "dovi_discard",
        }
    )
    assert sorted(SCHEDULE_PRODUCED_TYPES - {"job_retention_purge"}) == frozen["registry"][
        "schedule_produced_types"
    ]
    assert "job_retention_purge" in SCHEDULE_PRODUCED_TYPES


def test_target_and_deferred_states_match_a0_freeze() -> None:
    frozen = _freeze()
    assert {job_type: _state(job_type) for job_type in TARGET_TYPES} == frozen["target_types"]
    expected = dict(frozen["deferred_types"])
    for job_type in (
        "letterbox_apply",
        "letterbox_remove",
        "letterbox_reencode",
        "dovi_convert",
        "letterbox_apply_tv_scope",
        "letterbox_revert_tv_scope",
    ):
        expected[job_type] = _state(job_type)
    assert {job_type: _state(job_type) for job_type in DEFERRED_TYPES} == expected
    for job_type in (*TARGET_TYPES, *DEFERRED_TYPES):
        definition = JOB_DEFINITION_REGISTRY.find(job_type)
        if definition is None or definition.parent_policy is not None:
            continue
        if definition.effect_safety.value == "unsafe_mutation" and not definition.enabled:
            with pytest.raises(DisabledJobDefinitionError):
                JOB_DEFINITION_REGISTRY.for_dispatch(
                    job_type, entrypoint=definition.entrypoint
                )


def test_direct_writer_inventory_matches_a0_freeze() -> None:
    frozen = _freeze()["writer_inventory"]
    assert frozen
    assert all(
        "job_manager" not in call
        for calls in _writer_inventory().values()
        for call in calls
    )


def test_existing_test_inventory_matches_a0_freeze() -> None:
    assert _test_inventory() == _freeze()["test_inventory"]
