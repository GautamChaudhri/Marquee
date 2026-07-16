"""B0 contract freeze for built-in inventory and stable registry taxonomy."""

from __future__ import annotations

import ast
from pathlib import Path

from marquee.core.jobs.contracts import (
    AttentionLevel,
    EffectSafety,
    ExecutionClass,
    FeatureArea,
    JobAction,
    ProgressStrategy,
    TriggerKind,
)
from marquee.core.jobs.inventory import (
    BUILTIN_JOB_TYPES,
    MEDIA_OPERATION_TYPES,
    PARENT_ONLY_TYPES,
    REGISTERED_HANDLER_TYPES,
    ROUTE_CONSTRUCTED_TYPES,
)

ROOT = Path(__file__).resolve().parents[1]


def _literal_keywords(paths: list[Path], names: set[str]) -> set[str]:
    values: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg in names:
                    candidates = (
                        (keyword.value.body, keyword.value.orelse)
                        if isinstance(keyword.value, ast.IfExp)
                        else (keyword.value,)
                    )
                    values.update(
                        candidate.value
                        for candidate in candidates
                        if isinstance(candidate, ast.Constant)
                        and isinstance(candidate.value, str)
                    )
    return values


def _decorated_handler_types() -> set[str]:
    values: set[str] = set()
    for path in (ROOT / "marquee/core/jobs").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if isinstance(decorator.func, ast.Name) and decorator.func.id == "register":
                    value = decorator.args[0]
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        values.add(value.value)
    return values


def test_source_inventory_matches_freeze() -> None:
    assert _decorated_handler_types() == set(REGISTERED_HANDLER_TYPES)
    route_types = _literal_keywords(
        list((ROOT / "marquee/api/routes").glob("*.py")),
        {"job_type", "parent_type", "parent_job_type"},
    )
    assert route_types == set(ROUTE_CONSTRUCTED_TYPES)
    assert len(MEDIA_OPERATION_TYPES) == 19
    assert len(PARENT_ONLY_TYPES) == 18  # +C3 fixed letterbox publish parent
    assert len(BUILTIN_JOB_TYPES) == 61  # +JMC5C Dolby Vision conversion/publication


def test_stable_taxonomy_values() -> None:
    assert {item.value for item in ExecutionClass} == {
        "control", "network", "cpu", "media_read", "media_write", "gpu", "maintenance"
    }
    assert {item.value for item in FeatureArea} == {
        "ai_posters", "hdr", "audio_subtitles", "letterbox", "library_integrations",
        "ml_taste", "maintenance", "system",
    }
    assert {item.value for item in TriggerKind} == {
        "manual", "schedule", "policy", "batch", "parent", "healing", "system", "webhook"
    }
    assert {item.value for item in EffectSafety} == {
        "read_only", "staged_idempotent", "unsafe_mutation"
    }
    assert {item.value for item in ProgressStrategy} == {
        "determinate", "indeterminate", "hybrid", "none"
    }
    assert {item.value for item in AttentionLevel} == {"normal", "warning", "error"}
    assert {item.value for item in JobAction} == {
        "cancel", "pause", "resume", "change_priority", "retry", "open_logs",
        "open_artifacts", "open_detail",
    }
