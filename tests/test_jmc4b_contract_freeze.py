"""Machine-checkable JMC4B B0 inventory: exact target/deferred state and legacy bypass graph.

This freeze records the pre-migration contract for the JMC4B non-mutating families and the
destructive types that must stay fail-closed throughout Chunk 4B.  Each migrating phase updates
``tests/fixtures/jmc4b/b0_contract_freeze.json`` in its own commit so the contract delta is
explicit and drift fails loudly.
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from marquee.core.jobs.definitions import DisabledJobDefinitionError
from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY

ROOT = Path(__file__).parents[1]
FREEZE_PATH = ROOT / "tests/fixtures/jmc4b/b0_contract_freeze.json"

TARGET_TYPES = (
    "library_sync",
    "subtitle_scan",
    "subtitle_scan_all",
    "audio_subs_deep_scan",
    "subtitle_policy_audit",
    "letterbox_detect",
    "letterbox_detect_episode",
    "letterbox_detect_tv_scope",
    "letterbox_detect_batch",
    "letterbox_detect_tv_batch",
    "dovi_analyze",
    "dovi_analyze_batch",
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
    }


def _legacy_bypass_calls() -> dict[str, int]:
    """Every job_manager / media_job_manager / cancel_registry call site under marquee/."""
    calls: Counter[str] = Counter()
    for path in sorted((ROOT / "marquee").rglob("*.py")):
        tree = ast.parse(path.read_text())
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if not isinstance(owner, ast.Name) or owner.id not in {
                "job_manager",
                "media_job_manager",
                "cancel_registry",
            }:
                continue
            enclosing = parents.get(node)
            while enclosing is not None and not isinstance(
                enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                enclosing = parents.get(enclosing)
            function = enclosing.name if enclosing is not None else "<module>"
            relative = path.relative_to(ROOT).as_posix()
            calls[f"{relative}:{function}:{owner.id}.{node.func.attr}"] += 1
    return dict(sorted(calls.items()))


def test_registry_and_execution_handlers_match_freeze() -> None:
    frozen = _freeze()
    assert len(JOB_DEFINITION_REGISTRY) == frozen["registry"]["definition_count"]
    assert sorted(JOB_DEFINITION_REGISTRY.enabled_types) == frozen["registry"]["enabled_types"]
    assert sorted(EXECUTION_HANDLERS) == frozen["execution_handlers"]


def test_jmc4b_target_type_states_match_freeze() -> None:
    frozen = _freeze()["target_types"]
    assert sorted(frozen) == sorted(TARGET_TYPES)
    assert {job_type: _state(job_type) for job_type in TARGET_TYPES} == frozen


def test_deferred_destructive_types_stay_present_and_disabled() -> None:
    frozen = _freeze()["deferred_disabled_types"]
    assert {job_type: _state(job_type) for job_type in frozen} == frozen
    for job_type, state in frozen.items():
        assert state["present"] is True
        assert state["enabled"] is False
        # Fail-closed on dispatch: a deferred type cannot be delivered on any entrypoint.
        definition = JOB_DEFINITION_REGISTRY.get(job_type)
        with pytest.raises(DisabledJobDefinitionError):
            JOB_DEFINITION_REGISTRY.for_dispatch(job_type, entrypoint=definition.entrypoint)
        assert job_type not in EXECUTION_HANDLERS


def test_legacy_bypass_call_graph_matches_freeze() -> None:
    assert _legacy_bypass_calls() == _freeze()["legacy_bypass_calls"]
