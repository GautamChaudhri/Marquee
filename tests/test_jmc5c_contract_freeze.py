"""JMC5C C0 freeze for the final writer, runtime, and schema authority.

The assertions deliberately describe the pre-migration tree.  Each C phase must
update this file in the same commit that removes or enables an authority so a
legacy writer, process launcher, schema object, or dispatch path cannot drift
silently while the final runtime is being retired.
"""

from __future__ import annotations

import ast
from pathlib import Path

from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY

ROOT = Path(__file__).parents[1]

JMC5C_EXISTING_TYPES = (
    "letterbox_reencode",
    "dovi_convert",
)

JMC5C_NEW_TYPES = (
    "letterbox_reencode_publish",
    "letterbox_reencode_restore",
    "letterbox_reencode_discard",
    "dovi_publish",
    "dovi_restore",
    "dovi_discard",
)

ENABLED_TYPES = {
    "audio_remove",
    "audio_reorder",
    "backup_create",
    "dovi_analyze",
    "dovi_convert",
    "dovi_publish",
    "dovi_restore",
    "dovi_discard",
    "job_retention_purge",
    "learned_head_train",
    "letterbox_detect",
    "letterbox_detect_episode",
    "letterbox_detect_tv_scope",
    "letterbox_apply",
    "letterbox_remove",
    "letterbox_reencode",
    "letterbox_reencode_publish",
    "letterbox_reencode_restore",
    "letterbox_reencode_discard",
    "library_sync",
    "pipeline_cache_clear",
    "poster_backup_subject",
    "poster_deploy",
    "poster_maintenance",
    "poster_pipeline",
    "poster_rescan",
    "poster_reset",
    "poster_restore",
    "subtitle_embed",
    "subtitle_extract",
    "subtitle_generate",
    "subtitle_metadata",
    "subtitle_policy",
    "subtitle_policy_audit",
    "subtitle_remove",
    "subtitle_restore",
    "subtitle_scan",
    "system_metrics_purge",
    "system_noop",
    "taste_map",
    "taste_enrich",
    "taste_rebuild",
    "track_remove",
}

DIRECT_LAUNCHES: dict[str, dict[str, int]] = {}


def _scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    owner = parents.get(node)
    while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
        owner = parents.get(owner)
    return owner.name if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)) else "<module>"


def _direct_launches(relative: str) -> dict[str, int]:
    tree = ast.parse((ROOT / relative).read_text())
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    result: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "create_subprocess_exec":
            continue
        scope = _scope(node, parents)
        result[scope] = result.get(scope, 0) + 1
    return result


def test_jmc5c_definition_and_executor_baseline_is_exact() -> None:
    assert set(JOB_DEFINITION_REGISTRY.enabled_types) == ENABLED_TYPES
    assert set(EXECUTION_HANDLERS) == ENABLED_TYPES

    for job_type in ("dovi_convert", "dovi_publish", "dovi_restore", "dovi_discard"):
        definition = JOB_DEFINITION_REGISTRY.find(job_type)
        assert definition is not None
        assert definition.enabled is True
        assert definition.effect_safety.value == "unsafe_mutation"
        assert definition.execution_class.value == "media_write"

    definition = JOB_DEFINITION_REGISTRY.find("letterbox_reencode")
    assert definition is not None
    assert definition.enabled is True
    assert definition.effect_safety.value == "unsafe_mutation"
    assert definition.execution_class.value == "media_write"

    for job_type in ("letterbox_apply", "letterbox_remove"):
        definition = JOB_DEFINITION_REGISTRY.find(job_type)
        assert definition is not None
        assert definition.enabled is True
        assert definition.effect_safety.value == "unsafe_mutation"
        assert definition.execution_class.value == "media_write"

    for job_type in (
        "letterbox_heal",
        "letterbox_apply_tv_scope",
        "letterbox_revert_tv_scope",
    ):
        definition = JOB_DEFINITION_REGISTRY.find(job_type)
        assert definition is not None
        assert definition.enabled is False
        assert definition.effect_safety.value == "read_only"
        assert definition.execution_class.value == "control"
        assert definition.child_job_types

    for job_type in (
        "letterbox_reencode_publish",
        "letterbox_reencode_restore",
        "letterbox_reencode_discard",
    ):
        definition = JOB_DEFINITION_REGISTRY.find(job_type)
        assert definition is not None
        assert definition.enabled is True
        assert definition.effect_safety.value == "unsafe_mutation"
        assert definition.execution_class.value == "media_write"

    parent = JOB_DEFINITION_REGISTRY.find("letterbox_reencode_publish_batch")
    assert parent is not None
    assert parent.enabled is False
    assert parent.child_job_types == frozenset({"letterbox_reencode_publish"})


