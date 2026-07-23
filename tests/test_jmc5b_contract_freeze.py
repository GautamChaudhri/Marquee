"""Machine-checkable JMC5B B0 audio/subtitle contract and authority inventory.

Every B phase updates this freeze in the same commit as the corresponding
migration.  It pins the audio/subtitle route surface, the twelve JMC5B job
definitions, the legacy ``MediaJob``/``MediaBackup``/``cancel_registry``
authority that JMC5B removes, and every direct subprocess launch that must move
behind the JMC3 tracked launcher.  A writer, route, or enabled unsafe definition
cannot appear without explicit certification.
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
from tests.support.jmc6j import current_job_types

ROOT = Path(__file__).parents[1]
FREEZE_PATH = ROOT / "tests/fixtures/jmc5b/b0_contract_freeze.json"

# B04 enabled-leaf targets plus the two parent-only aggregates from B05.
TARGET_TYPES = (
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
    "subtitle_generate_batch",
    "subtitle_policy_batch",
)

# Types JMC5B must leave untouched and dispatch-disabled for JMC5C.
DEFERRED_TYPES = (
    "dovi_convert",
    "letterbox_apply",
    "letterbox_apply_tv_scope",
            "letterbox_heal",
            "dovi_convert",
    "letterbox_reencode",
    "letterbox_remove",
    "letterbox_revert_tv_scope",
    "radarr_upgrade",
)

# Legacy authority forbidden from every migrated JMC5B module.
LEGACY_SYMBOLS = {
    "MediaBackup",
    "cancel_registry",
    "media_job_manager",
}

# Direct child launches that must route through the JMC3 tracked launcher.
LAUNCH_CALLS = {
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
}

INVENTORY_FILES = (
    "marquee/api/routes/audio_subs.py",
    "marquee/api/routes/subtitle_generators.py",
    "marquee/api/routes/subtitle_policies.py",
    "marquee/api/routes/subtitles.py",
    "marquee/core/subtitles/backup.py",
    "marquee/core/subtitles/generation.py",
    "marquee/core/subtitles/mutation.py",
    "marquee/core/subtitles/policy.py",
    "marquee/core/subtitles/restore.py",
    "marquee/core/jobs/audio_subtitle_planning.py",
    "marquee/core/jobs/handlers_generation.py",
    "marquee/core/jobs/handlers_policy_restore.py",
    "marquee/core/jobs/handlers_sidecars.py",
    "marquee/core/jobs/handlers_track_mutations.py",
)

ROUTE_MODULES = (
    "marquee/api/routes/audio_subs.py",
    "marquee/api/routes/subtitle_generators.py",
    "marquee/api/routes/subtitle_policies.py",
    "marquee/api/routes/subtitles.py",
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
        "tool_adapter": definition.progress_policy.tool_adapter,
    }


def _attribute_name(node: ast.expr) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return ".".join([node.id, *reversed(parts)])


def _scope_of(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    owner = parents.get(node)
    while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
        owner = parents.get(owner)
    return owner.name if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)) else "<module>"


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _legacy_inventory() -> dict[str, list[str]]:
    """Every reference to legacy authority, keyed by ``file:scope``."""
    inventory: dict[str, list[str]] = {}
    for relative in INVENTORY_FILES:
        path = ROOT / relative
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        parents = _parent_map(tree)
        for node in ast.walk(tree):
            name: str | None = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                attribute = _attribute_name(node)
                name = attribute.split(".")[0] if attribute else None
            if name not in LEGACY_SYMBOLS:
                continue
            inventory.setdefault(f"{relative}:{_scope_of(node, parents)}", []).append(name)
    return {key: sorted(set(value)) for key, value in sorted(inventory.items())}


def _launch_inventory() -> dict[str, list[str]]:
    """Direct subprocess launches inside JMC5B-owned modules."""
    inventory: dict[str, list[str]] = {}
    for relative in INVENTORY_FILES:
        path = ROOT / relative
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        parents = _parent_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call = _attribute_name(node.func)
            if call not in LAUNCH_CALLS:
                continue
            inventory.setdefault(f"{relative}:{_scope_of(node, parents)}", []).append(call)
    return {key: sorted(value) for key, value in sorted(inventory.items())}


def _route_inventory() -> dict[str, list[str]]:
    """Declared HTTP methods/paths per audio/subtitle route module."""
    methods = {"get", "post", "put", "patch", "delete"}
    inventory: dict[str, list[str]] = {}
    for relative in ROUTE_MODULES:
        path = ROOT / relative
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        routes: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                target = _attribute_name(decorator.func)
                if target is None or "router" not in target.split(".")[0]:
                    continue
                verb = target.split(".")[-1]
                if verb not in methods or not decorator.args:
                    continue
                first = decorator.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    routes.append(f"{verb.upper()} {first.value}")
        inventory[relative] = sorted(routes)
    return inventory


def _test_inventory() -> list[str]:
    terms = ("audio", "subtitle", "subgen", "media_job", "jmc5b_")
    return sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").glob("test_*.py")
        if any(term in path.name for term in terms)
    )


def test_registry_surfaces_match_b0_freeze() -> None:
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
        current_job_types(frozen["registry"]["enabled_types"]) | c1_leaves
    )
    assert sorted(EXECUTION_HANDLERS) == sorted(
        current_job_types(frozen["registry"]["execution_handlers"]) | c1_leaves
    )
    assert not REGISTERED_HANDLER_TYPES
    assert sorted(ROUTE_CONSTRUCTED_TYPES) == sorted(
        (current_job_types(frozen["registry"]["route_constructed_types"]) - {"radarr_upgrade"})
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


def test_target_and_deferred_states_match_b0_freeze() -> None:
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
        "letterbox_heal",
    ):
        expected[job_type] = _state(job_type)
    assert {job_type: _state(job_type) for job_type in DEFERRED_TYPES} == expected


def test_disabled_unsafe_targets_refuse_dispatch() -> None:
    """Any JMC5B/JMC5C leaf that is not yet enabled must fail closed."""
    for job_type in (*TARGET_TYPES, *DEFERRED_TYPES):
        definition = JOB_DEFINITION_REGISTRY.find(job_type)
        if definition is None or definition.parent_policy is not None:
            continue
        if definition.effect_safety.value == "unsafe_mutation" and not definition.enabled:
            with pytest.raises(DisabledJobDefinitionError):
                JOB_DEFINITION_REGISTRY.for_dispatch(job_type, entrypoint=definition.entrypoint)


def test_legacy_authority_inventory_matches_b0_freeze() -> None:
    assert _legacy_inventory() == _freeze()["legacy_authority"]


def test_direct_launch_inventory_matches_b0_freeze() -> None:
    assert _launch_inventory() == _freeze()["direct_launches"]


def test_route_inventory_matches_b0_freeze() -> None:
    assert _route_inventory() == _freeze()["routes"]


def test_existing_test_inventory_matches_b0_freeze() -> None:
    assert _test_inventory() == _freeze()["test_inventory"]
