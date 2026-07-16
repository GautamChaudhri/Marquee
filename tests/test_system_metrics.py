"""Tests for GET /api/system/metrics (frontend G1 dashboard telemetry)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from marquee.core import system_metrics
from marquee.core.jobs.pgqueuer_gateway import pgqueuer_gateway
from marquee.main import app
from marquee.models import Job, SystemMetricsSample


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_metrics_shape_no_gpu(db, client: AsyncClient, monkeypatch):
    # Force the graceful "no GPU" path regardless of host hardware.
    monkeypatch.setattr(system_metrics, "gpu_metrics", lambda: None)

    resp = await client.get("/api/system/metrics")
    assert resp.status_code == 200
    body = resp.json()

    assert body["gpu"] is None
    for key in ("cpu", "ram", "disk", "workers", "uptime"):
        assert key in body
    assert {"used", "total", "pct"} <= set(body["ram"])
    assert {"active", "queued"} <= set(body["workers"])
    assert isinstance(body["cpu"]["model"], str)
    assert isinstance(body["uptime"], str)


@pytest.mark.asyncio
async def test_metrics_gpu_present(db, client: AsyncClient, monkeypatch):
    fake = {
        "model": "NVIDIA GeForce RTX 3070",
        "util": 42,
        "memUtil": 17,
        "vramUsed": 1_000_000,
        "vramTotal": 8_000_000,
        "temp": 55,
        "power": 120.0,
        "enc": 0,
        "dec": 11,
    }
    monkeypatch.setattr(system_metrics, "gpu_metrics", lambda: fake)

    body = (await client.get("/api/system/metrics")).json()
    assert body["gpu"]["model"] == "NVIDIA GeForce RTX 3070"
    assert body["gpu"]["util"] == 42
    assert body["gpu"]["dec"] == 11
    assert body["gpu"]["vramTotal"] == 8_000_000


@pytest.mark.asyncio
async def test_metrics_history_returns_points_rates_and_job_overlay(db, client: AsyncClient):
    now = datetime.now(UTC)
    db.add_all(
        [
            SystemMetricsSample(
                cpu={"avg": 10},
                gpu={"util": 20, "memUtil": 5, "enc": 0, "dec": 0},
                ram={"pct": 35},
                disk={"readBytes": 1_000, "writeBytes": 2_000},
                net={"bytesRecv": 4_000, "bytesSent": 5_000},
                active_jobs=[],
                created_at=now - timedelta(minutes=3),
            ),
            SystemMetricsSample(
                cpu={"avg": 25},
                gpu={"util": 35, "memUtil": 9, "enc": 4, "dec": 6},
                ram={"pct": 41},
                disk={"readBytes": 4_000, "writeBytes": 8_000},
                net={"bytesRecv": 10_000, "bytesSent": 8_000},
                active_jobs=[{"id": "job-1", "type": "system_noop"}],
                created_at=now - timedelta(minutes=2),
            ),
            SystemMetricsSample(
                cpu={"avg": 40},
                gpu={"util": 55, "memUtil": 15, "enc": 8, "dec": 12},
                ram={"pct": 48},
                disk={"readBytes": 10_000, "writeBytes": 14_000},
                net={"bytesRecv": 22_000, "bytesSent": 12_000},
                active_jobs=[{"id": "job-1", "type": "system_noop"}],
                created_at=now - timedelta(minutes=1),
            ),
        ]
    )
    job = Job(
        id="metrics-history-job",
        root_id="metrics-history-job",
        type="system_noop",
        request={},
        phase="terminal",
        outcome="succeeded",
        subject_snapshot={},
        trigger_kind="system",
        feature_area="system",
    )
    job.started_at = now - timedelta(minutes=2, seconds=30)
    job.terminal_at = now - timedelta(minutes=1, seconds=15)
    db.add(job)
    await db.commit()

    resp = await client.get(
        "/api/system/metrics/history", params={"window": "1h", "resolution": 10}
    )
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["points"]) == 3
    assert body["points"][-1]["gpu_dec"] == 12
    assert body["points"][1]["disk_read_bps"] is not None
    assert body["points"][1]["net_recv_bps"] is not None
    assert body["jobs"][0]["job_id"] == job.id
    assert body["jobs"][0]["label"] == "Health Check"


def test_fmt_uptime():
    assert system_metrics._fmt_uptime(90) == "1m"
    assert system_metrics._fmt_uptime(3 * 3600 + 5 * 60) == "3h 5m"
    assert system_metrics._fmt_uptime(2 * 86400 + 3 * 3600) == "2d 3h 0m"


@pytest.mark.asyncio
async def test_operations_snapshot_is_typed_bounded_and_payload_free(
    db, client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(
        system_metrics,
        "collect",
        lambda _path: {
            "cpu": {"model": "Synthetic CPU", "avg": 12, "temp": 40},
            "gpu": None,
            "gpus": [],
            "ram": {"used": 20, "total": 100, "pct": 20},
            "disk": {"used": 30, "total": 100, "pct": 30, "readBytes": 1, "writeBytes": 2},
            "net": {"bytesRecv": 3, "bytesSent": 4},
            "uptime": "1h 2m",
        },
    )

    async def queue_statistics(_db):
        return []

    monkeypatch.setattr(pgqueuer_gateway, "queue_statistics", queue_statistics)

    response = await client.get("/api/system/operations")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert body["node"]["cpu_model"] == "Synthetic CPU"
    assert set(body) == {
        "version",
        "generated_at",
        "node",
        "workers",
        "transport",
        "database",
        "events",
        "storage",
        "contracts",
    }
    serialized = response.text.lower()
    assert "payload" not in serialized
    assert "request" not in serialized
    assert "result" not in serialized

    schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"]["/api/system/operations"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/OperationsSnapshot"
    }


@pytest.mark.asyncio
async def test_operations_history_rejects_unbounded_window(client: AsyncClient):
    response = await client.get("/api/system/metrics/history", params={"window": "all"})
    assert response.status_code == 422
