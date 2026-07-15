"""B5 public planned/confirmed mutation routes over generated confined media."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import Queries
from sqlalchemy import func, select

from marquee.database import _get_engine
from marquee.main import app
from marquee.models import Job, JobDispatch, Movie, SubtitlePolicy
from tests.support.jmc5b_harness import inventory_of, media_file_row
from tests.support.media_fixtures import DEFAULT_AUDIO, DEFAULT_SUBTITLES, build_mkv


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


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as value:
        yield value


@pytest.mark.asyncio
async def test_route_plans_without_ticket_then_confirms_exactly_once(
    db,
    client: AsyncClient,
    tmp_path: Path,
    jmc5b_media_roots,
) -> None:
    source = build_mkv(
        tmp_path, "route-plan.mkv", audio=DEFAULT_AUDIO, subtitles=DEFAULT_SUBTITLES
    )
    media_file = await media_file_row(db, source)
    await db.commit()
    inventory = inventory_of(source)
    subtitle = next(entry for entry in inventory.entries if entry.facts.kind == "subtitle")
    selector = {
        "track_key": subtitle.track_key,
        "facts": subtitle.facts.model_dump(mode="json"),
        "inventory_signature": inventory.signature,
        "stream_index_hint": subtitle.stream_index,
        "tool_track_id_hint": subtitle.tool_track_id,
    }

    response = await client.post(
        f"/api/media-files/{media_file.id}/subtitle-plans",
        headers={"Idempotency-Key": "subtitle_remove:route-remove-one"},
        json={
            "operation": "subtitle_remove",
            "request": {"media_file_id": media_file.id, "selectors": [selector]},
        },
    )
    assert response.status_code == 201, response.text
    planned = response.json()
    assert planned["phase"] == "planned"
    assert planned["input_signature"] == inventory.signature
    assert len(planned["plan_version"]) == 64

    db.expire_all()
    job = await db.get(Job, planned["job_id"])
    assert job is not None
    assert job.phase == "planned"
    assert job.dispatch_generation == 0
    assert await db.scalar(
        select(func.count()).select_from(JobDispatch).where(JobDispatch.job_id == job.id)
    ) == 0

    confirmation = {
        "expected_plan_version": planned["plan_version"],
        "expected_configuration_version": planned["configuration_version"],
    }
    first = await client.post(planned["confirmation_url"], json=confirmation)
    second = await client.post(planned["confirmation_url"], json=confirmation)
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text

    db.expire_all()
    job = await db.get(Job, planned["job_id"])
    assert job is not None
    assert job.phase == "queued"
    assert job.dispatch_generation == 1
    assert await db.scalar(
        select(func.count()).select_from(JobDispatch).where(JobDispatch.job_id == job.id)
    ) == 1


@pytest.mark.asyncio
async def test_confirmation_rejects_source_changed_after_plan(
    db,
    client: AsyncClient,
    tmp_path: Path,
    jmc5b_media_roots,
) -> None:
    source = build_mkv(
        tmp_path, "route-stale.mkv", audio=DEFAULT_AUDIO, subtitles=DEFAULT_SUBTITLES
    )
    media_file = await media_file_row(db, source)
    await db.commit()
    inventory = inventory_of(source)
    subtitle = next(entry for entry in inventory.entries if entry.facts.kind == "subtitle")
    response = await client.post(
        f"/api/media-files/{media_file.id}/subtitle-plans",
        headers={"Idempotency-Key": "subtitle_extract:route-stale-one"},
        json={
            "operation": "subtitle_extract",
            "request": {
                "media_file_id": media_file.id,
                "selector": {
                    "track_key": subtitle.track_key,
                    "facts": subtitle.facts.model_dump(mode="json"),
                    "inventory_signature": inventory.signature,
                    "stream_index_hint": subtitle.stream_index,
                    "tool_track_id_hint": subtitle.tool_track_id,
                },
            },
        },
    )
    assert response.status_code == 201, response.text
    planned = response.json()
    source.write_bytes(source.read_bytes() + b"changed")

    confirmation = await client.post(
        planned["confirmation_url"],
        json={
            "expected_plan_version": planned["plan_version"],
            "expected_configuration_version": planned["configuration_version"],
        },
    )
    assert confirmation.status_code == 409
    assert confirmation.json()["detail"]["code"] == "signature_changed"

    db.expire_all()
    job = await db.get(Job, planned["job_id"])
    assert job is not None
    assert job.phase == "planned"
    assert job.dispatch_generation == 0


@pytest.mark.asyncio
async def test_generation_route_freezes_embed_request_without_contacting_provider(
    db,
    client: AsyncClient,
    tmp_path: Path,
    jmc5b_media_roots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_mkv(
        tmp_path, "route-generate.mkv", audio=DEFAULT_AUDIO, subtitles=DEFAULT_SUBTITLES
    )
    media_file = await media_file_row(db, source)
    await db.commit()
    monkeypatch.setattr(
        "marquee.api.routes.subtitle_generators.generation.validate_generation_request",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "marquee.api.routes.subtitle_generators.subtitle_settings.SUBGEN_DEPLOYMENT",
        "embedded",
    )

    response = await client.post(
        f"/api/media-files/{media_file.id}/subtitle-generations",
        headers={"Idempotency-Key": "subtitle_generate:route-embed-one"},
        json={"language_hint": "eng", "output": "embedded", "task": "transcribe"},
    )
    assert response.status_code == 202, response.text
    planned = response.json()
    assert planned["phase"] == "planned"

    db.expire_all()
    job = await db.get(Job, planned["job_id"])
    assert job is not None
    assert job.request["publish"] == "embed"
    assert job.request["task"] == "transcribe"
    assert job.request["source_selector"]["facts"]["kind"] == "audio"
    assert job.dispatch_generation == 0


@pytest.mark.asyncio
async def test_policy_apply_route_seals_frozen_typed_child_plan(
    db,
    client: AsyncClient,
    tmp_path: Path,
    jmc5b_media_roots,
) -> None:
    source = build_mkv(
        tmp_path, "route-policy.mkv", audio=DEFAULT_AUDIO, subtitles=DEFAULT_SUBTITLES
    )
    movie = Movie(title="Policy fixture", year=2026, folder_path=str(tmp_path))
    policy = SubtitlePolicy(
        name="Remove French",
        mode="blocklist",
        languages_json='["fr"]',
        protect_forced=False,
        protect_default=True,
        protect_last_full_dialogue=True,
        include_external=False,
    )
    db.add_all((movie, policy))
    await db.flush()
    media_file = await media_file_row(db, source)
    media_file.movie_id = movie.id
    policy_revision = policy.revision
    await db.commit()

    response = await client.post(
        f"/api/subtitle-policies/{policy.id}/apply",
        headers={"Idempotency-Key": "subtitle_policy_batch:route-policy-one"},
        json={"movie_ids": [movie.id]},
    )
    assert response.status_code == 202, response.text
    submitted = response.json()

    db.expire_all()
    parent = await db.get(Job, submitted["job_id"])
    assert parent is not None
    assert parent.type == "subtitle_policy_batch"
    assert parent.pgq_job_id is None
    child = await db.scalar(select(Job).where(Job.parent_id == parent.id))
    assert child is not None
    assert child.type == "subtitle_policy"
    assert child.request["policy_revision"] == policy_revision
    assert len(child.request["remove_selectors"]) == 1
    assert child.request["remove_selectors"][0]["facts"]["kind"] == "subtitle"
