"""Static architecture guards: source-level rules that no runtime test can catch.

These read the shipped modules and assert structural facts — each process role runs only
its own manager, the API holds no inline job handler, nothing reaches for a legacy claim or
recovery path, security-sensitive serving goes through the filesystem boundary rather than
string prefixes, and no raw poster delete or cross-device copy fallback survives."""

from __future__ import annotations

import re
from pathlib import Path

from marquee.core.jobs.supervisor import WorkerSupervisor

ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_shipped_roles_run_only_their_owned_manager():
    worker = _source("marquee/core/jobs/pgqueuer_worker.py")
    scheduler = _source("marquee/core/jobs/pgqueuer_scheduler.py")

    assert "app.qm.run(" in worker
    assert "app.sm.run(" not in worker
    assert "PgQueuer.run(" not in worker
    assert '"control"' in worker
    assert "app.sm.run()" in scheduler
    assert "app.qm.run(" not in scheduler
    assert "PgQueuer.run(" not in scheduler
    assert ".entrypoint(" not in scheduler


def test_api_contains_no_inline_job_handler_or_legacy_bootstrap():
    main = _source("marquee/main.py")

    assert "deliver_control_job" not in main
    assert "PgQueuer(" not in main
    assert "bootstrap_resources" not in main
    assert "marquee.core.jobs.worker" not in main
    assert "marquee.core.jobs.scheduler" not in main


def test_all_process_targets_exclude_custom_claim_runtime():
    compose = _source("docker/docker-compose.yml")
    children = WorkerSupervisor()._plan()
    targets = [child.args for child in children if child.name != "subgen"]

    assert "marquee.core.jobs.pgqueuer_worker" in compose
    assert "marquee.core.jobs.pgqueuer_scheduler" in compose
    assert 'marquee.core.jobs.worker"]' not in compose
    assert 'marquee.core.jobs.scheduler"]' not in compose
    assert ["-m", "marquee.core.jobs.pgqueuer_scheduler"] in targets
    assert ["-m", "marquee.core.jobs.pgqueuer_worker"] in targets


def test_new_runtime_does_not_call_legacy_claim_or_recovery():
    sources = "\n".join(
        _source(path)
        for path in (
            "marquee/core/jobs/pgqueuer_worker.py",
            "marquee/core/jobs/pgqueuer_scheduler.py",
            "marquee/core/jobs/delivery.py",
        )
    )
    for forbidden in (
        "claim_next",
        "recover_stale",
        "bootstrap_resources",
        "job_workers",
        "job_resources",
        "job_schedules",
    ):
        assert forbidden not in sources


REPOSITORY = Path(__file__).resolve().parents[1]


def test_security_sensitive_serving_uses_filesystem_boundary() -> None:
    route_sources = list((REPOSITORY / "marquee" / "api" / "routes").glob("*.py"))
    offenders = [
        path for path in route_sources if "FileResponse" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_string_prefix_is_not_used_for_path_confinement() -> None:
    backend_sources = list((REPOSITORY / "marquee").rglob("*.py"))
    unsafe = re.compile(r"str\([^\n]+\)\.startswith\(str\(")
    offenders = [
        path for path in backend_sources if unsafe.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_raw_poster_delete_and_cross_device_copy_fallback_are_absent() -> None:
    library = (REPOSITORY / "marquee" / "api" / "routes" / "library.py").read_text(encoding="utf-8")
    mutation_handlers = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPOSITORY / "marquee" / "core" / "jobs").glob("*mutation*.py")
    )
    assert not (REPOSITORY / "marquee/core/jobs/builtin_handlers.py").exists()
    assert "Path(entity.poster_path).unlink" not in library
    assert "marquee-replace" not in mutation_handlers
