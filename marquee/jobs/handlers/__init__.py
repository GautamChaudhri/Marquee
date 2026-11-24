"""Thin adapters from a job to the domain code that does its work, and the list of kinds.

A gpu kind names its function by import path, so the worker never imports it: only the
GPU child does.
"""

from __future__ import annotations

from marquee.jobs.handlers.library_sync import library_sync
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
    JobKind(
        "engine_probe",
        "gpu",
        gpu_function="marquee.jobs.handlers.gpu:engine_probe",
        timeout_s=10 * 60,
    ),
)
