"""JMC4B B5 — canonical, read-only Dolby Vision analysis guards."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import Queries, RetryRequested

from marquee.api.routes import hdr
from marquee.api.routes.hdr import (
    analyze_dovi_batch,
    analyze_movie_dovi,
    analyze_tv_dovi_batch,
    analyze_tv_show_dovi,
)
from marquee.core.jobs.handlers_dovi import (
    _dovi_tool_summary,
    _parse_probe,
    execute_dovi_analyze,
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


def _probe() -> dict:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "pix_fmt": "yuv420p10le",
                "color_transfer": "smpte2084",
                "color_primaries": "bt2020",
                "color_space": "bt2020nc",
                "side_data_list": [
                    {
                        "side_data_type": "DOVI configuration record",
                        "dv_profile": 7,
                        "dv_level": 6,
                        "rpu_present": 1,
                        "el_present": 1,
                        "bl_present": 1,
                        "dv_bl_signal_compatibility_id": 1,
                    }
                ],
            }
        ]
    }


def test_dovi_definition_is_enabled_read_only_with_ticketless_parent() -> None:
    definition = JOB_DEFINITION_REGISTRY.get("dovi_analyze")
    assert definition.enabled is True
    assert definition.execution_class.value == "media_read"
    assert definition.effect_safety.value == "read_only"
    assert definition.progress_policy.stage_keys == {"probing", "analyzing", "validating"}
    parent = JOB_DEFINITION_REGISTRY.get("dovi_analyze_batch")
    assert parent.parent_policy is not None
    assert parent.child_job_types == {"dovi_analyze"}


def test_dovi_probe_reports_read_only_typed_metadata() -> None:
    result = _parse_probe(
        _probe(), analysis_depth="deep", tool_summary="Profile 7\nEL type: FEL", tool_warning=None
    )

    assert result["status"] == "analyzed"
    assert result["dovi_profile"] == 7
    assert result["el_type"] == "FEL"
    assert result["source_hdr_base"] == "hdr10"
    assert result["source_bit_depth"] == 10
    assert result["rpu_present"] is True
    assert result["bl_present"] is True
    assert result["analysis_supported"] is True


def test_canonical_dovi_handler_has_no_legacy_or_media_mutation_path() -> None:
    source = inspect.getsource(__import__("marquee.core.jobs.handlers_dovi", fromlist=["*"]))
    for forbidden in (
        "job_manager",
        "media_job_manager",
        "cancel_registry",
        "subprocess",
        "dovi_conversion",
        "dovi_convert",
        "media_write",
        "publish_atomic",
        "backup",
        "replacement",
        "ffmpeg",
    ):
        assert forbidden not in source


@pytest.mark.asyncio
async def test_deep_tool_unavailability_uses_definition_retry() -> None:
    class UnavailableLauncher:
        async def launch(self, *_args, **_kwargs):
            from marquee.core.jobs.process_launcher import ProcessLaunchError

            raise ProcessLaunchError("dovi_tool is unavailable")

    with pytest.raises(RetryRequested) as raised:
        await _dovi_tool_summary(SimpleNamespace(process_launcher=UnavailableLauncher()), "/media/test.hevc")

    assert raised.value.reason == "dovi_tool is temporarily unavailable"


@pytest.mark.asyncio
async def test_stale_source_signature_returns_no_change_without_probe(monkeypatch) -> None:
    async def _source(*_args, **_kwargs):
        return SimpleNamespace(path="/media/test.mkv", signature="b" * 40)

    async def _unexpected(*_args, **_kwargs):
        raise AssertionError("stale source must not be probed")

    monkeypatch.setattr("marquee.core.jobs.handlers_dovi._load_source", _source)
    monkeypatch.setattr("marquee.core.jobs.handlers_dovi._launch_json", _unexpected)
    context = SimpleNamespace(
        cancellation=SimpleNamespace(cancel_called=False),
        request={
            "media_file_id": 7,
            "movie_id": 4,
            "source_signature": "a" * 40,
            "analysis_depth": "standard",
        },
        subject={"media_file_id": 7},
    )

    result = await execute_dovi_analyze(context)

    assert result == {
        "outcome": "no_change",
        "summary": {"media_file_id": 7, "reason": "source_changed_before_analysis"},
    }


@pytest.mark.asyncio
async def test_dovi_route_submits_a_canonical_media_read_job(client, db, monkeypatch) -> None:
    movie = Movie(
        title="Canonical DoVi",
        year=2026,
        folder_path="/media",
        movie_file_path="/media/canonical-dovi.mkv",
        tmdb_id=876543,
        has_dv=True,
    )
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    async def _snapshot(_db, media_file, **_kwargs):
        return {
            "media_file_id": media_file.id,
            "movie_id": movie.id,
            "episode_id": None,
            "source_signature": "a" * 40,
            "source_codec": None,
            "source_hdr_type": "DV HDR10",
            "analysis_depth": "standard",
        }

    monkeypatch.setattr(hdr, "_dovi_request_snapshot", _snapshot)
    response = await client.post(f"/api/hdr/{movie.id}/analyze")

    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"job_id", "disposition", "phase", "snapshot_url", "detail_url"}
    job = await db.get(Job, body["job_id"])
    assert job is not None
    assert job.type == "dovi_analyze"
    assert job.plan["entrypoint"] == "media_read"
    assert job.plan["effect_safety"] == "read_only"
    assert job.subject_kind == "media_file"
    assert job.request["source_signature"] == "a" * 40


def test_migrated_dovi_routes_do_not_use_legacy_lifecycle() -> None:
    for route in (
        analyze_movie_dovi,
        analyze_dovi_batch,
        analyze_tv_show_dovi,
        analyze_tv_dovi_batch,
    ):
        source = inspect.getsource(route)
        assert "job_manager" not in source
        assert "create_batch" not in source
        assert "binaries.resolve" not in source
