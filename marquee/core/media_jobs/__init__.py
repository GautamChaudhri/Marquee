"""Durable media-job queue (design §22) — restart-safe background mutation work.

Unlike the in-memory ``RunManager`` (poster inference), media jobs are persisted
so they survive restarts, can be cancelled/paused, and carry an auditable
plan + result. The single ``media_job_manager`` runs an asyncio worker that
processes queued jobs serially (mutations must not overlap on one disk) and
streams progress over SSE backed by ``media_job_events``.
"""

from marquee.core.media_jobs.manager import media_job_manager

__all__ = ["media_job_manager"]
