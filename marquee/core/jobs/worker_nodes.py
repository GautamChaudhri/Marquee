"""Durable worker capability telemetry; never execution ownership."""

from __future__ import annotations

import importlib.metadata
from datetime import UTC, datetime

from marquee.core.jobs.process_identity import containment_capabilities, read_boot_id
from marquee.database import _get_session_factory
from marquee.models.job_evidence import WorkerNode


def worker_build() -> str:
    try:
        return importlib.metadata.version("marquee")[:64]
    except importlib.metadata.PackageNotFoundError:
        return "development"


async def record_worker_node(node_id: str, *, readiness: str) -> None:
    factory = _get_session_factory()
    async with factory() as session, session.begin():
        node = await session.get(WorkerNode, node_id)
        if node is None:
            node = WorkerNode(id=node_id)
            session.add(node)
        node.build = worker_build()
        node.boot_id = read_boot_id()
        node.capabilities = {"process_containment": containment_capabilities().public()}
        node.readiness = readiness
        node.last_seen_at = datetime.now(UTC)
