"""Batch quick-delete regressions: rotated track uuids, sibling plans, stacking.

Covers the three mechanisms that made "remove these languages" silently drop
selections:
  1. Inventory rescans regenerate every SubtitleTrack uuid, and _build_argv
     silently filtered unknown ids — an audio+subtitle removal degraded to an
     audio-only remux that still reported success.
  2. supersede_planned_media_jobs cancelled ALL planned jobs for the same
     (operation, file) regardless of what they targeted, so a second language's
     plan cancelled the first.
  3. A plan confirmed while another mutation was queued/running on the same
     file was guaranteed to die at preflight with plan_stale.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from marquee.core.media_files import ResolvedMediaFile
from marquee.core.media_jobs import media_job_manager
from marquee.core.subtitles.mutation import PreflightError, _resolve_track_ids
from marquee.main import app
from marquee.models import Job, MediaFile, MediaJob


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _track(track_id, *, source="embedded", stream_index=None, external_path=None):
    return SimpleNamespace(
        id=track_id, source=source, stream_index=stream_index, external_path=external_path
    )


def _plan_with_before(tracks: list[dict]) -> dict:
    return {"before": {"tracks": tracks}, "after": {"tracks": []}}


# ---------------------------------------------------------------------------
# 1. _resolve_track_ids — uuid rotation survival + loud failure
# ---------------------------------------------------------------------------


def test_resolve_track_ids_passes_through_live_ids():
    by_id = {"new-1": _track("new-1", stream_index=2)}
    resolved = _resolve_track_ids(["new-1"], by_id, plan=None)
    assert resolved["new-1"] is by_id["new-1"]


def test_resolve_track_ids_remaps_rotated_embedded_uuid_by_stream_index():
    # Plan captured old uuids; a rescan rotated them but stream indexes are stable.
    by_id = {
        "new-1": _track("new-1", stream_index=2),
        "new-2": _track("new-2", stream_index=3),
    }
    plan = _plan_with_before(
        [
            {"id": "old-1", "source": "embedded", "stream_index": 2, "external_path": None},
            {"id": "old-2", "source": "embedded", "stream_index": 3, "external_path": None},
        ]
    )
    resolved = _resolve_track_ids(["old-1", "old-2"], by_id, plan)
    assert resolved["old-1"] is by_id["new-1"]
    assert resolved["old-2"] is by_id["new-2"]


def test_resolve_track_ids_remaps_rotated_external_uuid_by_path():
    by_id = {"new-x": _track("new-x", source="external", external_path="/m/movie.en.srt")}
    plan = _plan_with_before(
        [{"id": "old-x", "source": "external", "external_path": "/m/movie.en.srt"}]
    )
    resolved = _resolve_track_ids(["old-x"], by_id, plan)
    assert resolved["old-x"] is by_id["new-x"]


def test_resolve_track_ids_raises_plan_stale_instead_of_silently_filtering():
    by_id = {"new-1": _track("new-1", stream_index=2)}
    plan = _plan_with_before(
        [{"id": "old-gone", "source": "embedded", "stream_index": 9, "external_path": None}]
    )
    with pytest.raises(PreflightError) as exc:
        _resolve_track_ids(["old-gone"], by_id, plan)
    assert exc.value.code == "plan_stale"
    assert "old-gone" in str(exc.value)


def test_resolve_track_ids_without_plan_snapshot_fails_loudly():
    # Policy-driven removals carry no plan; unknown ids must still abort.
    with pytest.raises(PreflightError) as exc:
        _resolve_track_ids(["missing"], {}, plan=None)
    assert exc.value.code == "plan_stale"


# ---------------------------------------------------------------------------
# 2. supersede_planned_media_jobs — selection-aware
# ---------------------------------------------------------------------------


async def _media_file(db, key="radarr:mf:g1") -> MediaFile:
    media_file = MediaFile(source="radarr", source_key=key, path=f"/movies/{key}.mkv")
    db.add(media_file)
    await db.commit()
    return media_file


async def _planned_job(db, media_file_id, track_ids, *, operation="subtitle_remove") -> MediaJob:
    return await media_job_manager.create_job(
        db,
        operation=operation,
        media_file_id=media_file_id,
        request={"track_ids": track_ids, "audio_stream_indices": []},
        status="planned",
    )


async def test_supersede_keeps_sibling_plans_with_different_selections(db):
    media_file = await _media_file(db)
    eng = await _planned_job(db, media_file.id, ["sub-en-1", "sub-en-2"])
    fra = await _planned_job(db, media_file.id, ["sub-fr-1"])

    cancelled = await media_job_manager.supersede_planned_media_jobs(
        db,
        media_file_id=media_file.id,
        operation="subtitle_remove",
        request={"track_ids": ["sub-en-2", "sub-en-1"], "audio_stream_indices": []},
    )

    await db.refresh(eng)
    await db.refresh(fra)
    assert cancelled == [eng.job_id]
    assert eng.status == "cancelled"
    assert fra.status == "planned"  # different language selection survives


async def test_supersede_matches_selection_regardless_of_order(db):
    media_file = await _media_file(db, key="radarr:mf:g2")
    job = await _planned_job(db, media_file.id, ["a", "b", "c"])

    cancelled = await media_job_manager.supersede_planned_media_jobs(
        db,
        media_file_id=media_file.id,
        operation="subtitle_remove",
        request={"track_ids": ["c", "a", "b"], "audio_stream_indices": []},
    )
    await db.refresh(job)
    assert cancelled == [job.job_id]
    assert job.status == "cancelled"


async def test_supersede_without_request_cancels_all_planned(db):
    media_file = await _media_file(db, key="radarr:mf:g3")
    one = await _planned_job(db, media_file.id, ["x"])
    two = await _planned_job(db, media_file.id, ["y"])

    cancelled = await media_job_manager.supersede_planned_media_jobs(
        db, media_file_id=media_file.id, operation="subtitle_remove"
    )
    await db.refresh(one)
    await db.refresh(two)
    assert set(cancelled) == {one.job_id, two.job_id}
    assert one.status == "cancelled"
    assert two.status == "cancelled"


# ---------------------------------------------------------------------------
# 3. Plan endpoint — mutation_pending guard + sibling plans through the API
# ---------------------------------------------------------------------------


def _wire_plan_route_fakes(monkeypatch, media_file):
    resolved = ResolvedMediaFile(
        media_file_id=media_file.id,
        source="radarr",
        path=media_file.path,
        size_bytes=4,
        mtime_ns=1,
        st_nlink=1,
        signature="sig-language-removal",
        container="mkv",
        movie_id=None,
    )

    async def fake_resolve_media_file(_db, _media_file_id):
        return resolved

    async def fake_inventory(_db, _media_file_id):
        return {
            "inventory_id": 1,
            "tracks": [],
            "audio_streams": [],
            "coverage": {},
            "container_family": "mkv",
            "capabilities": {"can_remove": True},
        }

    async def fake_build_plan(_db, _resolved, _inventory, **_kwargs):
        return {
            "operation": "subtitle_remove",
            "before": {"tracks": [], "audio_streams": []},
            "after": {"tracks": [], "audio_streams": []},
            "warnings": [],
            "capabilities": {"can_execute": True},
        }

    monkeypatch.setattr("marquee.api.routes.subtitles._require_ffprobe", lambda: None)
    monkeypatch.setattr("marquee.api.routes.subtitles.resolve_media_file", fake_resolve_media_file)
    monkeypatch.setattr("marquee.api.routes.subtitles.service.get_inventory_dict", fake_inventory)
    monkeypatch.setattr("marquee.api.routes.subtitles.mutation.build_plan", fake_build_plan)


async def test_create_plan_rejected_while_mutation_pending_on_same_file(
    db, client, monkeypatch
):
    media_file = await _media_file(db, key="radarr:mf:g4")
    _wire_plan_route_fakes(monkeypatch, media_file)

    queued = await _planned_job(db, media_file.id, ["sub-en-1"])
    queued.status = "queued"  # confirmed and waiting for its media_write slot
    await db.commit()

    resp = await client.post(
        f"/api/media-files/{media_file.id}/subtitle-plans",
        json={"operation": "subtitle_remove", "track_ids": ["sub-fr-1"]},
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "mutation_pending"
    assert detail["pending_job_id"] == queued.job_id


async def test_sibling_language_plans_coexist_through_the_api(db, client, monkeypatch):
    media_file = await _media_file(db, key="radarr:mf:g5")
    _wire_plan_route_fakes(monkeypatch, media_file)

    first = await client.post(
        f"/api/media-files/{media_file.id}/subtitle-plans",
        json={"operation": "subtitle_remove", "track_ids": ["sub-en-1", "sub-en-2"]},
    )
    second = await client.post(
        f"/api/media-files/{media_file.id}/subtitle-plans",
        json={"operation": "subtitle_remove", "track_ids": ["sub-fr-1"]},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    first_id = first.json()["job_id"]
    second_id = second.json()["job_id"]

    assert (await db.get(MediaJob, first_id)).status == "planned"
    assert (await db.get(MediaJob, second_id)).status == "planned"
    generic_statuses = {
        row.payload.get("media_job_id"): row.status
        for row in (await db.execute(select(Job).where(Job.type == "subtitle_remove")))
        .scalars()
        .all()
        if isinstance(row.payload, dict)
    }
    assert generic_statuses[first_id] == "planned"
    assert generic_statuses[second_id] == "planned"
