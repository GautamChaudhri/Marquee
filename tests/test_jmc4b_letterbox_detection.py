"""JMC4B B4 — canonical read-only letterbox detection guards."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import Queries, RetryRequested

from marquee.api.routes.letterbox import detect_batch, detect_one, detect_tv_batch, detect_tv_series
from marquee.core.jobs.handlers_letterbox import (
    _frozen_detection_config,
    _launch_json,
    execute_letterbox_detect,
    execute_letterbox_detect_episode,
    execute_letterbox_detect_tv_scope,
)
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.database import _get_engine
from marquee.main import app
from marquee.models import Job, Movie


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(autouse=True)
async def installed_pgqueuer(db) -> Queries:
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queries = Queries.from_asyncpg_connection(raw.driver_connection)
        await queries.install()
        try:
            yield queries
        finally:
            await db.rollback()
            await queries.uninstall()


def test_letterbox_detection_definitions_are_enabled_read_only_and_ticketless_parents() -> None:
    for job_type in (
        "letterbox_detect",
        "letterbox_detect_episode",
        "letterbox_detect_tv_scope",
    ):
        definition = JOB_DEFINITION_REGISTRY.get(job_type)
        assert definition.enabled is True
        assert definition.execution_class.value == "media_read"
        assert definition.effect_safety.value == "read_only"
        assert definition.progress_policy.stage_keys == {"probing", "sampling", "validating"}
    assert JOB_DEFINITION_REGISTRY.get("letterbox_detect_batch").parent_policy is not None
    assert JOB_DEFINITION_REGISTRY.get("letterbox_detect_tv_batch").child_job_types == {
        "letterbox_detect_tv_scope"
    }


def test_canonical_letterbox_handlers_have_no_legacy_or_media_mutation_path() -> None:
    source = inspect.getsource(
        __import__("marquee.core.jobs.handlers_letterbox", fromlist=["*"])
    )
    for forbidden in (
        "letterbox_manager",
        "job_manager",
        "subprocess",
        "mkvpropedit",
        "reencode",
        "letterbox_service",
        "media_write",
        "LETTERBOX_AUTO_APPLY_HIGH",
    ):
        assert forbidden not in source


@pytest.mark.asyncio
async def test_movie_and_episode_handlers_only_store_derived_observations(monkeypatch) -> None:
    observation = {
        "status": "candidate",
        "confidence": "high",
        "source_width": 1920,
        "source_height": 1080,
        "recommended_crop_top": 138,
        "recommended_crop_bottom": 138,
        "aspect_label": "2.40:1",
        "detect_method": "cropdetect",
        "samples_json": "[]",
        "error": None,
        "variable_ar": False,
        "variable_ar_note": None,
        "sample_count": 3,
        "sampling_scope": "movie_standard",
        "warnings": [],
    }
    stored: list[tuple[str, list[int]]] = []

    async def _observe(*_args, **_kwargs):
        return observation

    async def _store(_context, *, media_type, subject_ids, observation):
        stored.append((media_type, subject_ids))
        return True

    monkeypatch.setattr("marquee.core.jobs.handlers_letterbox._observe_media", _observe)
    monkeypatch.setattr("marquee.core.jobs.handlers_letterbox._store_observation", _store)
    context = SimpleNamespace(
        subject={"media_file_id": 7},
        request={"movie_id": 4, "media_file_id": 7, "thorough": False},
    )
    movie = await execute_letterbox_detect(context)
    context.request = {"media_file_id": 7, "episode_ids": [8, 9], "thorough": False}
    episode = await execute_letterbox_detect_episode(context)

    assert movie["summary"]["status"] == "candidate"
    assert episode["summary"]["episode_ids"] == [8, 9]
    assert stored == [("movie", [4]), ("episode", [8, 9])]
    assert inspect.iscoroutinefunction(execute_letterbox_detect_tv_scope)


def test_letterbox_detection_uses_the_request_snapshot_not_live_configuration() -> None:
    context = SimpleNamespace(
        request={
            "detection_config": {
                "method": "trim",
                "cropdetect_limit": 37,
                "tv_quick_windows": 6,
            }
        },
        configuration={"LETTERBOX_CROPDETECT_LIMIT": 999},
    )

    config = _frozen_detection_config(context)

    assert config.LETTERBOX_DETECT_METHOD == "trim"
    assert config.LETTERBOX_CROPDETECT_LIMIT == 37
    assert config.LETTERBOX_TV_QUICK_WINDOWS == 6


@pytest.mark.asyncio
async def test_unavailable_probe_requests_definition_retry() -> None:
    class UnavailableLauncher:
        async def launch(self, *_args, **_kwargs):
            from marquee.core.jobs.process_launcher import ProcessLaunchError

            raise ProcessLaunchError("ffprobe is unavailable")

    with pytest.raises(RetryRequested) as raised:
        await _launch_json(SimpleNamespace(process_launcher=UnavailableLauncher()), "ffprobe", [])

    assert raised.value.reason == "ffprobe is temporarily unavailable"


@pytest.mark.asyncio
async def test_movie_detect_route_submits_canonical_read_only_job(client, db) -> None:
    movie = Movie(
        title="Canonical detect",
        year=2026,
        folder_path="/media",
        movie_file_path="/media/canonical.mkv",
        tmdb_id=987654,
    )
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    response = await client.post(f"/api/letterbox/movies/{movie.id}/detect")

    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"job_id", "disposition", "phase", "snapshot_url", "detail_url"}
    job = await db.get(Job, body["job_id"])
    assert job is not None
    assert job.type == "letterbox_detect"
    assert job.plan["entrypoint"] == "media_read"
    assert job.plan["effect_safety"] == "read_only"
    assert job.request["movie_id"] == movie.id
    assert job.request["media_file_id"] == int(job.subject_snapshot["media_file_id"])
    assert job.request["detection_config"]["method"] == "cropdetect"


def test_migrated_routes_do_not_use_legacy_detect_lifecycle() -> None:
    for route in (detect_one, detect_batch, detect_tv_batch, detect_tv_series):
        source = inspect.getsource(route)
        assert "job_manager" not in source
        assert "_require_ffmpeg" not in source
        assert "create_batch" not in source
