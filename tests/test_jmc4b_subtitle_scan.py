"""JMC4B B3 — subtitle_scan definition and read-only handler behavior."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from marquee.core.jobs.handlers_subtitles import execute_subtitle_scan
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.database import _get_session_factory
from marquee.models import Job, MediaFile, SubtitleInventory


def test_subtitle_scan_definition_is_enabled_read_only_media_read() -> None:
    definition = JOB_DEFINITION_REGISTRY.get("subtitle_scan")
    assert definition.enabled is True
    assert definition.execution_class.value == "media_read"
    assert definition.effect_safety.value == "read_only"
    assert definition.progress_policy.strategy.value == "indeterminate"
    assert definition.progress_policy.stage_keys == {"probing", "inventorying"}
    assert JOB_DEFINITION_REGISTRY.for_dispatch("subtitle_scan", entrypoint="media_read").enabled


@pytest.mark.asyncio
async def test_subtitle_scan_no_change_when_signature_matches(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_file = MediaFile(source="radarr", source_key="mf-scan-1", path="/media/x.mkv")
    db.add(media_file)
    await db.commit()
    db.add(
        SubtitleInventory(
            media_file_id=media_file.id,
            file_signature="sig-1",
            error=None,
            container="matroska",
        )
    )
    await db.commit()

    async def _fake_resolve(_session, _media_file_id):
        return SimpleNamespace(
            media_file_id=media_file.id,
            path="/media/x.mkv",
            signature="sig-1",
            container="matroska",
        )

    monkeypatch.setattr(
        "marquee.core.jobs.handlers_subtitles.resolve_media_file", _fake_resolve
    )
    context = SimpleNamespace(
        cancellation=SimpleNamespace(cancel_called=False),
        subject={"media_file_id": media_file.id},
        request={},
        session_factory=_get_session_factory(),
    )
    result = await execute_subtitle_scan(context)  # type: ignore[arg-type]
    assert result["outcome"] == "no_change"
    assert result["summary"]["reason"] == "inventory_up_to_date"
    assert result["summary"]["media_file_id"] == media_file.id


@pytest.mark.asyncio
async def test_subtitle_scan_force_bypasses_no_change(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force=True must not short-circuit on a matching signature (it proceeds to probe)."""
    media_file = MediaFile(source="radarr", source_key="mf-scan-2", path="/media/y.mkv")
    db.add(media_file)
    await db.commit()
    db.add(
        SubtitleInventory(
            media_file_id=media_file.id, file_signature="sig-2", error=None, container="matroska"
        )
    )
    await db.commit()

    async def _fake_resolve(_session, _media_file_id):
        return SimpleNamespace(
            media_file_id=media_file.id, path="/media/y.mkv", signature="sig-2", container="matroska"
        )

    async def _fake_launch(_context, tool, _args):
        raise AssertionError(f"probe should have run for {tool}")

    monkeypatch.setattr(
        "marquee.core.jobs.handlers_subtitles.resolve_media_file", _fake_resolve
    )
    # With force=True the no_change branch is skipped, so the handler reaches the probe stage.
    monkeypatch.setattr(
        "marquee.core.jobs.handlers_subtitles._run_tool_json", _fake_launch
    )
    context = SimpleNamespace(
        cancellation=SimpleNamespace(cancel_called=False),
        subject={"media_file_id": media_file.id},
        request={"force": True},
        session_factory=_get_session_factory(),
        delivery=SimpleNamespace(canonical_job_id="job-1"),
        attempt=SimpleNamespace(attempt_id=1, fence_token=1),
    )
    with pytest.raises(AssertionError, match="probe should have run"):
        await execute_subtitle_scan(context)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_deep_scan_schedule_produces_no_change_batch(db) -> None:
    """The activated hourly deep-scan schedule creates an audio_subs_deep_scan fixed batch."""
    from datetime import UTC, datetime

    from marquee.core.jobs.schedules import (
        PRODUCTION_SCHEDULE_CATALOG,
        ScheduleConfiguration,
        ScheduleDiagnostics,
        submit_schedule_occurrence,
    )

    definition = next(
        item for item in PRODUCTION_SCHEDULE_CATALOG if item.key == "audio-subs-deep-scan"
    )
    assert definition.batch_producer is not None

    config = ScheduleConfiguration(
        revision=1,
        sync_interval_minutes=15,
        audio_subs_deep_scan_enabled=True,
        audio_subs_deep_scan_hour=3,
        audio_subs_deep_scan_batch=200,
    )
    schedule = SimpleNamespace(updated=datetime(2026, 7, 14, 3, 0, 0, tzinfo=UTC))
    result = await submit_schedule_occurrence(
        definition,
        schedule,
        configuration_loader=lambda: config,
        diagnostics=ScheduleDiagnostics(),
    )
    assert result is not None
    job = await db.get(Job, result.job_id)
    assert job is not None
    assert job.type == "audio_subs_deep_scan"
    assert job.trigger_kind == "schedule"
    # empty library -> sealed empty batch -> no_change parent, no transport ticket
    assert job.outcome == "no_change"
    assert job.pgq_job_id is None
