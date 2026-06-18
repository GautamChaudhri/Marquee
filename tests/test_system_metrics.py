"""Tests for GET /api/system/metrics (frontend G1 dashboard telemetry)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from marquee.core import system_metrics
from marquee.main import app


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
        "model": "NVIDIA GeForce RTX 3070", "util": 42,
        "vramUsed": 1_000_000, "vramTotal": 8_000_000,
        "temp": 55, "power": 120.0, "enc": 0,
    }
    monkeypatch.setattr(system_metrics, "gpu_metrics", lambda: fake)

    body = (await client.get("/api/system/metrics")).json()
    assert body["gpu"]["model"] == "NVIDIA GeForce RTX 3070"
    assert body["gpu"]["util"] == 42
    assert body["gpu"]["vramTotal"] == 8_000_000


def test_fmt_uptime():
    assert system_metrics._fmt_uptime(90) == "1m"
    assert system_metrics._fmt_uptime(3 * 3600 + 5 * 60) == "3h 5m"
    assert system_metrics._fmt_uptime(2 * 86400 + 3 * 3600) == "2d 3h 0m"
