from __future__ import annotations

import json
import sqlite3
import tarfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from marquee.api.results import BackupInfo, BackupResult, RestoreResult
from marquee.config import settings
from marquee.core import pipeline_config as pipeline_config_module
from marquee.core.backup import backup_service
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


def _write_db(path: Path, values: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE items (value TEXT NOT NULL)")
        conn.executemany("INSERT INTO items (value) VALUES (?)", [(value,) for value in values])
        conn.commit()


def _read_db_values(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        return [row[0] for row in conn.execute("SELECT value FROM items ORDER BY rowid")]


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

    (data_dir / "pipeline_overrides.json").write_text('{"OCR_WORKERS": 2}', encoding="utf-8")

    (data_dir / "staging").mkdir(parents=True, exist_ok=True)
    (data_dir / "staging" / "scratch.bin").write_bytes(b"scratch")

    (data_dir / "marquee.db-wal").write_bytes(b"wal")
    (data_dir / "marquee.db-shm").write_bytes(b"shm")


def _make_backup_dir(root: Path, backup_id: str) -> None:
    backup_root = root / backup_id
    backup_root.mkdir(parents=True, exist_ok=True)
    (backup_root / "marquee.db").write_bytes(b"db")
    (backup_root / "state.tar.gz").write_bytes(b"state")
    (backup_root / "manifest.json").write_text(
        json.dumps(
            {
                "backup_id": backup_id,
                "created_at": "2026-06-17T00:00:00+00:00",
                "db_size": 2,
                "state_size": 5,
            }
        ),
        encoding="utf-8",
    )


def test_pipeline_settings_default_runtime_state_paths_are_under_data():
    cfg = PipelineSettings(AI_MODEL="siglip-so400m")

    assert str(cfg.FEEDBACK_LABELS_PATH).endswith("data/feedback/labels.jsonl")
    assert str(cfg.TRAINING_DATA_DIR).endswith("data/training/positive")
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
    backup_paths: tuple[Path, Path]
):
    data_dir, backup_dir = backup_paths
    _write_db(data_dir / "marquee.db", ["before"])
    _seed_managed_state(data_dir)

    result = await backup_service.create_backup()

    backup_root = Path(result.backup_dir)
    assert backup_root == backup_dir / result.backup_id
    assert (backup_root / "marquee.db").is_file()
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
    assert "pipeline_overrides.json" in members
    assert "staging/scratch.bin" not in members
    assert "marquee.db-wal" not in members
    assert "marquee.db-shm" not in members
    assert not any(name.startswith("backups/") for name in members)


@pytest.mark.asyncio
async def test_restore_backup_exactly_replaces_managed_state_and_db(
    backup_paths: tuple[Path, Path]
):
    data_dir, _backup_dir = backup_paths
    db_path = data_dir / "marquee.db"
    _write_db(db_path, ["before"])
    _seed_managed_state(data_dir)

    result = await backup_service.create_backup()

    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO items (value) VALUES (?)", ("after",))
        conn.commit()

    (data_dir / "feedback" / "labels.jsonl").write_text('{"label": 0}\n', encoding="utf-8")
    (data_dir / "training" / "positive" / "newer.jpg").write_bytes(b"newer")
    (data_dir / "cache" / "taste_map.clip-vit-b-32.npz").write_bytes(b"changed")
    (data_dir / "staging" / "scratch.bin").write_bytes(b"keep-me")

    restored = await backup_service.restore_backup(result.backup_id)

    assert restored.restored is True
    assert restored.restart_required is True
    assert _read_db_values(db_path) == ["before"]
    assert (data_dir / "feedback" / "labels.jsonl").read_text(encoding="utf-8") == '{"label": 1}\n'
    assert not (data_dir / "training" / "positive" / "newer.jpg").exists()
    assert (data_dir / "cache" / "taste_map.clip-vit-b-32.npz").read_bytes() == b"map"
    assert (data_dir / "staging" / "scratch.bin").read_bytes() == b"keep-me"


def test_rotate_backups_keeps_latest_per_day_for_retention_window(
    backup_paths: tuple[Path, Path]
):
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
async def test_backup_api_endpoints(client, monkeypatch: pytest.MonkeyPatch):
    async def fake_create():
        return BackupResult(
            backup_id="20260617-120000",
            created_at="2026-06-17T12:00:00+00:00",
            backup_dir="/tmp/backups/20260617-120000",
            db_path="/tmp/backups/20260617-120000/marquee.db",
            state_path="/tmp/backups/20260617-120000/state.tar.gz",
            manifest_path="/tmp/backups/20260617-120000/manifest.json",
            db_size=123,
            state_size=456,
        )

    async def fake_list():
        return [
            BackupInfo(
                backup_id="20260617-120000",
                created_at="2026-06-17T12:00:00+00:00",
                backup_dir="/tmp/backups/20260617-120000",
                db_size=123,
                state_size=456,
            )
        ]

    async def fake_restore(backup_id: str):
        assert backup_id == "20260617-120000"
        return RestoreResult(
            backup_id=backup_id,
            restored=True,
            restored_db=True,
            restored_state=True,
            restart_required=True,
        )

    async def fake_delete(backup_id: str):
        return backup_id == "20260617-120000"

    monkeypatch.setattr(backup_service, "create_backup", fake_create)
    monkeypatch.setattr(backup_service, "list_backups", fake_list)
    monkeypatch.setattr(backup_service, "restore_backup", fake_restore)
    monkeypatch.setattr(backup_service, "delete_backup", fake_delete)

    create_response = await client.post("/api/system/backup")
    assert create_response.status_code == 200
    assert create_response.json()["backup_id"] == "20260617-120000"

    list_response = await client.get("/api/system/backups")
    assert list_response.status_code == 200
    assert list_response.json()[0]["backup_id"] == "20260617-120000"

    restore_response = await client.post("/api/system/restore", params={"backup_id": "20260617-120000"})
    assert restore_response.status_code == 202
    assert restore_response.json()["restart_required"] is True

    delete_response = await client.delete("/api/system/backups/20260617-120000")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"backup_id": "20260617-120000", "deleted": True}
