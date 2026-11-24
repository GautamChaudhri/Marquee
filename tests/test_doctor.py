from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath

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


def test_media_roots_are_movie_roots_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(doctor.settings, "MOVIE_ROOTS", [])
    result = doctor.check_media_roots()
    assert (result.status, result.detail) == ("FAIL", "MOVIE_ROOTS is unset")

    _make_library(tmp_path)
    monkeypatch.setattr(doctor.settings, "MOVIE_ROOTS", [tmp_path])
    assert "(3 folders)" in doctor.check_media_roots().detail


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


def _configure_every_optional_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in doctor._OPTIONAL_SETTINGS:
        monkeypatch.setattr(doctor.settings, name, "set")


def test_settings_from_the_environment_alone_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "ENV_FILE", None)
    _configure_every_optional_setting(monkeypatch)
    result = doctor.check_settings()
    assert result.status == "PASS"
    assert "settings come from the environment" in result.detail


def test_settings_from_the_environment_still_warn_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor, "ENV_FILE", None)
    _configure_every_optional_setting(monkeypatch)
    monkeypatch.setattr(doctor.settings, "RADARR_URL", None)
    result = doctor.check_settings()
    assert result.status == "WARN"
    assert "settings come from the environment" in result.detail
    assert "unset: RADARR_URL" in result.detail


def test_settings_file_found_or_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure_every_optional_setting(monkeypatch)
    env_file = tmp_path / ".env"
    monkeypatch.setattr(doctor, "ENV_FILE", env_file)
    missing = doctor.check_settings()
    assert missing.status == "FAIL"
    assert ".env missing" in missing.detail

    env_file.write_text("", encoding="utf-8")
    found = doctor.check_settings()
    assert found.status == "PASS"
    assert ".env found" in found.detail


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


# ── Models, manifest, and taste profile ───────────────────────────────────


def _point_models_at(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    from marquee.config import pipeline_settings

    monkeypatch.setattr(doctor.settings, "DATA_DIR", data_dir)
    for name, path in {
        "DINO_MODEL_PATH": data_dir / "models" / "dinov2-vits14.onnx",
        "PERSON_MODEL_PATH": data_dir / "models" / "yolo11n.onnx",
        "ZEROSHOT_AXES_PATH": data_dir / "ml" / "zeroshot_axes.clip-vit-b-32.npz",
        "TASTE_PROFILE_PATH": data_dir / "ml" / "taste_profile.clip-vit-b-32.npz",
    }.items():
        monkeypatch.setattr(pipeline_settings, name, path)


def test_style_models_warn_until_fetched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _point_models_at(monkeypatch, tmp_path)

    result = doctor.check_style_models()
    assert (result.name, result.status) == ("Style models", "WARN")
    assert "marquee models fetch" in result.detail

    for relative in ("models/dinov2-vits14.onnx", "models/yolo11n.onnx"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_bytes(b"x")
    (tmp_path / "ml").mkdir()
    (tmp_path / "ml" / "zeroshot_axes.clip-vit-b-32.npz").write_bytes(b"x")
    assert doctor.check_style_models().status == "PASS"


def test_model_manifest_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from marquee.ml import model_manifest

    _point_models_at(monkeypatch, tmp_path)
    model = tmp_path / "models" / "yolo11n.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"weights")

    absent = doctor.check_model_manifest()
    assert (absent.status, "marquee models fetch" in absent.detail) == ("WARN", True)

    entry = model_manifest.describe(tmp_path, model, item="yolo", source="test")
    model_manifest.write_manifest(tmp_path / "models" / "manifest.json", [entry])
    clean = doctor.check_model_manifest()
    assert (clean.status, clean.detail) == ("PASS", "1 entries match their checksums")

    model.write_bytes(b"WEIGHTS")  # same size, different bytes
    changed = doctor.check_model_manifest()
    assert (changed.status, changed.detail) == ("FAIL", "models/yolo11n.onnx: checksum mismatch")

    model.unlink()
    assert doctor.check_model_manifest().detail == "models/yolo11n.onnx: missing"

    (tmp_path / "models" / "manifest.json").write_text("{broken")
    assert doctor.check_model_manifest().status == "FAIL"


def _profile(path: Path, *, model_name: str = "clip-vit-b-32", dino: str | None = None) -> None:
    import numpy as np

    from marquee.ml.artifact_codec import unicode_array, unicode_scalar

    rng = np.random.default_rng(0)
    embeddings = rng.standard_normal((3, 512)).astype(np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    payload = {
        "embeddings": embeddings,
        "poster_names": unicode_array(["A (2001).png", "B (2002).png", "C (2003).png"]),
        "centroid_emb": embeddings.mean(axis=0),
        "model_name": unicode_scalar(model_name),
    }
    if dino:
        payload["dino_embeddings"] = np.ones((3, 384), dtype=np.float32)
        payload["dino_model_name"] = unicode_scalar(dino)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **payload)


def test_taste_profile_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from marquee.config import pipeline_settings

    _point_models_at(monkeypatch, tmp_path)
    profile = Path(pipeline_settings.TASTE_PROFILE_PATH)

    absent = doctor.check_taste_profile()
    assert (absent.name, absent.status) == ("Taste profile", "WARN")
    assert "marquee taste seed" in absent.detail

    _profile(profile, dino="dinov2-vits14")
    ok = doctor.check_taste_profile()
    assert ok.status == "PASS"
    assert ok.detail.startswith("3 exemplars, DINOv2 embeddings (dinov2-vits14)")

    _profile(profile)
    assert "no DINOv2 embeddings" in doctor.check_taste_profile().detail

    _profile(profile, dino="dinov2-vitb14")
    other_dino = doctor.check_taste_profile()
    assert (other_dino.status, "dinov2-vitb14" in other_dino.detail) == ("FAIL", True)

    _profile(profile, model_name="clip-vit-l-14")
    other_clip = doctor.check_taste_profile()
    assert other_clip.status == "FAIL"
    assert "model mismatch" in other_clip.detail


def test_settings_warn_when_the_api_key_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "ENV_FILE", None)
    _configure_every_optional_setting(monkeypatch)
    monkeypatch.setattr(doctor.settings, "API_KEY", None)
    result = doctor.check_settings()
    assert result.status == "WARN"
    assert "unset: API_KEY" in result.detail


def test_error_text_scrubs_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.settings, "API_KEY", SecretStr("the-api-key-value"))
    text = doctor._error_text(RuntimeError("header carried the-api-key-value"))
    assert "the-api-key-value" not in text


