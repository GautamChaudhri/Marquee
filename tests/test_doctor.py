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
    monkeypatch.setattr(doctor, "_registered_profile", lambda: None)  # nothing registered
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


@pytest.mark.db
def test_taste_profile_is_checked_against_its_registered_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, database_url: str
) -> None:
    import hashlib

    import psycopg

    from marquee.config import pipeline_settings

    _point_models_at(monkeypatch, tmp_path)
    _point_settings_at(monkeypatch, database_url)
    profile = Path(pipeline_settings.TASTE_PROFILE_PATH)
    _profile(profile, dino="dinov2-vits14")
    sha = hashlib.sha256(profile.read_bytes()).hexdigest()
    assert doctor.check_taste_profile().status == "PASS"  # nothing registered yet

    with psycopg.connect(doctor.settings.database_url_sync, autocommit=True) as conn:
        conn.execute(
            "insert into taste_profiles (sha256, revision, source, positives, negatives, "
            "positive_movie_ids, file_name, is_active) values (%s, %s, 'seed', 3, 0, '[]', %s, "
            "true)",
            [sha, "b" * 64, f"{sha}.npz"],
        )
        registered = doctor.check_taste_profile()
        assert registered.status == "PASS"
        assert registered.detail.endswith("; registered build 1")

        _profile(profile)  # another file in its place
        other = doctor.check_taste_profile()
        assert other.status == "WARN"
        assert f"is not the registered build (profile 1, {sha[:12]})" in other.detail

        conn.execute("update taste_profiles set is_active = false")
        none_active = doctor.check_taste_profile()
        assert (
            none_active.status == "WARN" and "no registered build is active" in none_active.detail
        )


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
    assert "schema at 0007 (newest)" in result.detail


@pytest.mark.db
def test_database_warns_when_not_migrated(
    monkeypatch: pytest.MonkeyPatch, empty_database_url: str
) -> None:
    _point_settings_at(monkeypatch, empty_database_url)
    result = doctor.check_database()
    assert result.status == "WARN"
    assert "schema at no revision, newest is 0007" in result.detail
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


def test_backups_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from marquee.artwork import backup

    monkeypatch.setattr(doctor.settings, "DATA_DIR", tmp_path)
    fresh = doctor.check_backups()
    assert fresh.status == "PASS" and "first deploy creates it" in fresh.detail
    assert not (tmp_path / "backups").exists()  # the doctor creates nothing

    backup.store(b"x" * 2048, 1, tmp_path / "backups")
    kept = doctor.check_backups()
    assert kept.status == "PASS" and "1 poster backup(s), 2.0 KiB" in kept.detail

    (tmp_path / "backups").chmod(0o500)
    try:
        assert doctor.check_backups().status == "FAIL"
    finally:
        (tmp_path / "backups").chmod(0o700)


# ── Production, roles, and the CUDA kernel cache ──────────────────────────


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.settings, "MARQUEE_ENVIRONMENT", "production")


@pytest.fixture
def development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.settings, "MARQUEE_ENVIRONMENT", "development")


def test_inference_on_the_gpu_passes_everywhere(production: None) -> None:
    assert doctor._provider_verdict(None, "CUDAExecutionProvider")[0] == "PASS"


def test_inference_on_the_cpu_fails_in_production(production: None) -> None:
    for requested in (None, "CPUExecutionProvider"):
        status, detail = doctor._provider_verdict(requested, "CPUExecutionProvider")
        assert status == "FAIL"
        assert "in production inference runs on the GPU" in detail


def test_inference_on_the_cpu_only_warns_in_development(development: None) -> None:
    assert doctor._provider_verdict(None, "CPUExecutionProvider")[0] == "WARN"
    assert doctor._provider_verdict("CPUExecutionProvider", "CPUExecutionProvider") == (
        "PASS",
        "CPUExecutionProvider active as requested",
    )
    assert doctor._provider_verdict("CUDAExecutionProvider", "CPUExecutionProvider")[0] == "FAIL"


def test_ocr_on_the_cpu_fails_in_production(production: None) -> None:
    assert doctor._ocr_verdict("gpu:0", True, None) == ("PASS", "PaddleOCR loaded on gpu:0")
    for gpu_visible in (True, False):
        status, detail = doctor._ocr_verdict("cpu", gpu_visible, "no CUDA build")
        assert status == "FAIL"
        assert detail.startswith("PaddleOCR loaded on CPU (no CUDA build)")


def test_ocr_on_the_cpu_warns_or_passes_in_development(development: None) -> None:
    assert doctor._ocr_verdict("cpu", True, "OCR_DEVICE=cpu") == (
        "WARN",
        "PaddleOCR loaded on CPU on a CUDA host (OCR_DEVICE=cpu)",
    )
    assert doctor._ocr_verdict("cpu", False, None) == ("PASS", "PaddleOCR loaded on cpu")


