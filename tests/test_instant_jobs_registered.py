"""The API process must load every handler module that registers an
``instant=True`` job type, or ``job_manager.create_and_run`` 500s for any
route that uses it (see marquee/core/jobs/handlers.py / manager.py)."""

from __future__ import annotations

from marquee.core.jobs.handlers import is_instant
from marquee.main import app as _app  # noqa: F401 - importing main is the assertion

INSTANT_JOB_TYPES_USED_BY_ROUTES = [
    "pipeline_cache_clear",
    "poster_deploy_reset",
    "backup_create",
    "letterbox_apply",
    "letterbox_remove",
    "poster_heal",
]


def test_route_instant_job_types_are_registered_after_importing_main() -> None:
    for job_type in INSTANT_JOB_TYPES_USED_BY_ROUTES:
        assert is_instant(job_type), f"{job_type!r} not registered for instant execution"
