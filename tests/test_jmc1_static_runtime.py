from __future__ import annotations

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
    assert "PgQueuer" not in main
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
