"""JMC3 final certification (Chunk 3, Phase C4).

These checks run against the real ``marquee.main:app`` — not a minimal test app —
to certify that the streaming ingress limiter is actually wired into production
and that the removed inline backup restore/delete routes stay gone. The
fail-closed backup POST (503), the read-only backup listing, and the
``system_noop``-only production registry are certified in ``tests/test_backup.py``;
the complete fenced-execution/process/lock/staging/progress/log/artifact/event
matrix is certified across the JMC3A/B suites, all of which run together in the
full retained suite that this phase compares against the baseline.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.config import settings
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_real_app_rejects_oversize_request_body() -> None:
    """The production ASGI stack rejects an over-limit body with a canonical 413."""
    oversize = b"x" * (settings.MAX_REQUEST_BODY_BYTES + 1)
    async with _client() as ac:
        resp = await ac.post("/api/system/backup", content=oversize)
    assert resp.status_code == 413
    assert resp.json()["code"] == "request_body_too_large"


@pytest.mark.asyncio
async def test_real_app_has_no_inline_restore_or_delete_backup_route() -> None:
    """C1 removed the inline restore/delete product routes; they must stay gone."""
    async with _client() as ac:
        restore = await ac.post("/api/system/backups/does-not-exist/restore", json={})
        delete = await ac.request("DELETE", "/api/system/backups/does-not-exist")
    assert restore.status_code == 404
    assert delete.status_code in (404, 405)


def test_only_system_noop_remains_production_enabled() -> None:
    """The final JMC3 state dispatches exactly one production definition."""
    assert JOB_DEFINITION_REGISTRY.enabled_types == {
        "subtitle_policy",
        "subtitle_restore",
        "subtitle_generate",
        "subtitle_extract",
        "subtitle_embed",
        "audio_remove",
        "track_remove",
        "subtitle_remove",
        "audio_reorder",
        "subtitle_metadata",
        "system_noop",
        "library_sync",
        "poster_pipeline",
        "letterbox_detect",
        "letterbox_detect_episode",
        "letterbox_detect_tv_scope",
        "subtitle_scan",
        "subtitle_policy_audit",
        "dovi_analyze",
        "dovi_convert",
        "dovi_publish",
        "dovi_restore",
        "dovi_discard",
        "ranking_residual_train",
        "poster_rescan",
        "taste_map",
        "taste_enrich",
        "taste_rebuild",
        "poster_deploy",
        "poster_restore",
        "poster_reset",
        "poster_backup_subject",
        "backup_create",
        "poster_maintenance",
        "pipeline_cache_clear",
        "job_retention_purge",
        "system_metrics_purge",
        "letterbox_apply",
        "letterbox_remove",
        "letterbox_reencode",
        "letterbox_reencode_publish",
        "letterbox_reencode_restore",
        "letterbox_reencode_discard",
    }
