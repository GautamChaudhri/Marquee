from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.api.results import BackupInfo
from marquee.config import settings
from marquee.core import pipeline_config as pipeline_config_module
from marquee.core.backup import BackupVerificationError, OfflineRestoreError, backup_service
from marquee.core.jobs.definitions import DisabledJobDefinitionError
from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.pipeline_config import (
    PipelineSettings,
    migrate_legacy_runtime_state,
    pipeline_settings,
)
from marquee.main import app


@pytest.fixture
def backup_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(settings, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(settings, "BACKUP_DIR", str(backup_dir))
    return data_dir, backup_dir


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _write_dump(path: Path, payload: bytes = b"pg_dump") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _seed_managed_state(data_dir: Path) -> None:
    (data_dir / "feedback").mkdir(parents=True, exist_ok=True)
    (data_dir / "feedback" / "labels.jsonl").write_text('{"label": 1}\n', encoding="utf-8")

    positive_dir = data_dir / "training" / "positive"
    positive_dir.mkdir(parents=True, exist_ok=True)
    (positive_dir / "approved.jpg").write_bytes(b"jpg")

    negative_dir = data_dir / "training" / "negative"
    negative_dir.mkdir(parents=True, exist_ok=True)
    (negative_dir / "rejected.jpg").write_bytes(b"jpg")

    ml_dir = data_dir / "ml"
    ml_dir.mkdir(parents=True, exist_ok=True)
    (ml_dir / "taste_profile.clip-vit-b-32.npz").write_bytes(b"profile")
    (ml_dir / "learned_head.clip-vit-b-32.npz").write_bytes(b"head")
    (ml_dir / "zeroshot_axes.clip-vit-b-32.npz").write_bytes(b"axes")

    posters_dir = data_dir / "cache" / "posters"
    posters_dir.mkdir(parents=True, exist_ok=True)
    (posters_dir / "poster.jpg").write_bytes(b"poster")

    embeddings_dir = data_dir / "cache" / "embeddings" / "clip-vit-b-32"
    embeddings_dir.mkdir(parents=True, exist_ok=True)
    (embeddings_dir / "candidate.npz").write_bytes(b"embedding")

    history_dir = data_dir / "cache" / "taste_map_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / "20260617.npz").write_bytes(b"history")

    (data_dir / "cache").mkdir(parents=True, exist_ok=True)
    (data_dir / "cache" / "taste_map.clip-vit-b-32.npz").write_bytes(b"map")

    archive_dir = data_dir / "runs" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "run.json").write_text("{}", encoding="utf-8")


    (data_dir / "staging").mkdir(parents=True, exist_ok=True)
    (data_dir / "staging" / "scratch.bin").write_bytes(b"scratch")


def _make_backup_dir(root: Path, backup_id: str) -> None:
    backup_root = root / backup_id
    backup_root.mkdir(parents=True, exist_ok=True)
    db = b"db"
    state_path = backup_root / "state.tar.gz"
    (backup_root / "marquee.dump").write_bytes(db)
    with tarfile.open(state_path, "w:gz"):
        pass
    state = state_path.read_bytes()
    (backup_root / "manifest.json").write_text(
        json.dumps(
            {
                "backup_format_version": 1,
                "backup_id": backup_id,
                "created_at": "2026-06-17T00:00:00+00:00",
                "database": {
                    "name": "marquee.dump",
                    "format": "postgresql-custom",
                    "size": len(db),
                    "sha256": hashlib.sha256(db).hexdigest(),
                },
                "data": {
                    "archive": {
                        "name": "state.tar.gz",
                        "format": "tar-gzip",
                        "size": len(state),
                        "sha256": hashlib.sha256(state).hexdigest(),
                    },
                    "members": [],
                },
                "db_size": len(db),
                "state_size": len(state),
            }
        ),
        encoding="utf-8",
    )


def test_pipeline_settings_default_runtime_state_paths_are_under_data():
    cfg = PipelineSettings(AI_MODEL="siglip-so400m")

    assert str(cfg.FEEDBACK_LABELS_PATH).endswith("data/feedback/labels.jsonl")
    assert str(cfg.TRAINING_DATA_DIR).endswith("data/taste_seeding/movies")
    assert str(cfg.NEGATIVE_DATA_DIR).endswith("data/training/negative")
    assert str(cfg.TASTE_PROFILE_PATH).endswith("data/ml/taste_profile.siglip-so400m.npz")
    assert str(cfg.LEARNED_HEAD_PATH).endswith("data/ml/learned_head.siglip-so400m.npz")
    assert str(cfg.ZEROSHOT_AXES_PATH).endswith("data/ml/zeroshot_axes.siglip-so400m.npz")


