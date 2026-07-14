"""JMC4B B3 — canonical, read-only subtitle policy audit."""

from __future__ import annotations

import inspect

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import Queries

from marquee.api.routes.subtitle_policies import audit_policy
from marquee.core.jobs.handlers_subtitles import _AUDIT_SUBJECT_CAP, execute_subtitle_policy_audit
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.database import _get_engine, _get_session_factory
from marquee.main import app
from marquee.models import Job, MediaFile, SubtitleInventory, SubtitlePolicy, SubtitleTrack


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
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


def test_policy_audit_definition_is_enabled_read_only_cpu() -> None:
    definition = JOB_DEFINITION_REGISTRY.get("subtitle_policy_audit")
    assert definition.enabled is True
    assert definition.execution_class.value == "cpu"
    assert definition.effect_safety.value == "read_only"
    assert definition.progress_policy.strategy.value == "indeterminate"
    assert definition.progress_policy.stage_keys == {"selecting", "evaluating"}
    assert JOB_DEFINITION_REGISTRY.for_dispatch(
        "subtitle_policy_audit", entrypoint="cpu"
    ).enabled


@pytest.mark.asyncio
async def test_policy_audit_route_submits_snapshot_job_and_remains_read_only(
    client: AsyncClient, db
) -> None:
    policy = SubtitlePolicy(name="Keep English", mode="allowlist", languages_json='["en"]')
    db.add(policy)
    await db.commit()
    await db.refresh(policy)

    response = await client.post(f"/api/subtitle-policies/{policy.id}/audit", json={"scope": "tv"})
    assert response.status_code == 202
    body = response.json()
    assert body["disposition"] == "created"
    assert set(body) == {"job_id", "disposition", "phase", "snapshot_url", "detail_url"}

    job = await db.get(Job, body["job_id"])
    assert job is not None
    assert job.type == "subtitle_policy_audit"
    assert job.plan["entrypoint"] == "cpu"
    assert job.plan["effect_safety"] == "read_only"
    assert job.request == {
        "policy_id": policy.id,
        "policy_revision": policy.revision,
        "policy_snapshot": {
            "mode": "allowlist",
            "languages": ["en"],
            "unknown_action": "keep",
            "protect_forced": True,
            "protect_default": True,
            "protect_last_full_dialogue": True,
            "include_external": False,
        },
        "scope": "tv",
    }

    route_source = inspect.getsource(audit_policy)
    assert "submit_job" in route_source
    assert "evaluate_policy" not in route_source
    assert "create_plan" not in route_source