def test_jmc5c_direct_child_launch_inventory_is_exact() -> None:
    assert {relative: _direct_launches(relative) for relative in DIRECT_LAUNCHES} == DIRECT_LAUNCHES


def test_c1_letterbox_mutations_have_one_canonical_writer_path() -> None:
    route = (ROOT / "marquee/api/routes/letterbox.py").read_text()
    canonical_handlers = (ROOT / "marquee/core/jobs/handlers_letterbox_mutations.py").read_text()
    assert "job_manager.create_and_run" not in route
    assert "letterbox_service.apply_episode_group" not in route
    assert "letterbox_service.remove_episode_group" not in route
    for job_type in ("letterbox_apply", "letterbox_remove"):
        assert (
            canonical_handlers.count(
                f'register_execution_handler("{job_type}", execute_{job_type})'
            )
            == 1
        )


def test_c2_parallel_reencode_runtime_authority_is_retired() -> None:
    model_path = ROOT / "marquee/models/letterbox_reencode.py"
    migration = (
        ROOT / "alembic/versions/0002_jmc2a_canonical_model_and_configuration.py"
    ).read_text()
    exports = (ROOT / "marquee/models/__init__.py").read_text()
    assert not model_path.exists()
    assert "letterbox_reencode_artifacts" not in migration
    assert "LetterboxReencodeArtifact" not in exports
    for relative in (
        "marquee/api/routes/letterbox.py",
        "marquee/core/sync_service.py",
    ):
        assert "LetterboxReencodeArtifact" not in (ROOT / relative).read_text()


def test_remaining_legacy_runtime_authority_is_retired() -> None:
    for relative in (
        "marquee/core/jobs/manager.py",
        "marquee/core/jobs/cancel_registry.py",
        "marquee/core/jobs/builtin_handlers.py",
        "marquee/core/jobs/handlers.py",
        "marquee/core/dovi_conversion.py",
        "marquee/core/dovi_analysis.py",
    ):
        assert not (ROOT / relative).exists()

    production = "\n".join(path.read_text() for path in (ROOT / "marquee").rglob("*.py"))
    for token in ("job_manager", "media_job_manager", "cancel_registry"):
        assert token not in production
    assert "WorkerSupervisor" in (ROOT / "marquee/main.py").read_text()

    assert not (ROOT / "marquee/core/letterbox_reencode.py").exists()


def test_c3_artifact_decisions_have_only_canonical_route_and_handler_paths() -> None:
    route = (ROOT / "marquee/api/routes/letterbox.py").read_text()
    handler = (ROOT / "marquee/core/jobs/handlers_letterbox_publication.py").read_text()
    for operation in ("publish", "restore", "discard"):
        assert f"letterbox_reencode.{operation}:" not in route
        assert handler.count(f'register_execution_handler("letterbox_reencode_{operation}", ') == 1
    for token in ("os.replace", ".unlink(", "shutil.move"):
        assert token not in route


def test_deferred_surfaces_remain_non_executable() -> None:
    assert not (ROOT / "marquee/api/routes/webhooks.py").exists()
    definition = JOB_DEFINITION_REGISTRY.find("radarr_upgrade")
    assert definition is not None
    assert definition.enabled is False