def test_migrate_legacy_runtime_state_moves_feedback_and_training(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    legacy_root = tmp_path / "experiments"
    monkeypatch.setattr(pipeline_config_module, "_LEGACY_EXPERIMENTS_DIR", legacy_root)
    monkeypatch.setattr(
        pipeline_settings, "FEEDBACK_LABELS_PATH", tmp_path / "data" / "feedback" / "labels.jsonl"
    )
    monkeypatch.setattr(
        pipeline_settings, "TRAINING_DATA_DIR", tmp_path / "data" / "training" / "positive"
    )
    monkeypatch.setattr(
        pipeline_settings, "NEGATIVE_DATA_DIR", tmp_path / "data" / "training" / "negative"
    )

    (legacy_root / "feedback").mkdir(parents=True, exist_ok=True)
    (legacy_root / "feedback" / "labels.jsonl").write_text('{"label": 1}\n', encoding="utf-8")
    (legacy_root / "training_data").mkdir(parents=True, exist_ok=True)
    (legacy_root / "training_data" / "poster.jpg").write_bytes(b"poster")
    (legacy_root / "negative_data").mkdir(parents=True, exist_ok=True)
    (legacy_root / "negative_data" / "reject.jpg").write_bytes(b"reject")

    messages = migrate_legacy_runtime_state()

    assert len(messages) == 3
    assert (tmp_path / "data" / "feedback" / "labels.jsonl").exists()
    assert (tmp_path / "data" / "training" / "positive" / "poster.jpg").exists()
    assert (tmp_path / "data" / "training" / "negative" / "reject.jpg").exists()
    assert not (legacy_root / "training_data").exists()


def test_migrate_legacy_runtime_state_conflict_raises_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    legacy_root = tmp_path / "experiments"
    current_labels = tmp_path / "data" / "feedback" / "labels.jsonl"
    monkeypatch.setattr(pipeline_config_module, "_LEGACY_EXPERIMENTS_DIR", legacy_root)
    monkeypatch.setattr(pipeline_settings, "FEEDBACK_LABELS_PATH", current_labels)

    (legacy_root / "feedback").mkdir(parents=True, exist_ok=True)
    (legacy_root / "feedback" / "labels.jsonl").write_text("legacy\n", encoding="utf-8")
    current_labels.parent.mkdir(parents=True, exist_ok=True)
    current_labels.write_text("current\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Resolve manually, then restart Marquee"):
        migrate_legacy_runtime_state()


@pytest.mark.asyncio
async def test_create_backup_creates_directory_and_excludes_transient_files(
    backup_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    data_dir, backup_dir = backup_paths
    _seed_managed_state(data_dir)
    monkeypatch.setattr(backup_service, "_snapshot_db", lambda path: _write_dump(path, b"dump"))

    result = await backup_service.create_backup()

    backup_root = Path(result.backup_dir)
    assert backup_root == backup_dir / result.backup_id
    assert (backup_root / "marquee.dump").is_file()
    assert (backup_root / "state.tar.gz").is_file()
    assert (backup_root / "manifest.json").is_file()

    with tarfile.open(backup_root / "state.tar.gz", "r:gz") as archive:
        members = archive.getnames()

    assert "feedback/labels.jsonl" in members
    assert "training/positive/approved.jpg" in members
    assert "ml/taste_profile.clip-vit-b-32.npz" in members
    assert "cache/posters/poster.jpg" in members
    assert "cache/embeddings/clip-vit-b-32/candidate.npz" in members
    assert "cache/taste_map.clip-vit-b-32.npz" in members
    assert "runs/archive/run.json" in members
    assert "staging/scratch.bin" not in members
    assert not any(name.startswith("backups/") for name in members)


@pytest.mark.asyncio
async def test_restore_backup_reports_restart_required_after_validation(
    backup_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    data_dir, _backup_dir = backup_paths
    _seed_managed_state(data_dir)
    monkeypatch.setattr(backup_service, "_snapshot_db", lambda path: _write_dump(path, b"dump"))
    monkeypatch.setattr(backup_service, "_validate_backup_db", lambda path: None)

    result = await backup_service.create_backup()

    restored = await backup_service.restore_backup(result.backup_id)

    assert restored.backup_id == result.backup_id
    assert restored.restored is False
    assert restored.restored_db is False
    assert restored.restored_state is False
    assert restored.restart_required is True


@pytest.mark.asyncio
async def test_restore_backup_validates_only_and_never_mutates_targets(
    backup_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    data_dir, _backup_dir = backup_paths
    _seed_managed_state(data_dir)
    monkeypatch.setattr(backup_service, "_snapshot_db", lambda path: _write_dump(path, b"dump"))
    monkeypatch.setattr(backup_service, "_validate_backup_db", lambda path: None)

    def must_not_restore(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("running-process restore must not mutate a target")

    monkeypatch.setattr(backup_service, "_restore_sync", must_not_restore)
    monkeypatch.setattr(backup_service, "_extract_state_archive", must_not_restore)

    result = await backup_service.create_backup()
    restored = await backup_service.restore_backup(result.backup_id)

    assert restored.restored is False
    assert restored.restored_db is False
    assert restored.restored_state is False


def test_only_system_noop_is_dispatch_enabled_and_executable() -> None:
    assert JOB_DEFINITION_REGISTRY.enabled_types == {
        "system_noop",
        "library_sync",
        "poster_pipeline",
        "letterbox_detect",
        "letterbox_detect_episode",
        "letterbox_detect_tv_scope",
        "subtitle_scan",
        "subtitle_policy_audit",
        "dovi_analyze",
        "learned_head_train",
        "poster_rescan",
        "taste_map",
        "taste_rebuild",
    }
    assert set(EXECUTION_HANDLERS) == {
        "system_noop",
        "library_sync",
        "poster_pipeline",
        "letterbox_detect",
        "letterbox_detect_episode",
        "letterbox_detect_tv_scope",
        "subtitle_scan",
        "subtitle_policy_audit",
        "dovi_analyze",
        "learned_head_train",
        "poster_rescan",
        "taste_map",
        "taste_rebuild",
    }

    for definition in JOB_DEFINITION_REGISTRY:
        if not definition.enabled:
            with pytest.raises(DisabledJobDefinitionError, match="dispatch-disabled"):
                JOB_DEFINITION_REGISTRY.for_dispatch(
                    definition.job_type, entrypoint=definition.entrypoint
                )


@pytest.mark.asyncio
async def test_backup_create_route_cannot_reach_backup_service(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    async def must_not_create() -> None:
        raise AssertionError("legacy API route bypassed the fail-closed job facade")

    monkeypatch.setattr(backup_service, "create_backup", must_not_create)

    response = await client.post("/api/system/backup")

    assert response.status_code == 503
    assert response.json()["code"] == "job_platform_unmigrated"


def test_backup_scheduler_has_no_production_startup_caller() -> None:
    callers = [
        path
        for path in Path("marquee").rglob("*.py")
        if "backup_service.scheduler_loop(" in path.read_text(encoding="utf-8")
    ]

    assert callers == []


def test_pg_dump_uses_pgpass_without_putting_password_in_argv(
    backup_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    _data_dir, backup_dir = backup_paths
    secret = "unsafe-password"
    monkeypatch.setattr(settings, "DB_URL", f"postgresql+asyncpg://marquee:{secret}@db.example:5544/test")
    captured: dict[str, object] = {}

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(argv: list[str], **kwargs: object) -> Result:
        captured["argv"] = argv
        pgpass = Path(str(kwargs["env"]["PGPASSFILE"]))  # type: ignore[index]
        captured["pgpass_mode"] = pgpass.stat().st_mode & 0o777
        captured["pgpass_contents"] = pgpass.read_text(encoding="utf-8")
        return Result()

    monkeypatch.setattr("marquee.core.backup.subprocess.run", fake_run)
    backup_service._snapshot_db(backup_dir / "stage" / "marquee.dump")

    assert secret not in " ".join(captured["argv"])  # type: ignore[arg-type]
    assert captured["pgpass_mode"] == 0o600
    assert secret in captured["pgpass_contents"]  # type: ignore[operator]
    assert not (backup_dir / "stage" / ".pgpass").exists()


@pytest.mark.asyncio
async def test_backup_verification_rejects_tampered_component(
    backup_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    data_dir, _backup_dir = backup_paths
    _seed_managed_state(data_dir)
    monkeypatch.setattr(backup_service, "_snapshot_db", lambda path: _write_dump(path, b"dump"))
    result = await backup_service.create_backup()
    Path(result.db_path).write_bytes(b"tampered")

    with pytest.raises(BackupVerificationError, match="checksum"):
        await backup_service.restore_backup(result.backup_id)


@pytest.mark.asyncio
async def test_backup_rejects_symlinked_managed_member(
    backup_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    data_dir, _backup_dir = backup_paths
    (data_dir / "feedback").mkdir(parents=True)
    outside = data_dir.parent / "outside.txt"
    outside.write_text("not managed", encoding="utf-8")
    (data_dir / "feedback" / "unsafe.txt").symlink_to(outside)
    monkeypatch.setattr(backup_service, "_snapshot_db", lambda path: _write_dump(path, b"dump"))

    with pytest.raises(BackupVerificationError, match="confined root"):
        await backup_service.create_backup()


@pytest.mark.asyncio
async def test_offline_restore_requires_confirmed_fresh_owned_targets(
    backup_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    _data_dir, _backup_dir = backup_paths
    restore_root = _data_dir.parent / "restore-root"
    restore_root.mkdir()
    monkeypatch.setenv("MARQUEE_RESTORE_ROOT", str(restore_root))

    with pytest.raises(OfflineRestoreError, match="confirmation"):
        await backup_service.restore_offline(
            "missing",
            target_database="restored",
            confirm_database_name="different",
            target_data_dir=restore_root / "target",
            allow_create_target=True,
        )
    with pytest.raises(OfflineRestoreError, match="approved root"):
        await backup_service.restore_offline(
            "missing",
            target_database="restored",
            confirm_database_name="restored",
            target_data_dir=_data_dir.parent / "outside",
            allow_create_target=True,
        )


@pytest.mark.asyncio
async def test_offline_restore_certifies_fresh_disposable_targets(
    backup_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
):
    data_dir, _backup_dir = backup_paths
    _seed_managed_state(data_dir)
    restore_root = data_dir.parent / "restore-root"
    restore_root.mkdir()
    monkeypatch.setenv("MARQUEE_RESTORE_ROOT", str(restore_root))
    target_database = f"jmc3c_restore_{uuid.uuid4().hex[:16]}"
    target_data_dir = restore_root / "restored-data"

    backup = await backup_service.create_backup()
    try:
        result = await backup_service.restore_offline(
            backup.backup_id,
            target_database=target_database,
            confirm_database_name=target_database,
            target_data_dir=target_data_dir,
            allow_create_target=True,
        )
        assert result["restored"] is True
        assert (target_data_dir / "feedback" / "labels.jsonl").read_text(encoding="utf-8") == '{"label": 1}\n'
    finally:
        await backup_service._drop_owned_database(target_database)
        shutil.rmtree(target_data_dir, ignore_errors=True)


def test_rotate_backups_keeps_latest_per_day_for_retention_window(backup_paths: tuple[Path, Path]):
    _data_dir, backup_dir = backup_paths
    for backup_id in (
        "20260617-010000",
        "20260617-220000",
        "20260616-030000",
        "20260615-030000",
    ):
        _make_backup_dir(backup_dir, backup_id)

    deleted = backup_service._rotate_backups_sync(2)

    assert deleted == 2
    assert (backup_dir / "20260617-220000").exists()
    assert (backup_dir / "20260616-030000").exists()
    assert not (backup_dir / "20260617-010000").exists()
    assert not (backup_dir / "20260615-030000").exists()


@pytest.mark.asyncio
async def test_incomplete_backup_is_not_listed(backup_paths: tuple[Path, Path]):
    _data_dir, backup_dir = backup_paths
    incomplete = backup_dir / "20260617-120000"
    incomplete.mkdir(parents=True)
    (incomplete / "manifest.json").write_text("{}", encoding="utf-8")

    assert await backup_service.list_backups() == []


@pytest.mark.asyncio
async def test_backup_api_endpoints(client, monkeypatch: pytest.MonkeyPatch):
    async def fake_list():
        return [
            BackupInfo(
                backup_id="20260617-120000",
                created_at="2026-06-17T12:00:00+00:00",
                db_size=123,
                state_size=456,
            )
        ]

    monkeypatch.setattr(backup_service, "list_backups", fake_list)

    create_response = await client.post("/api/system/backup")
    assert create_response.status_code == 503
    assert create_response.json()["code"] == "job_platform_unmigrated"

    list_response = await client.get("/api/system/backups")
    assert list_response.status_code == 200
    assert list_response.json()[0]["backup_id"] == "20260617-120000"

    restore_response = await client.post("/api/system/restore", params={"backup_id": "20260617-120000"})
    assert restore_response.status_code == 404

    delete_response = await client.delete("/api/system/backups/20260617-120000")
    assert delete_response.status_code == 404
