"""Typed bounded contracts for the secondary Projection Room Operations view."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class OperationsNode(BaseModel):
    cpu_model: str
    cpu_percent: float | None = None
    cpu_temperature_c: float | None = None
    ram_percent: float | None = None
    ram_used_bytes: int | None = None
    ram_total_bytes: int | None = None
    gpu_model: str | None = None
    gpu_percent: float | None = None
    gpu_memory_percent: float | None = None
    network_received_bytes: int | None = None
    network_sent_bytes: int | None = None
    uptime: str


class OperationsWorkers(BaseModel):
    active: int
    queued: int
    supervisor_available: bool
    listener_healthy: bool


class OperationsTransport(BaseModel):
    picked: int
    held_failed: int
    oldest_eligible_age_seconds: float | None = None


class OperationsConnectionBudget(BaseModel):
    configured: int
    maximum: int
    within_budget: bool


class OperationsDatabase(BaseModel):
    observed_connections: int
    connection_roles: dict[str, int]
    pool_size: int | None = None
    pool_checked_in: int | None = None
    pool_checked_out: int | None = None
    pool_overflow: int | None = None
    budget: OperationsConnectionBudget


class OperationsEvents(BaseModel):
    listener_healthy: bool
    source: str
    last_observed_event_at: datetime | None = None


class OperationsStorage(BaseModel):
    poster_cache_items: int
    poster_cache_bytes: int
    disk_percent: float | None = None
    disk_used_bytes: int | None = None
    disk_total_bytes: int | None = None


class OperationsSchemaContract(BaseModel):
    component: str
    expected_version: int
    durability: str
    catalog_fingerprint: str | None = None


class OperationsSnapshot(BaseModel):
    version: Literal[1] = 1
    generated_at: datetime
    node: OperationsNode
    workers: OperationsWorkers
    transport: OperationsTransport
    database: OperationsDatabase
    events: OperationsEvents
    storage: OperationsStorage
    contracts: list[OperationsSchemaContract] = Field(default_factory=list)


class OperationsHistoryPoint(BaseModel):
    ts: datetime
    cpu_avg: float | None = None
    gpu_util: float | None = None
    gpu_mem: float | None = None
    gpu_enc: float | None = None
    gpu_dec: float | None = None
    ram_pct: float | None = None
    disk_read_bps: float | None = None
    disk_write_bps: float | None = None
    net_recv_bps: float | None = None
    net_sent_bps: float | None = None
    active_jobs: int


class OperationsHistoryJob(BaseModel):
    job_id: str
    type: str
    label: str
    status: str
    subject: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class OperationsHistoryResponse(BaseModel):
    window: Literal["15m", "1h", "6h", "24h"]
    start_at: datetime
    end_at: datetime
    points: list[OperationsHistoryPoint]
    jobs: list[OperationsHistoryJob]
    jobs_truncated: bool
