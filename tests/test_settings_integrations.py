from __future__ import annotations

import base64
import json
import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

pytest.importorskip("cryptography")

import marquee.api.routes.settings as settings_routes  # noqa: E402
from marquee.config import settings  # noqa: E402
from marquee.core.managed_secrets import (  # noqa: E402
    ManagedSecretProvider,
    ManagedSecretUnavailableError,
)
from marquee.main import app  # noqa: E402
from marquee.models.configuration import ManagedSecret, ManagedSecretEvent  # noqa: E402


@pytest.fixture
def managed_keyring(tmp_path, monkeypatch):
    path = tmp_path / "settings-keyring.json"
    path.write_text(
        json.dumps(
            {
                "active_key_id": "test-key",
                "keys": {"test-key": base64.b64encode(os.urandom(32)).decode("ascii")},
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    monkeypatch.setattr(settings, "MARQUEE_SETTINGS_KEYRING_FILE", path)
    return path


@pytest.fixture
def integration_probe(monkeypatch):
    calls: list[tuple[str, str | None, str | None]] = []

    async def probe(provider: str, *, url: str | None, credential: str | None) -> None:
        calls.append((provider, url, credential))

    monkeypatch.setattr(settings_routes, "_test_integration", probe)
    return calls


def _client(*, secure: bool = True) -> AsyncClient:
    transport = ASGITransport(app=app, client=("10.20.30.40", 12345))
    scheme = "https" if secure else "http"
    return AsyncClient(transport=transport, base_url=f"{scheme}://marquee.test")


@pytest.mark.asyncio
async def test_candidate_connection_test_does_not_persist(db, managed_keyring, integration_probe):
    async with _client() as client:
        response = await client.post(
            "/api/settings/integrations/radarr/test",
            json={"url": "http://radarr:7878", "credential": "candidate-key"},
        )

    assert response.status_code == 200
    assert integration_probe == [("radarr", "http://radarr:7878", "candidate-key")]
    assert await db.get(ManagedSecret, "RADARR_API_KEY") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:password@radarr:7878",
        "http://radarr:7878/?apikey=leak",
        "http://169.254.169.254/latest/meta-data",
        "http://2130706433:7878",
        "http://0177.0.0.1:7878",
        "http://0x7f.0.0.1:7878",
        "http://127.1:7878",
    ],
)
async def test_connection_urls_reject_credential_embedding_and_prohibited_targets(
    db, managed_keyring, integration_probe, url
):
    async with _client() as client:
        response = await client.post(
            "/api/settings/integrations/radarr/test",
            json={"url": url, "credential": "candidate-key"},
        )
    assert response.status_code == 400
    assert integration_probe == []


@pytest.mark.asyncio
async def test_integration_update_is_atomic_redacted_and_generation_checked(
    db, managed_keyring, integration_probe
):
    async with _client() as client:
        response = await client.put(
            "/api/settings/integrations/radarr",
            json={
                "expected_version": 1,
                "expected_secret_generation": 0,
                "name": "Cinema Rack",
                "url": "http://radarr:7878/",
                "credential": "managed-radarr-key",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["settings"]["values"]["RADARR_URL"] == "http://radarr:7878"
        assert payload["settings"]["values"]["RADARR_INSTANCE_NAME"] == "Cinema Rack"
        assert payload["settings"]["integrations"]["radarr"]["name"] == "Cinema Rack"
        assert payload["settings"]["secrets"]["RADARR_API_KEY"]["configured"] is True
        assert payload["settings"]["secrets"]["RADARR_API_KEY"]["generation"] == 1
        assert "managed-radarr-key" not in response.text
        assert "ciphertext" not in response.text

        stale = await client.put(
            "/api/settings/integrations/radarr",
            json={
                "expected_version": 2,
                "expected_secret_generation": 0,
                "url": "http://radarr:7878",
                "credential": "replacement",
            },
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "credential_generation_conflict"
        assert len(integration_probe) == 1

    row = await db.get(ManagedSecret, "RADARR_API_KEY")
    assert row is not None
    assert row.ciphertext != b"managed-radarr-key"
    assert b"managed-radarr-key" not in row.ciphertext
    events = (await db.scalars(select(ManagedSecretEvent))).all()
    assert [(event.name, event.action, event.generation) for event in events] == [
        ("RADARR_API_KEY", "replaced", 1)
    ]
    assert integration_probe[0] == ("radarr", "http://radarr:7878", "managed-radarr-key")


@pytest.mark.asyncio
async def test_stored_arr_credential_cannot_cross_origins_without_explicit_replacement(
    db, managed_keyring, integration_probe
):
    async with _client() as client:
        created = await client.put(
            "/api/settings/integrations/radarr",
            json={
                "expected_version": 1,
                "expected_secret_generation": 0,
                "url": "http://radarr:7878/base",
                "credential": "managed-key",
            },
        )
        assert created.status_code == 200
        integration_probe.clear()

        changed = await client.post(
            "/api/settings/integrations/radarr/test",
            json={"url": "http://sonarr:7878/base"},
        )
        assert changed.status_code == 400
        assert "replacement credential" in changed.json()["detail"]
        assert integration_probe == []

        same_origin = await client.post(
            "/api/settings/integrations/radarr/test",
            json={"url": "http://RADARR:7878/other-path"},
        )
        assert same_origin.status_code == 200
        assert integration_probe == [("radarr", "http://radarr:7878/other-path", "managed-key")]


@pytest.mark.asyncio
async def test_generic_configuration_endpoint_rejects_arr_urls(db, managed_keyring):
    async with _client() as client:
        response = await client.put(
            "/api/settings/config",
            json={
                "expected_version": 1,
                "values": {"RADARR_URL": "http://attacker.test:7878"},
            },
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "integration_url_requires_provider_endpoint"


@pytest.mark.asyncio
async def test_validation_errors_never_reflect_candidate_secret(db, managed_keyring):
    candidate = "secret-marker-" + ("x" * 9000)
    async with _client() as client:
        response = await client.post(
            "/api/settings/integrations/tmdb/test",
            json={"credential": candidate},
        )
    assert response.status_code == 422
    assert "secret-marker" not in response.text
    assert "input" not in response.text


@pytest.mark.asyncio
async def test_credential_clear_creates_tombstone_that_overrides_legacy_environment(
    db, managed_keyring, integration_probe, monkeypatch
):
    monkeypatch.setattr(settings, "RADARR_API_KEY", "legacy-key")
    async with _client() as client:
        created = await client.put(
            "/api/settings/integrations/radarr",
            json={
                "expected_version": 1,
                "expected_secret_generation": 0,
                "url": "http://radarr:7878",
                "credential": "managed-key",
            },
        )
        assert created.status_code == 200
        cleared = await client.request(
            "DELETE",
            "/api/settings/integrations/radarr/credential",
            json={"expected_generation": 1},
        )

    assert cleared.status_code == 200
    status = cleared.json()["settings"]["secrets"]["RADARR_API_KEY"]
    assert status == {
        "configured": False,
        "source": "managed",
        "generation": 2,
        "updated_at": status["updated_at"],
    }
    row = await db.get(ManagedSecret, "RADARR_API_KEY")
    assert row is not None and row.configured is False and row.generation == 2


@pytest.mark.asyncio
async def test_provider_fails_closed_when_database_rows_cannot_be_decrypted(
    db, managed_keyring, integration_probe, monkeypatch, tmp_path
):
    async with _client() as client:
        created = await client.put(
            "/api/settings/integrations/radarr",
            json={
                "expected_version": 1,
                "expected_secret_generation": 0,
                "url": "http://radarr:7878",
                "credential": "managed-key",
            },
        )
    assert created.status_code == 200

    wrong = tmp_path / "wrong-keyring.json"
    wrong.write_text(
        json.dumps(
            {
                "active_key_id": "different",
                "keys": {"different": base64.b64encode(os.urandom(32)).decode("ascii")},
            }
        ),
        encoding="utf-8",
    )
    wrong.chmod(0o600)
    monkeypatch.setattr(settings, "MARQUEE_SETTINGS_KEYRING_FILE", wrong)

    with pytest.raises(ManagedSecretUnavailableError, match="not present"):
        await ManagedSecretProvider().refresh(db)


@pytest.mark.asyncio
async def test_internal_http_allows_connection_tests(db, managed_keyring, integration_probe):
    async with _client(secure=False) as client:
        response = await client.post(
            "/api/settings/integrations/tmdb/test",
            json={"credential": "candidate-key"},
        )
    assert response.status_code == 200
    assert integration_probe == [("tmdb", None, "candidate-key")]


@pytest.mark.asyncio
async def test_private_same_origin_proxy_can_attest_external_https(
    db, managed_keyring, integration_probe
):
    async with _client(secure=False) as client:
        response = await client.post(
            "/api/settings/integrations/tmdb/test",
            json={"credential": "candidate-key"},
            headers={
                "X-Marquee-Internal-Proxy": "same-origin",
                "X-Forwarded-Proto": "https",
            },
        )
    assert response.status_code == 200
    assert integration_probe == [("tmdb", None, "candidate-key")]
