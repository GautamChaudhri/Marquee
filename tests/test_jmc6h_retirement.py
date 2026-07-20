"""H20-H22 semantic retirement contracts for final product convergence."""

from __future__ import annotations

import ast
from pathlib import Path

from marquee.main import app

ROOT = Path(__file__).resolve().parents[1]
RETIRED_MODULES = {
    "marquee.core.heal",
    "marquee.core.letterbox_heal",
    "marquee.core.letterbox_reencode",
    "marquee.core.letterbox_service",
    "marquee.core.letterbox_tv_scope",
    "marquee.core.pipeline_cache",
    "marquee.core.poster_service",
    "marquee.media.letterbox_manager",
    "marquee.ml.artifact_registry",
    "marquee.models.artifact_snapshot",
    "marquee.pipeline.extractor_runtime",
}


def _python_sources() -> list[Path]:
    roots = (ROOT / "marquee", ROOT / "scripts", ROOT / "experiments")
    return sorted(
        path
        for source_root in roots
        for path in source_root.rglob("*.py")
        if "gauntlet-output" not in path.as_posix()
    )


def test_retired_modules_are_absent_and_semantically_unreachable() -> None:
    for module in RETIRED_MODULES:
        assert not (ROOT / f"{module.replace('.', '/')}.py").exists(), module

    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            assert RETIRED_MODULES.isdisjoint(imported), f"{path}: {imported}"


def test_retired_runtime_routes_and_startup_hooks_are_absent() -> None:
    paths = set(app.openapi()["paths"])
    assert "/api/system/release-gpu" not in paths
    assert "/api/taste/map/candidates" not in paths
    assert "/api/taste/exemplars/{name}/image" not in paths
    assert not any(path.endswith("/activate") for path in paths if path.startswith("/api/taste/"))

    executable = "\n".join(path.read_text(encoding="utf-8") for path in _python_sources())
    for token in (
        "/api/system/release-gpu",
        "ArtifactSnapshot",
        "warm_extractor",
        "asyncio.ensure_future(",
    ):
        assert token not in executable, f"retired executable token remains: {token}"

    startup = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in ("marquee/main.py", "marquee/database.py", "marquee/db_migration.py")
    )
    assert "migrate_legacy_runtime_state" not in startup
    assert "migrate_live_artifacts(" not in startup
    assert (ROOT / "marquee/ml/migrate_artifacts.py").is_file()


def test_surviving_algorithms_live_in_pure_purpose_named_modules() -> None:
    expected = {
        "marquee/core/letterbox_eligibility.py",
        "marquee/core/letterbox_scope.py",
        "marquee/core/letterbox_transcode.py",
        "marquee/core/poster_files.py",
        "marquee/core/jobs/poster_summary.py",
        "marquee/ml/publication_catalog.py",
    }
    for relative in expected:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "asyncio.create_task(" not in source
        assert "marquee.api.routes" not in source
