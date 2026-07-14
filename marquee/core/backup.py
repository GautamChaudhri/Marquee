"""Internal backup service for local rollback snapshots."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from marquee.api.results import BackupInfo, BackupResult, RestoreResult
from marquee.config import settings

logger = logging.getLogger(__name__)

_TMP_DIR_NAME = ".tmp"
_DB_FILENAME = "marquee.dump"
_STATE_FILENAME = "state.tar.gz"
_MANIFEST_FILENAME = "manifest.json"


class BackupService:
    def __init__(self) -> None:
        self._operation_lock = asyncio.Lock()

    @property
    def backup_dir(self) -> Path:
        return settings.backup_dir_path

    @property
    def data_dir(self) -> Path:
        return settings.data_dir_path

    def managed_directory_targets(self) -> list[Path]:
        return [
            Path("feedback"),
            Path("training"),
            Path("ml"),
            Path("cache") / "posters",
            Path("cache") / "embeddings",
            Path("cache") / "taste_map_history",
            Path("runs") / "archive",
        ]

    def managed_file_targets(self) -> list[Path]:
        return []

    def managed_glob_targets(self) -> list[tuple[Path, str]]:
        return [(Path("cache"), "taste_map*.npz")]

    async def create_backup(self) -> BackupResult:
        async with self._operation_lock:
            result = await asyncio.to_thread(self._create_backup_sync)
            await asyncio.to_thread(self._rotate_backups_sync, settings.BACKUP_RETENTION_DAYS)
            return result

    async def list_backups(self) -> list[BackupInfo]:
        async with self._operation_lock:
            return self._list_backups_sync()

    async def delete_backup(self, backup_id: str) -> bool:
        async with self._operation_lock:
            return self._delete_backup_sync(backup_id)

    async def restore_backup(self, backup_id: str) -> RestoreResult:
        """Validate a restore request; execution belongs to maintenance mode.

        Restoring the database that owns this request cannot be safely done by
        a normal API/worker process.  The dedicated maintenance command drains
        services and invokes pg_restore before they restart.
        """
        async with self._operation_lock:
            backup_root = self._backup_root(backup_id)
            await asyncio.to_thread(self._validate_backup_pair, backup_root)
            await asyncio.to_thread(self._validate_backup_db, backup_root / _DB_FILENAME)
            return RestoreResult(
                backup_id=backup_id,
                restored=False,
                restored_db=False,
                restored_state=False,
                restart_required=True,
            )

    async def scheduler_loop(self) -> None:
        if settings.DEBUG or settings.BACKUP_INTERVAL_HOURS <= 0:
            return
        delay = max(0, settings.BACKUP_INITIAL_DELAY_SECONDS)
        if delay:
            await asyncio.sleep(delay)
        interval = max(3600, settings.BACKUP_INTERVAL_HOURS * 3600)
        while True:
            try:
                await self.create_backup()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Scheduled backup failed", exc_info=True)
            await asyncio.sleep(interval)

    def _timestamp(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    def _backup_root(self, backup_id: str) -> Path:
        return self.backup_dir / backup_id

    def _temp_root(self, backup_id: str) -> Path:
        return self.backup_dir / _TMP_DIR_NAME / backup_id

    def _list_backups_sync(self) -> list[BackupInfo]:
        backups: list[BackupInfo] = []
        if not self.backup_dir.exists():
            return backups
        for path in sorted(self.backup_dir.iterdir(), reverse=True):
            if not path.is_dir() or path.name == _TMP_DIR_NAME:
                continue
            try:
                manifest = self._read_manifest(path)
                backups.append(
                    BackupInfo(
                        backup_id=manifest["backup_id"],
                        created_at=manifest["created_at"],
                        backup_dir=str(path),
                        db_size=int(manifest["db_size"]),
                        state_size=int(manifest["state_size"]),
                    )
                )
            except Exception:
                logger.warning("Skipping unreadable backup directory %s", path, exc_info=True)
        return backups

    def _create_backup_sync(self) -> BackupResult:
        backup_id = self._timestamp()
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        (self.backup_dir / _TMP_DIR_NAME).mkdir(parents=True, exist_ok=True)
        temp_root = self._temp_root(backup_id)
        backup_root = self._backup_root(backup_id)
        if backup_root.exists() or temp_root.exists():
            raise RuntimeError(f"Backup id collision: {backup_id}")

        temp_root.mkdir(parents=True, exist_ok=False)
        try:
            db_path = temp_root / _DB_FILENAME
            self._snapshot_db(db_path)

            state_path = temp_root / _STATE_FILENAME
            manifest = self._build_state_archive(state_path, backup_id)
            manifest["db_size"] = db_path.stat().st_size
            manifest["state_size"] = state_path.stat().st_size
            manifest_path = temp_root / _MANIFEST_FILENAME
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

            os.replace(temp_root, backup_root)
        except Exception:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise

        result = BackupResult(
            backup_id=backup_id,
            created_at=manifest["created_at"],
            backup_dir=str(backup_root),
            db_path=str(backup_root / _DB_FILENAME),
            state_path=str(backup_root / _STATE_FILENAME),
            manifest_path=str(backup_root / _MANIFEST_FILENAME),
            db_size=manifest["db_size"],
            state_size=manifest["state_size"],
        )
        logger.info(
            "BACKUP CREATED | id=%s | db=%d | state=%d",
            result.backup_id,
            result.db_size,
            result.state_size,
        )
        return result

    def _snapshot_db(self, db_backup_path: Path) -> None:
        db_backup_path.parent.mkdir(parents=True, exist_ok=True)
        db_url = settings.db_url_resolved.replace("postgresql+asyncpg://", "postgresql://", 1)
        subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--file", str(db_backup_path), db_url],
            check=True,
            capture_output=True,
            text=True,
        )

    def _build_state_archive(self, state_path: Path, backup_id: str) -> dict:
        members: list[str] = []
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(state_path, "w:gz") as archive:
            for rel_path in self.managed_directory_targets():
                source = self.data_dir / rel_path
                if source.exists():
                    archive.add(source, arcname=rel_path.as_posix())
                    members.append(rel_path.as_posix())
            for rel_path in self.managed_file_targets():
                source = self.data_dir / rel_path
                if source.is_file():
                    archive.add(source, arcname=rel_path.as_posix())
                    members.append(rel_path.as_posix())
            for rel_parent, pattern in self.managed_glob_targets():
                parent = self.data_dir / rel_parent
                if not parent.exists():
                    continue
                for match in sorted(parent.glob(pattern)):
                    if match.is_file():
                        arcname = match.relative_to(self.data_dir).as_posix()
                        archive.add(match, arcname=arcname)
                        members.append(arcname)
        return {
            "backup_id": backup_id,
            "created_at": datetime.now(UTC).isoformat(),
            "managed_directories": [p.as_posix() for p in self.managed_directory_targets()],
            "managed_files": [p.as_posix() for p in self.managed_file_targets()],
            "managed_globs": [
                {"parent": parent.as_posix(), "pattern": pattern}
                for parent, pattern in self.managed_glob_targets()
            ],
            "archive_members": members,
        }

    def _validate_backup_pair(self, backup_root: Path) -> None:
        missing = [
            name
            for name in (_DB_FILENAME, _STATE_FILENAME, _MANIFEST_FILENAME)
            if not (backup_root / name).exists()
        ]
        if missing:
            raise FileNotFoundError(f"Backup {backup_root.name} is incomplete; missing {missing}")

    def _validate_backup_db(self, db_path: Path) -> None:
        result = subprocess.run(
            ["pg_restore", "--list", str(db_path)], check=False, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Backup database validation failed: {result.stderr[:300]}")

    def _extract_state_archive(self, archive_path: Path, extract_root: Path) -> None:
        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(extract_root, filter="data")

    def _restore_sync(self, backup_root: Path, extracted_root: Path) -> None:
        raise RuntimeError(
            "PostgreSQL restores require the marquee-maintenance command after workers are drained; "
            "they cannot safely run inside the database being restored."
        )

    def _clear_managed_state(self) -> None:
        for rel_path in self.managed_directory_targets():
            target = self.data_dir / rel_path
            if target.exists():
                shutil.rmtree(target)
        for rel_path in self.managed_file_targets():
            target = self.data_dir / rel_path
            if target.exists():
                target.unlink()
        for rel_parent, pattern in self.managed_glob_targets():
            parent = self.data_dir / rel_parent
            if not parent.exists():
                continue
            for match in parent.glob(pattern):
                if match.is_file():
                    match.unlink()

    def _restore_managed_state(self, extracted_root: Path) -> None:
        for rel_path in self.managed_directory_targets():
            source = extracted_root / rel_path
            target = self.data_dir / rel_path
            if source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target)
        for rel_path in self.managed_file_targets():
            source = extracted_root / rel_path
            target = self.data_dir / rel_path
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        for rel_parent, pattern in self.managed_glob_targets():
            parent = extracted_root / rel_parent
            target_parent = self.data_dir / rel_parent
            if not parent.exists():
                continue
            target_parent.mkdir(parents=True, exist_ok=True)
            for match in parent.glob(pattern):
                if match.is_file():
                    shutil.copy2(match, target_parent / match.name)

    def _read_manifest(self, backup_root: Path) -> dict:
        return json.loads((backup_root / _MANIFEST_FILENAME).read_text(encoding="utf-8"))

    def _delete_backup_sync(self, backup_id: str) -> bool:
        backup_root = self._backup_root(backup_id)
        if not backup_root.exists():
            return False
        shutil.rmtree(backup_root)
        logger.info("BACKUP DELETED | id=%s", backup_id)
        return True

    def _rotate_backups_sync(self, retention_days: int) -> int:
        if retention_days <= 0:
            return 0
        backups = self._list_backups_sync()
        grouped: dict[str, list[BackupInfo]] = {}
        for backup in backups:
            day = backup.backup_id.split("-", 1)[0]
            grouped.setdefault(day, []).append(backup)
        keep_days = sorted(grouped, reverse=True)[:retention_days]
        keep_ids = {
            max(grouped[day], key=lambda backup: backup.backup_id).backup_id for day in keep_days
        }
        deleted = 0
        for backup in backups:
            if backup.backup_id in keep_ids:
                continue
            if self._delete_backup_sync(backup.backup_id):
                deleted += 1
        return deleted


backup_service = BackupService()
