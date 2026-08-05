"""Reset semantics for the unified Settings API.

Resetting has to drop the stored override, not write the default value back.
Writing it would leave the key marked ``custom`` forever and stop it from
tracking any future change to the shipped default.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.main import app


def _client() -> AsyncClient:
    transport = ASGITransport(app=app, client=("10.20.30.40", 12345))
    return AsyncClient(transport=transport, base_url="https://marquee.test")


async def _put(client: AsyncClient, version: int, values: dict) -> dict:
    response = await client.put(
        "/api/settings/config",
        json={"expected_version": version, "values": values},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_reset_by_keys_returns_the_setting_to_its_default_source(db):
    async with _client() as client:
        saved = await _put(client, 1, {"K_NEIGHBORS": 17})
        assert saved["settings"]["sources"]["K_NEIGHBORS"] == "custom"
        default = saved["settings"]["defaults"]["K_NEIGHBORS"]
        assert saved["settings"]["values"]["K_NEIGHBORS"] == 17

        response = await client.post(
            "/api/settings/config/reset",
            json={
                "expected_version": saved["configuration_version"],
                "scope": "keys",
                "keys": ["K_NEIGHBORS"],
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    assert body["applied"] == ["K_NEIGHBORS"]
    assert body["settings"]["values"]["K_NEIGHBORS"] == default
    assert body["settings"]["sources"]["K_NEIGHBORS"] != "custom"


@pytest.mark.asyncio
async def test_reset_scoped_to_a_tab_leaves_other_tabs_alone(db):
    # The scope follows the catalog's own tab routing, not the owning model:
    # GATE_MIN_WIDTH is a PipelineSettings field shown on the pipeline tab, while
    # K_NEIGHBORS is also a PipelineSettings field but is surfaced under taste.
    async with _client() as client:
        saved = await _put(
            client, 1, {"GATE_MIN_WIDTH": 800, "K_NEIGHBORS": 17, "APP_NAME": "Screening Room"}
        )

        response = await client.post(
            "/api/settings/config/reset",
            json={
                "expected_version": saved["configuration_version"],
                "scope": "tab",
                "tab": "pipeline",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] == ["GATE_MIN_WIDTH"]
    assert body["settings"]["sources"]["GATE_MIN_WIDTH"] != "custom"
    assert body["settings"]["values"]["K_NEIGHBORS"] == 17
    assert body["settings"]["values"]["APP_NAME"] == "Screening Room"
    assert body["settings"]["sources"]["APP_NAME"] == "custom"


@pytest.mark.asyncio
async def test_reset_all_clears_every_override(db):
    async with _client() as client:
        saved = await _put(client, 1, {"K_NEIGHBORS": 17, "APP_NAME": "Screening Room"})

        response = await client.post(
            "/api/settings/config/reset",
            json={"expected_version": saved["configuration_version"], "scope": "all"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(body["applied"]) == ["APP_NAME", "K_NEIGHBORS"]
    sources = body["settings"]["sources"]
    assert not [key for key in ("K_NEIGHBORS", "APP_NAME") if sources[key] == "custom"]


@pytest.mark.asyncio
async def test_reset_all_on_a_pristine_install_is_a_noop(db):
    async with _client() as client:
        response = await client.post(
            "/api/settings/config/reset",
            json={"expected_version": 1, "scope": "all"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is False
    assert body["applied"] == []
    assert body["configuration_version"] == 1


@pytest.mark.asyncio
async def test_reset_rejects_keys_that_are_not_revision_owned(db):
    async with _client() as client:
        response = await client.post(
            "/api/settings/config/reset",
            # HOST is deployment-owned and TMDB_READ_ACCESS_TOKEN lives in the
            # secret store; neither is ever carried by a revision.
            json={
                "expected_version": 1,
                "scope": "keys",
                "keys": ["HOST", "TMDB_READ_ACCESS_TOKEN", "NOT_A_SETTING"],
            },
        )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "configuration_key_not_resettable"
    assert detail["fields"] == ["HOST", "NOT_A_SETTING", "TMDB_READ_ACCESS_TOKEN"]


@pytest.mark.asyncio
async def test_reset_scope_requires_its_selector(db):
    async with _client() as client:
        response = await client.post(
            "/api/settings/config/reset",
            json={"expected_version": 1, "scope": "tab"},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "configuration_reset_scope_incomplete"


@pytest.mark.asyncio
async def test_reset_rejects_an_unknown_tab(db):
    async with _client() as client:
        response = await client.post(
            "/api/settings/config/reset",
            json={"expected_version": 1, "scope": "tab", "tab": "nowhere"},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "configuration_reset_scope_unknown"


@pytest.mark.asyncio
async def test_stale_reset_conflicts_instead_of_dropping_a_newer_override(db):
    async with _client() as client:
        await _put(client, 1, {"K_NEIGHBORS": 17})

        response = await client.post(
            "/api/settings/config/reset",
            json={"expected_version": 1, "scope": "all"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "configuration_version_conflict"


@pytest.mark.asyncio
async def test_a_write_and_a_reset_land_in_one_revision(db):
    async with _client() as client:
        saved = await _put(client, 1, {"K_NEIGHBORS": 17, "APP_NAME": "Screening Room"})

        response = await client.put(
            "/api/settings/config",
            json={
                "expected_version": saved["configuration_version"],
                "values": {"APP_NAME": "Projection Room"},
                "removals": ["K_NEIGHBORS"],
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configuration_version"] == saved["configuration_version"] + 1
    assert body["settings"]["values"]["APP_NAME"] == "Projection Room"
    assert body["settings"]["sources"]["K_NEIGHBORS"] != "custom"
