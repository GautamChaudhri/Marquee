"""PostgreSQL-backed durable job platform."""

from marquee.core.jobs.manager import JobManager, job_manager

__all__ = ["JobManager", "job_manager"]
