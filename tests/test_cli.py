from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from marquee import cli
from marquee.config import pipeline_settings, settings


def test_run_arguments() -> None:
    args = cli._build_parser().parse_args(["run", "Die Hard", "--year", "1988", "--json"])

    assert (args.command, args.title, args.year, args.tmdb_id, args.json) == (
        "run",
        "Die Hard",
        1988,
        None,
        True,
    )


def test_run_with_a_tmdb_id() -> None:
    args = cli._build_parser().parse_args(["run", "Die Hard", "--tmdb-id", "562"])

    assert args.tmdb_id == 562


@pytest.mark.parametrize("argv", [["run"], ["run", "Die Hard", "--year", "eighty-eight"]])
def test_run_rejects_bad_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli._build_parser().parse_args(argv)
    assert exc.value.code == 2


def test_run_folder_name() -> None:
    now = datetime(2026, 11, 23, 16, 5, 9, tzinfo=UTC)

    assert cli._run_folder_name("Mission: Impossible", now) == "20261123T160509Z-mission-impossible"
    assert cli._run_folder_name("2010", now) == "20261123T160509Z-2010"
    assert cli._run_folder_name("!!!", now) == "20261123T160509Z-untitled"


@pytest.fixture
def ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A configured token and a taste profile file, so the run reaches the lookup."""
    profile = tmp_path / "taste_profile.npz"
    profile.write_bytes(b"profile")
    monkeypatch.setattr(settings, "TMDB_READ_ACCESS_TOKEN", SecretStr("test-token"))
    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_PATH", profile)
    return profile


def test_run_without_a_tmdb_token(
    monkeypatch: pytest.MonkeyPatch, ready: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(settings, "TMDB_READ_ACCESS_TOKEN", None)

    assert cli.main(["run", "Die Hard"]) == 1
    assert capsys.readouterr().err.strip() == (
        "marquee run: TMDB_READ_ACCESS_TOKEN is not set; add it to .env"
    )


def test_run_without_a_taste_profile(
    monkeypatch: pytest.MonkeyPatch,
    ready: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(pipeline_settings, "TASTE_PROFILE_PATH", tmp_path / "absent.npz")

    assert cli.main(["run", "Die Hard"]) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert "no taste profile" in err[0]
    assert "marquee taste seed" in err[0]


class _NoNetworkTMDB:
    """Stands in for TMDBClient; a lookup that never reaches TMDB must not call it."""

    def __init__(self, read_access_token: str) -> None:
        self.token = read_access_token

    async def __aenter__(self) -> _NoNetworkTMDB:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def find_by_external_id(self, external_id: str, source: str) -> dict:
        raise AssertionError("no TMDB call expected")


def test_run_with_an_unknown_title(
    monkeypatch: pytest.MonkeyPatch,
    ready: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "Movies"
    (root / "1080p" / "Die Hard (1988)").mkdir(parents=True)
    monkeypatch.setattr(settings, "MOVIE_ROOTS", [root])
    monkeypatch.setattr("marquee.integrations.tmdb.client.TMDBClient", _NoNetworkTMDB)

    assert cli.main(["run", "Die Hrd"]) == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert err[0].startswith("marquee run: no movie folder titled 'Die Hrd'")
    assert "Die Hard (1988)" in err[0]


def test_run_with_an_absent_library(
    monkeypatch: pytest.MonkeyPatch,
    ready: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(settings, "MOVIE_ROOTS", [tmp_path / "not-mounted"])

    assert cli.main(["run", "Die Hard"]) == 1
    assert "does not exist (is the share mounted?)" in capsys.readouterr().err
