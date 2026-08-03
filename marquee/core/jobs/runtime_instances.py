"""Durable process-incarnation topology and safety evidence.

Runtime heartbeats never claim work, extend a PgQueuer ticket, or authorize a product write.
Each write uses a short, separately bounded SQLAlchemy session so telemetry degradation cannot
hold the delivery transaction or its advisory locks.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core import system_metrics
from marquee.core.jobs.process_identity import capture_process_identity, containment_capabilities
from marquee.core.jobs.worker_nodes import worker_build
from marquee.core.runtime_settings import effective_settings as settings
from marquee.database import _get_session_factory
from marquee.models import RuntimeInstance

logger = logging.getLogger(__name__)

RuntimeRole = Literal["worker", "scheduler"]
RuntimeReadiness = Literal["starting", "ready", "not_ready", "stopped"]
_VERSION = re.compile(r"\b\d+(?:\.\d+){1,3}\b")


def capability_snapshot(entrypoints: Iterable[str]) -> dict[str, Any]:
    """Return bounded capability facts without paths, environment, payloads, or raw dumps."""
    try:
        gpu = system_metrics.gpu_metrics()
    except (OSError, RuntimeError, ValueError):
        gpu = None
    gpu_facts = {
        "available": gpu is not None,
        "model": str(gpu.get("model", "unknown"))[:100] if gpu else None,
        "encoder_observable": bool(gpu and gpu.get("enc") is not None),
        "decoder_observable": bool(gpu and gpu.get("dec") is not None),
    }
    return {
        "entrypoints": sorted(set(entrypoints)),
        "containment": containment_capabilities().public(),
        "gpu": gpu_facts,
    }


class RuntimeInstanceHandle:
    """Lifecycle owner for one immutable runtime row and its best-effort heartbeat task."""

    def __init__(
        self,
        *,
        role: RuntimeRole,
        node_label: str,
        advertised_entrypoints: Iterable[str],
        capabilities: dict[str, Any] | None = None,
        session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        identity = capture_process_identity(os.getpid(), worker_node=node_label)
        self.instance_id = str(uuid4())
        self.role = role
        self.node_label = node_label
        self.build = worker_build()
        self.host_boot_id = identity.host_boot_id
        self.process_id = identity.pid
        self.process_group_id = identity.process_group_id
        self.process_start_ticks = identity.process_start_ticks
        self.cgroup_path = identity.cgroup_path
        self.advertised_entrypoints = tuple(sorted(set(advertised_entrypoints)))
        self.capabilities = capabilities or capability_snapshot(self.advertised_entrypoints)
        self._session_factory = session_factory or _get_session_factory()
        self._task: asyncio.Task[None] | None = None
        self._shutdown = asyncio.Event()
        self._heartbeat_failures = 0
        self._last_error_at: datetime | None = None

    @property
    def telemetry_health(self) -> dict[str, Any]:
        return {
            "status": "degraded" if self._last_error_at else "ok",
            "heartbeat_failures": self._heartbeat_failures,
            "last_error_at": self._last_error_at,
        }

    async def start(self) -> None:
        await self._register()
        self._task = asyncio.create_task(
            self._heartbeat_loop(), name=f"runtime-heartbeat:{self.instance_id}"
        )

    async def ready(self) -> None:
        await self._set_readiness("ready")

    async def not_ready(self) -> None:
        await self._set_readiness("not_ready")

    async def stop(self) -> None:
        self._shutdown.set()
        if self._task is not None:
            await self._task
            self._task = None
        try:
            await self._set_readiness("stopped")
        except Exception:
            logger.error("runtime instance graceful-stop evidence failed", exc_info=True)

    async def heartbeat_once(self) -> bool:
        try:
            await self._write_heartbeat()
        except Exception:
            self._heartbeat_failures += 1
            self._last_error_at = datetime.now(UTC)
            logger.error("runtime instance heartbeat failed", exc_info=True)
            return False
        return True

    async def _heartbeat_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown.wait(), timeout=settings.JOB_RUNTIME_HEARTBEAT_SECONDS
                )
            except TimeoutError:
                await self.heartbeat_once()

    async def _register(self) -> None:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=settings.JOB_RUNTIME_EXPIRY_SECONDS)
        async with asyncio.timeout(settings.HEALTH_READY_TIMEOUT_SECONDS):
            async with self._session_factory() as session, session.begin():
                session.add(
                    RuntimeInstance(
                        id=self.instance_id,
                        role=self.role,
                        node_label=self.node_label,
                        build=self.build,
                        host_boot_id=self.host_boot_id,
                        process_id=self.process_id,
                        process_start_ticks=self.process_start_ticks,
                        process_group_id=self.process_group_id,
                        cgroup_path=self.cgroup_path,
                        advertised_entrypoints=list(self.advertised_entrypoints),
                        capabilities=self.capabilities,
                        readiness="starting",
                        heartbeat_failures=0,
                        started_at=now,
                        last_heartbeat_at=now,
                        heartbeat_expires_at=expires,
                    )
                )

    async def _set_readiness(self, readiness: RuntimeReadiness) -> None:
        now = datetime.now(UTC)
        async with asyncio.timeout(settings.HEALTH_READY_TIMEOUT_SECONDS):
            async with self._session_factory() as session, session.begin():
                instance = await session.get(
                    RuntimeInstance, self.instance_id, with_for_update=True
                )
                if instance is None:
                    raise RuntimeError("runtime instance evidence disappeared")
                instance.readiness = readiness
                instance.last_heartbeat_at = now
                instance.heartbeat_expires_at = now + timedelta(
                    seconds=settings.JOB_RUNTIME_EXPIRY_SECONDS
                )
                if readiness == "stopped":
                    instance.stopped_at = now

    async def _write_heartbeat(self) -> None:
        now = datetime.now(UTC)
        async with asyncio.timeout(settings.HEALTH_READY_TIMEOUT_SECONDS):
            async with self._session_factory() as session, session.begin():
                instance = await session.get(
                    RuntimeInstance, self.instance_id, with_for_update=True
                )
                if instance is None:
                    raise RuntimeError("runtime instance evidence disappeared")
                if instance.readiness == "stopped":
                    raise RuntimeError("stopped runtime instance cannot heartbeat")
                instance.last_heartbeat_at = now
                instance.heartbeat_expires_at = now + timedelta(
                    seconds=settings.JOB_RUNTIME_EXPIRY_SECONDS
                )
                instance.heartbeat_failures = self._heartbeat_failures
                instance.last_telemetry_error_at = self._last_error_at
