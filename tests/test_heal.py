"""Heal scan behavior — recent-deploy grace window and restore accounting.

The scan runs in the worker process while deploys happen in the API process,
so a time window on ``poster_deployed_at`` (not a lock) is what prevents the
scan from clobbering a poster deployed moments ago.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from marquee.core import heal
from marquee.core.poster_service import RestoreResult
from marquee.models import Movie


@pytest.mark.asyncio
async def test_heal_scan_grace_window_and_counts(db, tmp_path, monkeypatch):
    restored_titles: list[str] = []

    async def fake_restore(_db, movie, *, source="heal", **_kw):
        restored_titles.append(movie.title)
        return RestoreResult(restored=True, source="cache", path="x")

    monkeypatch.setattr(heal.poster_service, "restore", fake_restore)

    intact = tmp_path / "intact.jpg"
    intact.write_bytes(b"jpeg")
    now = datetime.now(UTC)
    db.add_all(
        [
            # Deployed seconds ago + file missing → grace window skips it.
            Movie(
                title="fresh",
                year=2001,
                folder_path="/m/fresh",
                poster_path=str(tmp_path / "gone1.jpg"),
                poster_deployed_at=now,
            ),
            # Old deploy + file missing → restored.
            Movie(
                title="old",
                year=2002,
                folder_path="/m/old",
                poster_path=str(tmp_path / "gone2.jpg"),
                poster_deployed_at=now - timedelta(hours=1),
            ),
            # Never recorded a deploy time + file missing → restored.
            Movie(
                title="undated",
                year=2003,
                folder_path="/m/undated",
                poster_path=str(tmp_path / "gone3.jpg"),
                poster_deployed_at=None,
            ),
            # File still on disk → checked, not restored.
            Movie(
                title="intact",
                year=2004,
                folder_path="/m/intact",
                poster_path=str(intact),
                poster_deployed_at=now - timedelta(hours=1),
            ),
            # No poster at all → not scanned.
            Movie(title="posterless", year=2005, folder_path="/m/posterless"),
        ]
    )
    await db.commit()

    result = await heal.heal_scan()

    assert sorted(restored_titles) == ["old", "undated"]
    assert result == {
        "checked": 3,
        "restored": 2,
        "failed": 0,
        "by_type": {
            "movie": {"checked": 3, "restored": 2, "failed": 0},
            "series": {"checked": 0, "restored": 0, "failed": 0},
            "season": {"checked": 0, "restored": 0, "failed": 0},
        },
    }


@pytest.mark.asyncio
async def test_heal_scan_counts_failures(db, tmp_path, monkeypatch):
    async def fake_restore(_db, movie, *, source="heal", **_kw):
        return RestoreResult(restored=False, source="none", error="nope")

    monkeypatch.setattr(heal.poster_service, "restore", fake_restore)

    db.add(
        Movie(
            title="broken",
            year=2000,
            folder_path="/m/broken",
            poster_path=str(tmp_path / "gone.jpg"),
            poster_deployed_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    await db.commit()

    result = await heal.heal_scan()
    assert result == {
        "checked": 1,
        "restored": 0,
        "failed": 1,
        "by_type": {
            "movie": {"checked": 1, "restored": 0, "failed": 1},
            "series": {"checked": 0, "restored": 0, "failed": 0},
            "season": {"checked": 0, "restored": 0, "failed": 0},
        },
    }


@pytest.mark.asyncio
async def test_latest_heal_summary_reads_job_rows(db):
    from marquee.core.heal import latest_heal_summary
    from marquee.models import Job

    assert await latest_heal_summary(db) is None

    older = datetime.now(UTC) - timedelta(hours=2)
    newer = datetime.now(UTC) - timedelta(minutes=5)
    db.add_all(
        [
            Job(
                id="healjob1",
                type="poster_heal",
                status="succeeded",
                finished_at=older,
                result={"checked": 10, "restored": 1, "failed": 0},
            ),
            Job(
                id="healjob2",
                type="poster_heal",
                status="succeeded",
                finished_at=newer,
                result={"checked": 12, "restored": 2, "failed": 1},
            ),
            Job(id="healjob3", type="poster_heal", status="failed", finished_at=None),
            Job(id="otherjob", type="poster_pipeline", status="succeeded", finished_at=newer),
        ]
    )
    await db.commit()

    summary = await latest_heal_summary(db)
    assert summary is not None
    assert summary["checked"] == 12
    assert summary["restored"] == 2
    assert summary["failed"] == 1
    assert summary["last_run"] is not None
