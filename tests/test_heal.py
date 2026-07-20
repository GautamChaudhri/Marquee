"""Canonical poster-heal parent projection tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


@pytest.mark.asyncio
async def test_latest_poster_heal_summary_reads_parent_projection(db):
    from marquee.core.jobs.poster_summary import latest_poster_heal_summary
    from marquee.models import Job, JobBatch

    assert await latest_poster_heal_summary(db) is None
    terminal_at = datetime.now(UTC) - timedelta(minutes=5)
    db.add(
        Job(
            id="healjob",
            type="poster_heal",
            payload_version=1,
            request={
                "operation": "heal",
                "scope": "missing",
                "selection_count": 3,
                "unchanged_count": 8,
                "unsupported_count": 1,
            },
            phase="terminal",
            outcome="partially_succeeded",
            terminal_at=terminal_at,
            root_id="healjob",
            subject_snapshot={
                "version": 1,
                "kind": "aggregate_batch",
                "display_id": "batch:heal",
                "display_name": "Heal",
                "batch_type": "poster_heal",
                "child_count": 3,
                "sealed": True,
            },
        )
    )
    db.add(
        JobBatch(
            parent_job_id="healjob",
            mode="fixed",
            sealed=True,
            sealed_at=terminal_at,
            sealed_child_total=3,
            created_total=3,
            terminal_total=3,
            succeeded_total=2,
            failed_total=1,
        )
    )
    await db.commit()

    summary = await latest_poster_heal_summary(db)
    assert summary == {
        "last_run": terminal_at.isoformat(),
        "checked": 12,
        "restored": 2,
        "failed": 1,
        "unchanged": 8,
        "unsupported": 1,
    }