def test_no_gpu_fails_in_production_and_warns_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor, "_nvidia_gpus", lambda: [])
    monkeypatch.setattr(doctor.settings, "MARQUEE_ENVIRONMENT", "production")
    assert doctor.check_gpu() == CheckResult(
        "GPU", "FAIL", "NVML unavailable or no NVIDIA GPU visible; production needs one"
    )
    monkeypatch.setattr(doctor.settings, "MARQUEE_ENVIRONMENT", "development")
    assert doctor.check_gpu().status == "WARN"


def _mountinfo(path: Path, *lines: str) -> Path:
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def test_the_mount_table_says_whether_a_folder_is_read_only(tmp_path: Path) -> None:
    table = _mountinfo(
        tmp_path / "mountinfo",
        "1 0 0:1 / / rw,relatime - overlay overlay rw,lowerdir=a",
        "2 1 0:2 / /media/movies ro,relatime master:1 - cifs //nas/media rw,vers=3.0",
        "3 1 0:3 / /data rw,relatime - ext4 /dev/sdb rw",
        "4 1 0:4 / /media/movies/4K rw,relatime - cifs //nas/4k rw",
        "5 1 0:5 / /with\\040space rw - tmpfs tmpfs ro",
    )
    assert doctor._mount_read_only(Path("/media/movies/1080p/A (2001)"), table) is True
    assert doctor._mount_read_only(Path("/media/movies"), table) is True
    assert doctor._mount_read_only(Path("/media/movies/4K"), table) is False  # deeper mount
    assert doctor._mount_read_only(Path("/media/moviesX"), table) is False  # not a prefix
    assert doctor._mount_read_only(Path("/data/runs"), table) is False
    assert doctor._mount_read_only(Path("/with space/x"), table) is True  # superblock ro
    assert doctor._mount_read_only(Path("/x"), tmp_path / "absent") is None


def test_the_later_of_two_mounts_on_one_point_decides(tmp_path: Path) -> None:
    table = _mountinfo(
        tmp_path / "mountinfo",
        "1 0 0:1 / / rw - ext4 /dev/sda rw",
        "2 1 0:2 / /media/movies rw - cifs //nas/media rw",
        "3 2 0:3 / /media/movies ro - cifs //nas/media rw",
    )
    assert doctor._mount_read_only(Path("/media/movies"), table) is True


@pytest.mark.parametrize(
    ("role", "environment", "status", "says"),
    [
        ("api", "production", "PASS", "mounted read-only, as the api needs (it only reads)"),
        ("worker", "production", "FAIL", "not writable (mounted read-only), so the worker"),
        ("worker", "development", "WARN", "not writable (mounted read-only), so the worker"),
        ("all", "production", "WARN", "readable but not writable (mounted read-only)"),
    ],
)
def test_a_read_only_library_by_role(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    role: doctor.Role,
    environment: str,
    status: str,
    says: str,
) -> None:
    _make_library(tmp_path)
    monkeypatch.setattr(doctor.settings, "MARQUEE_ENVIRONMENT", environment)
    monkeypatch.setattr(doctor, "_mount_read_only", lambda path: True)
    result = doctor._describe_root(tmp_path, role)
    assert result[0] == status
    assert says in result[1]


def test_a_writable_library_passes_for_every_role(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, production: None
) -> None:
    _make_library(tmp_path)
    monkeypatch.setattr(doctor, "_mount_read_only", lambda path: False)
    monkeypatch.setattr(doctor.os, "access", lambda path, mode: True)
    assert doctor._describe_root(tmp_path, "worker") == (
        "PASS",
        f"{tmp_path}: 1080p 2, 4K 1 (3 folders); writable",
    )
    assert doctor._describe_root(tmp_path, "api")[0] == "PASS"


def test_permission_bits_that_deny_writing_are_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, production: None
) -> None:
    _make_library(tmp_path)
    real_access = os.access
    monkeypatch.setattr(doctor, "_mount_read_only", lambda path: False)
    monkeypatch.setattr(
        doctor.os,
        "access",
        lambda path, mode: False if mode & os.W_OK else real_access(path, mode),
    )
    status, detail = doctor._describe_root(tmp_path, "worker")
    assert status == "FAIL"
    assert f"its permission bits do not let user {os.geteuid()} write" in detail