@pytest.mark.asyncio
async def test_policy_audit_reports_coverage_missing_and_unavailable_without_mutation(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    scanned = MediaFile(source="radarr", source_key="audit-scanned", path="/media/a.mkv")
    missing = MediaFile(source="radarr", source_key="audit-missing", path="/media/b.mkv")
    unavailable = MediaFile(
        source="radarr", source_key="audit-unavailable", path="/media/c.mkv", is_present=False
    )
    db.add_all((scanned, missing, unavailable))
    await db.flush()
    inventory = SubtitleInventory(media_file_id=scanned.id, audio_streams_json=[])
    unavailable_inventory = SubtitleInventory(
        media_file_id=unavailable.id, audio_streams_json=[], error="media_unavailable"
    )
    db.add_all((inventory, unavailable_inventory))
    await db.flush()
    db.add_all(
        (
            SubtitleTrack(
                id="audit-french-1",
                inventory_id=inventory.id,
                source="embedded",
                kind="text",
                language_tag="fr",
            ),
            SubtitleTrack(
                id="audit-french-2",
                inventory_id=inventory.id,
                source="embedded",
                kind="text",
                language_tag="fr",
            ),
        )
    )
    await db.commit()

    async def _noop_stage(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("marquee.core.jobs.handlers_subtitles._emit_policy_stage", _noop_stage)
    context = type(
        "AuditContext",
        (),
        {
            "cancellation": type("Cancellation", (), {"cancel_called": False})(),
            "request": {
                "policy_id": 1,
                "policy_snapshot": {
                    "mode": "allowlist",
                    "languages": ["en"],
                    "unknown_action": "keep",
                    "protect_forced": True,
                    "protect_default": True,
                    "protect_last_full_dialogue": True,
                    "include_external": False,
                },
                "scope": "all",
            },
            "session_factory": _get_session_factory(),
        },
    )()

    result = await execute_subtitle_policy_audit(context)  # type: ignore[arg-type]

    assert result["outcome"] == "succeeded"
    assert result["summary"] == {
        "policy_id": 1,
        "scope": "all",
        "subjects_scanned": 1,
        "subjects_with_changes": 1,
        "proposed_removals": 2,
        "protected_tracks": 0,
        "review_required_subjects": 0,
        "warnings": 2,
        "coverage": {
            "before_full_dialogue_subjects": 1,
            "after_full_dialogue_subjects": 0,
            "before_full_dialogue_languages": 1,
            "after_full_dialogue_languages": 0,
            "before_unknown_subjects": 0,
            "after_unknown_subjects": 0,
        },
        "missing_inventory": 1,
        "unavailable_media": 1,
        "per_subject": [
            {
                "media_file_id": scanned.id,
                "removals": 2,
                "protected": 0,
                "review_required": 0,
            }
        ],
        "per_subject_truncated": False,
        "full_report_registered": False,
        "full_report_truncated": False,
    }
    assert (await db.get(SubtitleTrack, "audit-french-1")) is not None
    assert (await db.get(SubtitleTrack, "audit-french-2")) is not None


@pytest.mark.asyncio
async def test_policy_audit_publishes_a_sanitized_bounded_report_after_compact_cap(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = [
        MediaFile(source="radarr", source_key=f"audit-report-{index}", path=f"/media/{index}.mkv")
        for index in range(_AUDIT_SUBJECT_CAP + 1)
    ]
    db.add_all(media)
    await db.flush()
    inventories = [SubtitleInventory(media_file_id=item.id, audio_streams_json=[]) for item in media]
    db.add_all(inventories)
    await db.flush()
    db.add_all(
        track
        for index, inventory in enumerate(inventories)
        for track in (
            SubtitleTrack(
                id=f"audit-report-{index}-a",
                inventory_id=inventory.id,
                source="embedded",
                kind="text",
                language_tag="fr",
            ),
            SubtitleTrack(
                id=f"audit-report-{index}-b",
                inventory_id=inventory.id,
                source="embedded",
                kind="text",
                language_tag="fr",
            ),
        )
    )
    await db.commit()

    async def _noop_stage(*_args, **_kwargs) -> None:
        return None

    published: list[dict] = []

    async def _capture_report(_context, report: dict) -> None:
        published.append(report)

    monkeypatch.setattr("marquee.core.jobs.handlers_subtitles._emit_policy_stage", _noop_stage)
    monkeypatch.setattr(
        "marquee.core.jobs.handlers_subtitles._publish_policy_audit_report", _capture_report
    )
    context = type(
        "AuditContext",
        (),
        {
            "cancellation": type("Cancellation", (), {"cancel_called": False})(),
            "request": {
                "policy_id": 1,
                "policy_snapshot": {"mode": "allowlist", "languages": ["en"]},
                "scope": "all",
            },
            "session_factory": _get_session_factory(),
        },
    )()

    result = await execute_subtitle_policy_audit(context)  # type: ignore[arg-type]

    assert result["summary"]["per_subject_truncated"] is True
    assert result["summary"]["full_report_registered"] is True
    assert result["summary"]["full_report_truncated"] is False
    assert len(published) == 1
    assert published[0]["version"] == 1
    assert len(published[0]["subjects"]) == _AUDIT_SUBJECT_CAP + 1
    assert set(published[0]["subjects"][0]) == {
        "media_file_id",
        "removals",
        "protected",
        "review_required",
        "coverage_before",
        "coverage_after",
        "warning_codes",
    }
