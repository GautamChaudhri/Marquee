"""Host-telemetry samples — written by the lightweight background sampler.

Deliberately outside the Job/JobEvent system: these rows are written on a
fast timer (seconds) by ``marquee.core.system_metrics_sampler``, never by a
Job handler. See that module's docstring for why.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from marquee.database import Base


class SystemMetricsSample(Base):
    __tablename__ = "system_metrics_samples"
    __table_args__ = (Index("ix_system_metrics_samples_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cpu: Mapped[dict] = mapped_column(JSON, nullable=False)
    gpu: Mapped[dict | None] = mapped_column(JSON)
    ram: Mapped[dict] = mapped_column(JSON, nullable=False)
    disk: Mapped[dict] = mapped_column(JSON, nullable=False)
    net: Mapped[dict] = mapped_column(JSON, nullable=False)
    # [{"id": job.id, "type": job.type}, ...] for jobs in manager.ACTIVE at
    # sample time — lets a future history overlay correlate spikes to jobs
    # without per-process OS-level attribution.
    active_jobs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