def test_the_api_role_leaves_out_the_engine_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _make_library(tmp_path)
    monkeypatch.setattr(doctor.settings, "MOVIE_ROOTS", [tmp_path])
    monkeypatch.setattr(doctor, "_nvidia_gpus", lambda: [])
    monkeypatch.setattr(doctor, "_mount_read_only", lambda path: True)
    monkeypatch.setattr(doctor, "CHECKS", (doctor.check_gpu, doctor.check_media_roots))
    assert [r.name for r in doctor.run_checks("api")] == ["Media roots"]
    assert [r.name for r in doctor.run_checks("worker")] == ["GPU", "Media roots"]
    monkeypatch.setattr(doctor.settings, "MARQUEE_ROLE", "api")
    (only,) = doctor.run_checks()
    assert (only.name, only.status) == ("Media roots", "PASS")


def test_the_settings_line_names_the_environment_and_the_role(
    monkeypatch: pytest.MonkeyPatch, production: None
) -> None:
    monkeypatch.setattr(doctor, "ENV_FILE", None)
    _configure_every_optional_setting(monkeypatch)
    assert doctor.check_settings("worker").detail.endswith("environment production, role worker")


def test_the_cuda_kernel_cache_against_its_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cuda-jit"
    monkeypatch.setattr(doctor.settings, "CUDA_CACHE_PATH", cache)
    monkeypatch.setattr(doctor.settings, "CUDA_CACHE_MAXSIZE", 10_000)
    empty = doctor.check_cuda_kernel_cache()
    assert empty.status == "PASS" and "first run about four times slower" in empty.detail
    (cache / "sm_120").mkdir(parents=True)
    (cache / "sm_120" / "kernel").write_bytes(b"x" * 2048)
    assert doctor.check_cuda_kernel_cache() == CheckResult(
        "CUDA kernel cache", "PASS", f"{cache}: 2.0 KiB of a 9.8 KiB limit"
    )
    (cache / "index").write_bytes(b"x" * 7000)
    near = doctor.check_cuda_kernel_cache()
    assert near.status == "WARN" and "evicts kernels" in near.detail


# ── Backups ───────────────────────────────────────────────────────────────


def _backup_state(monkeypatch: pytest.MonkeyPatch, *, server: int | None, tools: object) -> None:
    from marquee.maintenance import dumps

    def state() -> dumps.DatabaseState:
        if server is None:
            raise dumps.DumpError("the database marquee@db:5432/marquee cannot be reached: x")
        return dumps.DatabaseState(server, "0007", True, 0)

    def find(server_major: int | None = None) -> dumps.Tools:
        if isinstance(tools, Exception):
            raise tools
        return dumps.Tools(paths={}, major=tools)  # type: ignore[arg-type]

    monkeypatch.setattr(dumps, "database_state", state)
    monkeypatch.setattr(dumps, "find_tools", find)


def test_backup_tools_fail_the_worker_in_production_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from marquee.maintenance import dumps

    _backup_state(monkeypatch, server=19, tools=19)
    good = doctor.check_backup_tools("worker")
    assert (good.status, good.detail) == (
        "PASS",
        "pg_dump, pg_restore, psql 19; the database server is 19",
    )

    old = dumps.DumpToolsError("pg_dump is version 17 and the database server is version 19")
    _backup_state(monkeypatch, server=19, tools=old)
    assert doctor.check_backup_tools("worker").status == "WARN"
    monkeypatch.setattr(doctor.settings, "MARQUEE_ENVIRONMENT", "production")
    failed = doctor.check_backup_tools("worker")
    assert failed.status == "FAIL" and "version 17" in failed.detail
    assert doctor.check_backup_tools("api").status == "WARN"

    _backup_state(monkeypatch, server=None, tools=19)
    unknown = doctor.check_backup_tools("worker")
    assert unknown.status == "WARN" and "its version is unknown" in unknown.detail


def test_the_newest_dump_and_its_age(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from marquee.maintenance import dumps

    monkeypatch.setattr(doctor.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(doctor.settings, "DB_BACKUP_INTERVAL_HOURS", 24)
    now = datetime(2026, 11, 27, 12, tzinfo=UTC)

    none = doctor.check_db_dumps(now)
    assert none.status == "WARN" and "none yet" in none.detail

    folder = doctor.settings.db_backups_dir
    folder.mkdir(parents=True)
    name = dumps.dump_name("scheduled", now - timedelta(hours=5), "0007")
    (folder / name).write_bytes(b"x" * 2048)
    fresh = doctor.check_db_dumps(now)
    assert fresh.status == "PASS"
    assert fresh.detail.startswith(f"newest {name}, 2.0 KiB, 5.0 h old; 1 dump(s) in ")
    assert fresh.detail.endswith("one every 24 h")

    late = doctor.check_db_dumps(now + timedelta(hours=44))
    assert late.status == "WARN" and "older than two intervals (48 h)" in late.detail

    monkeypatch.setattr(doctor.settings, "DB_BACKUP_INTERVAL_HOURS", 0)
    off = doctor.check_db_dumps(now)
    assert off.status == "WARN" and "the scheduled dump is off" in off.detail
