"""Thin adapters from a job to the domain code that does its work, and the list of kinds.

A gpu kind either names its function by import path, so the worker never imports it (only
the GPU child does), or has a handler that calls the child through ``ctx.gpu``.
"""

from __future__ import annotations

from marquee.jobs.handlers.library_sync import library_sync
from marquee.jobs.handlers.poster_run import POSTER_RUN, POSTER_RUN_MASKED
from marquee.jobs.handlers.poster_write import POSTER_DEPLOY, POSTER_RESTORE
from marquee.jobs.handlers.taste_rebuild import TASTE_REBUILD
from marquee.jobs.registry import JobKind

KINDS: tuple[JobKind, ...] = (
    JobKind(
        "library_sync",
        "network",
        handler=library_sync,
        max_attempts=3,
        timeout_s=15 * 60,
        dedupe_key="library_sync",
    ),
    JobKind(
        "gpu_selftest",
        "gpu",
        gpu_function="marquee.jobs.handlers.gpu:gpu_selftest",
        timeout_s=5 * 60,
    ),
    POSTER_RUN,
    POSTER_RUN_MASKED,
    POSTER_DEPLOY,
    POSTER_RESTORE,
    TASTE_REBUILD,
)
