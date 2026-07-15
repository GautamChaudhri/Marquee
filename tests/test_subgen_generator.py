from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from marquee.core.subtitles.config import subtitle_settings
from marquee.core.subtitles.generators.base import GenerationRequest
from marquee.core.subtitles.generators.subgen import SubgenPathGenerator, expected_output_srt
from marquee.main import app


class _FakeResponse:
    def __init__(self) -> None:
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"status": "ok"}


class _FakeClient:
    last_url: str | None = None
    last_params: dict | None = None

    def __init__(self, *args, **kwargs) -> None:
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, *, params=None, json=None, files=None):
        self.__class__.last_url = url
        self.__class__.last_params = params
        assert json is None
        assert files is None
        return _FakeResponse()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_subgen_submit_uses_query_params(monkeypatch):
    monkeypatch.setattr("marquee.core.subtitles.generators.subgen.httpx.AsyncClient", _FakeClient)
    monkeypatch.setattr(subtitle_settings, "SUBGEN_URL", "http://subgen.local")
    generator = SubgenPathGenerator()
    request = GenerationRequest(
        media_file_id=1,
        local_media_path="/media/Movie.mkv",
        language_hint="en",
    )

    submission = await generator.submit(request)

    assert submission.accepted is True
    assert _FakeClient.last_url == "http://subgen.local/batch"
    assert _FakeClient.last_params == {"directory": "/media/Movie.mkv", "forceLanguage": "en"}


def test_expected_output_srt_uses_subgen_naming(monkeypatch):
    monkeypatch.setattr(subtitle_settings, "SUBGEN_NAMING_TYPE", "ISO_639_2_B")
    monkeypatch.setattr(subtitle_settings, "SUBGEN_NAME_INCLUDES_SUBGEN", True)
    monkeypatch.setattr(subtitle_settings, "SUBGEN_NAME_INCLUDES_MODEL", False)

    output = expected_output_srt("/media/Movie.mkv", "en", "large-v3-turbo")

    assert output == Path("/media/Movie.subgen.eng.srt")


@pytest.mark.asyncio
async def test_translate_request_rejects_nontranslating_model(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(subtitle_settings, "SUBGEN_DEPLOYMENT", "external")
    monkeypatch.setattr(subtitle_settings, "SUBGEN_URL", "http://subgen.local")
    monkeypatch.setattr(subtitle_settings, "SUBGEN_MODEL_LABEL", "large-v3-turbo")
    monkeypatch.setattr(subtitle_settings, "SUBGEN_WHISPER_MODEL", "")

    resp = await client.post(
        "/api/media-files/1/subtitle-generations",
        json={"task": "translate", "output": "external"},
        headers={"Idempotency-Key": "translate-rejected-model"},
    )

    assert resp.status_code == 422
    assert "cannot translate" in resp.json()["detail"]
