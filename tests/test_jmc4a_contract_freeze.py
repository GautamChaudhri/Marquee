"""Machine-checkable A0 inventory for the exact JMC4A plan base."""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any

from marquee.api.routes import jobs as job_routes
from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.pgqueuer_gateway import PgQueuerGateway
from marquee.core.jobs.pgqueuer_worker import entrypoint_concurrency_limits
from marquee.models import Job, JobAttempt, JobDispatch, JobEvent
from tests.support.jmc6j import current_job_types

ROOT = Path(__file__).parents[1]
FREEZE_PATH = ROOT / "tests/fixtures/jmc4a/a0_contract_freeze.json"


def _freeze() -> dict[str, Any]:
    return json.loads(FREEZE_PATH.read_text())


def _top_level_functions(relative_path: str) -> list[str]:
    tree = ast.parse((ROOT / relative_path).read_text())
    return sorted(
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def _decorated_nested_functions(relative_path: str, decorator_name: str) -> list[str]:
    tree = ast.parse((ROOT / relative_path).read_text())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            target = call.func if call is not None else decorator
            if isinstance(target, ast.Attribute) and target.attr == decorator_name:
                found.append(node.name)
    return sorted(found)


def _legacy_producer_calls() -> dict[str, int]:
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


def test_canonical_schema_registry_and_execution_inventory_is_frozen() -> None:
    frozen = _freeze()
    models = (Job, JobDispatch, JobAttempt, JobEvent)

    assert {model.__tablename__: list(model.__table__.columns.keys()) for model in models} == frozen[
        "models"
    ]
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
        "letterbox_reencode_publish",
        "letterbox_reencode_restore",
            "letterbox_reencode_discard",
            "dovi_convert",
            "dovi_publish",
            "dovi_restore",
            "dovi_discard",
    }
    assert JOB_DEFINITION_REGISTRY.enabled_types == current_job_types(
        frozen["registry"]["enabled_types"]
    ) | poster_leaves
    assert set(EXECUTION_HANDLERS) == current_job_types(frozen["execution_handlers"]) | poster_leaves


def test_gateway_commands_parent_worker_scheduler_and_routes_are_frozen() -> None:
    frozen = _freeze()
    gateway_methods = sorted(
        name
        for name, value in PgQueuerGateway.__dict__.items()
        if callable(value) and not name.startswith("_")
    )
    routes = sorted(
        f"{','.join(sorted(route.methods or set()))} {route.path} {route.name}"
        for route in job_routes.router.routes
    )

    assert gateway_methods == sorted([*frozen["gateway_methods"], "recover_admission_deferral"])
    assert _top_level_functions("marquee/core/jobs/commands.py") == frozen["command_functions"]
    assert _top_level_functions("marquee/core/jobs/parent_progress.py") == frozen[
        "parent_progress_functions"
    ]
    assert frozen["worker_entrypoints"] == ["control"]
    assert _decorated_nested_functions("marquee/core/jobs/pgqueuer_worker.py", "entrypoint") == []
    assert sorted(entrypoint_concurrency_limits()) == [
        "control",
        "cpu",
        "gpu",
        "maintenance",
        "media_read",
        "media_write",
        "network",
    ]
    assert _decorated_nested_functions(
        "marquee/core/jobs/pgqueuer_scheduler.py", "schedule"
    ) == frozen["scheduler_callbacks"]
    assert routes == sorted(frozen["job_routes"])


def test_every_legacy_producer_call_is_frozen() -> None:
    frozen = dict(_freeze()["legacy_producer_calls"])
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
    assert _legacy_producer_calls() == {}
