"""Machine-checkable JMC4C C0 inventory for poster/ML migration.

The target jobs deliberately start disabled.  Each C phase changes this freeze in
the same commit as its migration so a new executor, route, activation path, or
legacy lifecycle bridge cannot quietly appear.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from marquee.core.jobs.definitions import DisabledJobDefinitionError
from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from tests.support.jmc6j import current_job_types

ROOT = Path(__file__).parents[1]
FREEZE_PATH = ROOT / "tests/fixtures/jmc4c/c0_contract_freeze.json"

TARGET_TYPES = (
    "poster_pipeline",
    "poster_pipeline_batch",
    "poster_pipeline_tv_batch",
    "taste_rebuild",
    "taste_map",
    "ranking_residual_train",
    "poster_rescan",
)

DEFERRED_MUTATING_TYPES = (
    "poster_heal",
    "poster_deploy_reset",
    "poster_backup_all",
    "poster_maintenance",
    "pipeline_cache_clear",
    "radarr_upgrade",
    "backup_create",
    "dovi_convert",
    "letterbox_apply",
    "letterbox_remove",
    "letterbox_apply_tv_scope",
    "letterbox_revert_tv_scope",
    "letterbox_heal",
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
    }


def _call_inventory() -> dict[str, list[str]]:
    """Capture lifecycle and publication calls only in the C-family call graph."""
    inventory: dict[str, list[str]] = {}
    targets = {
        "marquee/api/routes/pipeline.py": {
            "run_pipeline",
            "run_pipeline_batch",
            "rescan_posters",
        },
        "marquee/api/routes/pipeline_tv.py": {"run_series_pipeline", "run_tv_pipeline_batch"},
        "marquee/api/routes/taste.py": {
            "retrain_taste",
            "retrain_ranking_residual",
            "rebuild_map",
            "cancel_retrain_taste",
        },
    }
    owners = {
        "job_manager",
        "media_job_manager",
        "cancel_registry",
        "run_manager",
    }
    for relative, functions in targets.items():
        tree = ast.parse((ROOT / relative).read_text())
        for node in ast.walk(tree):
            if (
                not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                or node.name not in functions
            ):
                continue
            calls: list[str] = []
            for child in ast.walk(node):
                if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                    continue
                owner = child.func.value
                if isinstance(owner, ast.Name) and owner.id in owners:
                    calls.append(f"{owner.id}.{child.func.attr}")
            inventory[f"{relative}:{node.name}"] = sorted(calls)
    return dict(sorted(inventory.items()))


def test_registry_and_handlers_match_c0_freeze() -> None:
    frozen = _freeze()
    additions = {
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
        "letterbox_reencode",
        "letterbox_reencode_publish",
        "letterbox_reencode_restore",
        "letterbox_reencode_discard",
        "dovi_convert",
        "dovi_publish",
        "dovi_restore",
        "dovi_discard",
    }
    assert (
        set(JOB_DEFINITION_REGISTRY.enabled_types)
        == current_job_types(frozen["registry"]["enabled_types"]) | additions
    )
    assert set(EXECUTION_HANDLERS) == current_job_types(frozen["execution_handlers"]) | additions


def test_target_states_match_c0_freeze() -> None:
    frozen = dict(_freeze()["target_types"])
    frozen["ranking_residual_train"] = frozen.pop("learned_head_train")
    assert sorted(frozen) == sorted(TARGET_TYPES)
    assert {job_type: _state(job_type) for job_type in TARGET_TYPES} == frozen


def test_deferred_mutation_never_dispatches() -> None:
    frozen = dict(_freeze()["deferred_mutating_types"])
    parent_state = {
        "present": True,
        "enabled": False,
        "migration_state": "parent_only",
        "execution_class": "control",
        "parent_only": True,
        "effect_safety": "read_only",
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
    enabled_maintenance = {
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
    }
    deferred = set(DEFERRED_MUTATING_TYPES) - enabled_maintenance
    for enabled in enabled_maintenance:
        frozen.pop(enabled, None)
    assert sorted(frozen) == sorted(deferred)
    assert {job_type: _state(job_type) for job_type in deferred} == frozen
    for job_type in deferred:
        definition = JOB_DEFINITION_REGISTRY.get(job_type)
        assert definition.execution_class.value != "media_write" or definition.enabled is False
        with pytest.raises(DisabledJobDefinitionError):
            JOB_DEFINITION_REGISTRY.for_dispatch(job_type, entrypoint=definition.entrypoint)


def test_c_family_lifecycle_and_activation_inventory_matches_freeze() -> None:
    frozen = {
        key: value
        for key, value in _freeze()["call_inventory"].items()
        if not key.startswith("marquee/core/jobs/builtin_handlers.py:")
    }
    old_route = "marquee/api/routes/taste.py:retrain_learned_head"
    if old_route in frozen:
        frozen["marquee/api/routes/taste.py:retrain_ranking_residual"] = frozen.pop(old_route)
    assert _call_inventory() == frozen