def _point_settings_at(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    monkeypatch.setattr(doctor.settings, "POSTGRES_USER", parsed.username)
    monkeypatch.setattr(doctor.settings, "POSTGRES_PASSWORD", SecretStr(parsed.password or ""))
    monkeypatch.setattr(doctor.settings, "POSTGRES_HOST", parsed.host)
    monkeypatch.setattr(doctor.settings, "POSTGRES_PORT", parsed.port)
    monkeypatch.setattr(doctor.settings, "POSTGRES_DB", parsed.database)


@pytest.mark.db
def test_database_passes_at_the_newest_schema(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    _point_settings_at(monkeypatch, database_url)
    result = doctor.check_database()
    assert result.status == "PASS"
    assert "schema at 0003 (newest)" in result.detail


@pytest.mark.db
def test_database_warns_when_not_migrated(
    monkeypatch: pytest.MonkeyPatch, empty_database_url: str
) -> None:
    _point_settings_at(monkeypatch, empty_database_url)
    result = doctor.check_database()
    assert result.status == "WARN"
    assert "schema at no revision, newest is 0003" in result.detail
    assert "marquee db upgrade" in result.detail


def _radarr_configured(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(doctor.settings, "RADARR_URL", "http://radarr.invalid:7878")
    monkeypatch.setattr(doctor.settings, "RADARR_API_KEY", SecretStr("radarr-key"))
    monkeypatch.setattr(doctor.settings, "MOVIE_ROOTS", [root])
    monkeypatch.setattr(doctor.settings, "RADARR_MOVIE_ROOTS", [PurePosixPath("/data/movies")])


def test_path_mapping_passes_and_shows_only_a_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _make_library(tmp_path)
    _radarr_configured(monkeypatch, tmp_path)
    monkeypatch.setattr(
        doctor,
        "_radarr_root_folders",
        lambda: [{"path": "/data/movies/1080p"}, {"path": "/data/movies/4K"}],
    )
    result = doctor.check_path_mapping()
    assert (result.status, result.detail) == ("PASS", "2 Radarr root folders map to folders here")


def test_path_mapping_fails_when_a_folder_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _make_library(tmp_path)
    _radarr_configured(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "_radarr_root_folders", lambda: [{"path": "/data/movies/720p"}])
    result = doctor.check_path_mapping()
    assert result.status == "FAIL"
    assert "RADARR_MOVIE_ROOTS" in result.detail


def test_path_mapping_fails_when_radarr_does_not_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _radarr_configured(monkeypatch, tmp_path)

    def unreachable() -> object:
        raise ConnectionError("refused, key radarr-key")

    monkeypatch.setattr(doctor, "_radarr_root_folders", unreachable)
    result = doctor.check_path_mapping()
    assert result.status == "FAIL"
    assert "radarr-key" not in result.detail


def test_path_mapping_warns_without_radarr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.settings, "RADARR_URL", None)
    assert doctor.check_path_mapping().status == "WARN"


def _libpq(url: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.mark.db
def test_worker_warns_when_no_worker_runs(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    import psycopg

    _point_settings_at(monkeypatch, database_url)
    with psycopg.connect(_libpq(database_url), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO jobs (kind, job_class, state) VALUES "
            "('a', 'network', 'queued'), ('b', 'gpu', 'failed'), ('batch', 'batch', 'running')"
        )
        try:
            result = doctor.check_worker()
        finally:
            conn.execute("TRUNCATE jobs RESTART IDENTITY CASCADE")
    assert result.status == "WARN"
    assert result.detail == "no worker running (`marquee worker`); 1 queued, 0 running, 1 failed"


@pytest.mark.db
def test_worker_passes_while_a_worker_holds_the_lock(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    import psycopg

    from marquee.jobs.store import WORKER_LOCK_KEY

    _point_settings_at(monkeypatch, database_url)
    with psycopg.connect(_libpq(database_url), autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(%s)", [WORKER_LOCK_KEY])
        result = doctor.check_worker()
    assert result.status == "PASS"
    assert result.detail.startswith("running since ")
    assert result.detail.endswith("UTC; 0 queued, 0 running, 0 failed")
    assert doctor.check_worker().status == "WARN"  # the lock went with the connection


@pytest.mark.db
def test_worker_warns_before_the_job_tables_exist(
    monkeypatch: pytest.MonkeyPatch, empty_database_url: str
) -> None:
    _point_settings_at(monkeypatch, empty_database_url)
    result = doctor.check_worker()
    assert (result.status, result.detail) == ("WARN", "no job tables; run `marquee db upgrade`")


def test_worker_fails_when_the_database_does_not_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.settings, "POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setattr(doctor.settings, "POSTGRES_PORT", 1)
    result = doctor.check_worker()
    assert result.status == "FAIL"
    assert result.detail.startswith("cannot read the queue")
