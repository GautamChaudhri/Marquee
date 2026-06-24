"""Durable media-job records (design §22) — restart-safe background mutation work.

Unlike the in-memory ``RunManager`` (poster inference), media jobs are persisted
so they survive restarts, can be cancelled/paused, and carry an auditable
plan + result. Execution is driven by the generic job platform
(``marquee.core.jobs``) via the bridge in ``marquee.core.jobs.legacy_media``;
``media_job_manager`` holds the detailed record and streams progress over SSE
backed by ``media_job_events``.
"""

from marquee.core.media_jobs.manager import media_job_manager

__all__ = ["media_job_manager"]
