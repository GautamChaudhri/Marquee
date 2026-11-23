from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import SecretStr

from marquee import doctor
from marquee.doctor import CheckResult


def _make_library(root: Path) -> None:
    for group, titles in {"1080p": ["A (2001)", "B (2002)"], "4K": ["C (2003)"]}.items():
        for title in titles:
            (root / group / title).mkdir(parents=True)
    (root / ".marquee" / "state").mkdir(parents=True)


def test_media_root_counts_visible_folders_only(tmp_path: Path) -> None:
    _make_library(tmp_path)
    status, detail = doctor._describe_root(tmp_path)
    assert status == ("PASS" if os.access(tmp_path, os.W_OK) else "WARN")
    assert "1080p 2, 4K 1 (3 folders)" in detail
    assert ".marquee" not in detail


def test_media_root_missing_or_empty_fails(tmp_path: Path) -> None:
    assert doctor._describe_root(tmp_path / "absent")[0] == "FAIL"
    assert "empty" in doctor._describe_root(tmp_path)[1]


def test_run_with_timeout_gives_up_on_a_hung_call() -> None:
    import threading

    never = threading.Event()
    with pytest.raises(TimeoutError):
        doctor._run_with_timeout(never.wait, 0.05)


def test_error_text_scrubs_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.settings, "RADARR_API_KEY", SecretStr("very-secret-key"))
    text = doctor._error_text(RuntimeError("request with key very-secret-key failed"))
    assert "very-secret-key" not in text
    assert "***" in text


def test_unconfigured_services_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.settings, "TMDB_READ_ACCESS_TOKEN", None)
    monkeypatch.setattr(doctor.settings, "RADARR_URL", None)
    assert doctor.check_tmdb().status == "WARN"
    assert doctor.check_radarr().status == "WARN"


def test_main_exit_code_and_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        doctor,
        "CHECKS",
        (
            lambda: CheckResult("One", "PASS", "fine"),
            lambda: CheckResult("Two", "WARN", "meh"),
        ),
    )
    assert doctor.main(as_json=True) == 0
    assert [row["status"] for row in json.loads(capsys.readouterr().out)] == ["PASS", "WARN"]

    def broken() -> CheckResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor, "CHECKS", (broken,))
    assert doctor.main() == 1
    assert "FAIL" in capsys.readouterr().out
