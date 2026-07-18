"""JMC6F static retirement contracts.

These encode the end state of JMC6F legacy retirement (plan §7, decisions F02/F03/
F04/F05/F08). The *absence* assertions are intentionally RED at F0 because the
detached lifecycles still exist; they turn green as F1-F3 delete them. The
*canonical replacement* guards are green regression anchors proving the surviving
canonical PgQueuer path and the extracted pure services remain.

No production module may re-introduce a process-local job/run/batch lifecycle or a
legacy delivery executor injection. Pure algorithms (poster ranking/OCR, letterbox
detection, archive loading, extractor/GPU management) survive only behind neutral,
purpose-named services used by canonical handlers or routes.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _production_paths() -> list[Path]:
    return [
        path
        for path in (ROOT / "marquee").rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def _production_sources() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in _production_paths())


# --------------------------------------------------------------------------- #
# Absence contracts (RED at F0, green after retirement)
# --------------------------------------------------------------------------- #


def test_detached_poster_run_lifecycle_is_retired() -> None:
    """F03: RunManager's process-local start/_execute/active-run/rebuild lifecycle."""
    production = _production_sources()
    for token in (
        "class RunManager",
        "class RunState",
        "class RunInProgressError",
        "RunInProgressError",
        "_active_run_id",
        "def begin_rebuild",
        "def end_rebuild",
    ):
        assert token not in production, f"detached run lifecycle token still present: {token}"


def test_legacy_batch_poster_engine_is_retired() -> None:
    """F03: the detached batch_runner poster engine has no module and no importer."""
    assert not (ROOT / "marquee/pipeline/batch_runner.py").exists()
    production = _production_sources()
    for token in (
        "pipeline.batch_runner",
        "import batch_runner",
        "async def run_batch",
        "run_batch_assets",
        "class _BatchMovie",
    ):
        assert token not in production, f"legacy batch engine token still present: {token}"


def test_detached_letterbox_batch_lifecycle_is_retired() -> None:
    """F02: LetterboxManager's JobState/start_batch/_execute_batch batch lifecycle."""
    production = _production_sources()
    for token in (
        "class JobState",
        "BatchInProgressError",
        "def start_batch",
        "def _execute_batch",
        "def active_job_id",
    ):
        assert token not in production, f"detached letterbox lifecycle token still present: {token}"


def test_no_route_imports_a_process_local_pipeline_lifecycle() -> None:
    """F01/F08: API routes reach pipeline work only through neutral services."""
    for relative in (ROOT / "marquee/api/routes").glob("*.py"):
        text = relative.read_text(encoding="utf-8")
        assert "pipeline.run_manager" not in text, relative.name
        assert "pipeline.batch_runner" not in text, relative.name
        assert "run_manager.start(" not in text, relative.name
        assert "run_manager.get_state" not in text, relative.name


def test_legacy_delivery_executor_injection_is_retired() -> None:
    """F05: canonical delivery has no injectable legacy executor seam."""
    delivery = _src("marquee/core/jobs/delivery.py")
    for token in ("LegacyNoopExecutor", "executor: LegacyNoopExecutor"):
        assert token not in delivery, f"legacy delivery executor token still present: {token}"


# --------------------------------------------------------------------------- #
# Canonical replacement guards (GREEN — surviving canonical path)
# --------------------------------------------------------------------------- #


def test_canonical_letterbox_detection_survives() -> None:
    """F07: canonical letterbox detection stays on marquee.media.letterbox_detect."""
    handler = _src("marquee/core/jobs/handlers_letterbox.py")
    assert "from marquee.media.letterbox_detect import" in handler
    assert 'register_execution_handler("letterbox_detect"' in handler


def test_canonical_poster_pipeline_handler_survives() -> None:
    """F03: exactly one canonical poster_pipeline execution handler remains."""
    handler = _src("marquee/core/jobs/poster_pipeline.py")
    assert "async def execute_poster_pipeline" in handler


def test_extracted_neutral_modules_have_no_lifecycle_coupling() -> None:
    """§4: the pure services extracted from retired managers import no API route,
    global job lifecycle, canonical Job mutation, or PgQueuer transport."""
    assert not (ROOT / "marquee/pipeline/run_manager.py").exists()
    for relative in (
        "marquee/pipeline/extractor_runtime.py",
        "marquee/pipeline/official_pick.py",
    ):
        source = _src(relative)
        for token in (
            "marquee.api.routes",
            "pgqueuer",
            "JobDispatch",
            "deliver_control_job",
            "deliver_job",
            "job_manager",
            "register_execution_handler",
        ):
            assert token not in source, f"{relative} couples to lifecycle token: {token}"
