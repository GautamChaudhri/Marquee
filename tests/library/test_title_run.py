from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from marquee.config import pipeline_settings, settings
from marquee.integrations.tmdb.client import MovieDetails
from marquee.library import title_run
from marquee.library.subjects import ResolvedSubject


def test_run_folder_name() -> None:
    now = datetime(2026, 11, 23, 16, 5, 9, tzinfo=UTC)

    assert (
        title_run.run_folder_name("Mission: Impossible", now)
        == "20261123T160509Z-mission-impossible"
    )
    assert title_run.run_folder_name("2010", now) == "20261123T160509Z-2010"
    assert title_run.run_folder_name("!!!", now) == "20261123T160509Z-untitled"


class _FakeTMDB:
    """Answers the one details request a run with a TMDB id makes."""

    def __init__(self, read_access_token: str) -> None:
        self.token = read_access_token

    async def __aenter__(self) -> _FakeTMDB:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def find_by_external_id(self, external_id: str, source: str) -> dict:
        raise AssertionError("a run with a TMDB id does not look the title up")

    async def get_movie_details(self, tmdb_id: int) -> MovieDetails:
        return MovieDetails(director="John McTiernan", production_companies=["Gordon"])


@pytest.fixture
def ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    profile = tmp_path / "taste_profile.npz"
    profile.write_bytes(b"profile")
    monkeypatch.setattr(settings, "TMDB_READ_ACCESS_TOKEN", SecretStr("test-token"))
    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_PATH", profile)
    monkeypatch.setattr("marquee.integrations.tmdb.client.TMDBClient", _FakeTMDB)
    return profile


async def test_run_title_passes_progress_and_cancel_to_the_pipeline(
    monkeypatch: pytest.MonkeyPatch, ready: Path
) -> None:
    seen: dict[str, Any] = {}

    async def fake_run_single_subject(**kwargs: Any) -> str:
        seen.update(kwargs)
        return "output"

    monkeypatch.setattr("marquee.pipeline.single_run.run_single_subject", fake_run_single_subject)
    resolved: list[ResolvedSubject] = []

    def progress(_event: object) -> None:
        return None

    def should_cancel() -> bool:
        return False

    run = await title_run.run_title(
        "Die Hard",
        tmdb_id=562,
        progress=progress,
        should_cancel=should_cancel,
        on_resolved=resolved.append,
    )

    assert run.output == "output"
    assert run.subject == ResolvedSubject(title="Die Hard", tmdb_id=562)
    assert resolved == [run.subject]
    assert seen["progress"] is progress
    assert seen["should_cancel"] is should_cancel
    assert seen["run_dir"] == run.run_dir
    assert run.run_dir.parent == settings.runs_dir
    assert run.run_dir.name.endswith("-die-hard")
    assert (seen["subject"].title, seen["subject"].tmdb_id) == ("Die Hard", 562)
    assert seen["ocr_gate"] is not None


async def test_run_title_without_a_token(monkeypatch: pytest.MonkeyPatch, ready: Path) -> None:
    monkeypatch.setattr(settings, "TMDB_READ_ACCESS_TOKEN", None)

    with pytest.raises(title_run.TitleRunError, match="TMDB_READ_ACCESS_TOKEN is not set"):
        await title_run.run_title("Die Hard", tmdb_id=562)


async def test_run_title_without_a_taste_profile(
    monkeypatch: pytest.MonkeyPatch, ready: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_PATH", tmp_path / "absent.npz")

    with pytest.raises(title_run.TitleRunError, match="no taste profile"):
        await title_run.run_title("Die Hard", tmdb_id=562)
