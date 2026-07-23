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
from tests.support.jmc6j import current_job_types

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
    assert len(JOB_DEFINITION_REGISTRY) == frozen["registry"]["definition_count"] + 14
    poster_leaves = {
        "subtitle_policy",
        "subtitle_restore",
        "subtitle_generate",
        "subtitle_extract",
        "subtitle_embed",
        "taste_enrich",
        # JMC5B B2 track mutations.
        "audio_remove",
        "track_remove",
        "subtitle_remove",
        "audio_reorder",
        "subtitle_metadata",
        "poster_deploy",
        "poster_restore",
        "poster_reset",
        "poster_backup_subject",
        "backup_create",
        "poster_maintenance",
        "pipeline_cache_clear",
        "job_retention_purge",
        "system_metrics_purge",
        "letterbox_apply",
        "letterbox_remove",
        "letterbox_preview",
        "letterbox_reencode",
        "dovi_convert",
        "letterbox_reencode_publish",
        "letterbox_reencode_restore",
        "letterbox_reencode_discard",
        "dovi_publish",
        "dovi_restore",
        "dovi_discard",
    }
    assert (
        JOB_DEFINITION_REGISTRY.enabled_types
        == current_job_types(frozen["registry"]["enabled_types"]) | poster_leaves
    )
    assert set(EXECUTION_HANDLERS) == current_job_types(frozen["execution_handlers"]) | poster_leaves


def test_jmc4b_target_type_states_match_freeze() -> None:
    frozen = _freeze()["target_types"]
    assert sorted(frozen) == sorted(TARGET_TYPES)
    assert {job_type: _state(job_type) for job_type in TARGET_TYPES} == frozen


def test_deferred_destructive_types_stay_present_and_disabled() -> None:
    frozen = dict(_freeze()["deferred_disabled_types"])
    parent_state = {
        "present": True,
        "enabled": False,
        "migration_state": "parent_only",
        "execution_class": "control",
        "parent_only": True,
    }
    for job_type in (
        "poster_backup_all",
        "poster_deploy_reset",
        "poster_heal",
        "letterbox_apply_tv_scope",
        "letterbox_revert_tv_scope",
        "letterbox_heal",
    ):
        frozen[job_type] = parent_state
    for enabled in (
        "subtitle_policy",
        "subtitle_restore",
        "subtitle_generate",
        "subtitle_extract",
        "subtitle_embed",
        # JMC5B B2 enabled these; the still-deferred set below stays disabled.
        "audio_remove",
        "track_remove",
        "subtitle_remove",
        "audio_reorder",
        "subtitle_metadata",
        "backup_create",
        "poster_maintenance",
        "pipeline_cache_clear",
        "job_retention_purge",
        "system_metrics_purge",
        "letterbox_apply",
        "letterbox_remove",
        "letterbox_reencode",
        "dovi_convert",
    ):
        frozen.pop(enabled)
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
    frozen = dict(_freeze()["legacy_bypass_calls"])
    for retired in (
        "marquee/api/routes/pipeline.py:backup_all_posters:job_manager.create",
        "marquee/api/routes/pipeline.py:reset_deployed_posters:job_manager.create_and_run",
        "marquee/api/routes/system.py:trigger_heal:job_manager.create_and_run",
        "marquee/api/routes/backup.py:create_backup:job_manager.create_and_run",
        "marquee/api/routes/pipeline.py:clear_pipeline_cache:job_manager.create_and_run",
        "marquee/api/routes/pipeline.py:poster_maintenance:job_manager.create",
        "marquee/api/routes/audio_subs.py:generate_tv:job_manager.create_batch",
        "marquee/api/routes/subtitle_generators.py:generate_for_media_file:media_job_manager.create_job",
        "marquee/api/routes/subtitle_generators.py:generate_for_movie:media_job_manager.create_job",
        "marquee/core/subtitles/generation.py:run_generation_job:cancel_registry.get",
        "marquee/core/subtitles/mutation.py:_raise_if_cancel_requested:cancel_registry.get",
        "marquee/api/routes/letterbox.py:apply_batch:job_manager.create",
        "marquee/api/routes/letterbox.py:apply_one:job_manager.create_and_run",
        "marquee/api/routes/letterbox.py:apply_tv_scope:job_manager.create",
        "marquee/api/routes/letterbox.py:letterbox_heal:job_manager.create",
        "marquee/api/routes/letterbox.py:remove_one:job_manager.create_and_run",
        "marquee/api/routes/letterbox.py:revert_tv_scope:job_manager.create",
        "marquee/api/routes/hdr.py:convert_movie_dovi:job_manager.create",
    ):
        frozen.pop(retired)
    assert frozen
    assert _legacy_bypass_calls() == {}
