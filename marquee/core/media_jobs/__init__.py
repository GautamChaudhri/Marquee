"""Fail-closed media-job facade (JMC2A).

The duplicate media lifecycle was removed with the canonical schema; media
operations return as canonical jobs with 1:1 operation detail in later chunks.
"""

from marquee.core.media_jobs.manager import media_job_manager

__all__ = ["media_job_manager"]
