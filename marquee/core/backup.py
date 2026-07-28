"""Internal backup service for local rollback snapshots."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
from sqlalchemy import URL
from sqlalchemy.engine import make_url

from marquee.api.results import BackupInfo, BackupResult, RestoreResult
from marquee.config import settings
from marquee.core.filesystem import FilesystemBoundary, FilesystemBoundaryError, RootSpec
from marquee.core.jobs.safety_gates import SafetyGateService, SafetyRequirements
from marquee.db_migration import asyncpg_dsn, connect_admin, verify_runtime_schema

logger = logging.getLogger(__name__)

_TMP_DIR_NAME = ".tmp"
_DB_FILENAME = "marquee.dump"
_STATE_FILENAME = "state.tar.gz"
_MANIFEST_FILENAME = "manifest.json"
_BACKUP_FORMAT_VERSION = 1


class BackupVerificationError(RuntimeError):
    """A backup is incomplete, malformed, or cannot be safely verified."""


class OfflineRestoreError(RuntimeError):
    """An offline restore guard or certification check failed."""


class BackupCancelledError(asyncio.CancelledError):
    """Cancellation interrupted a managed-data archive between bounded reads."""


class _DigestReader:
    def __init__(
        self,
        stream,
        digest: hashlib._Hash,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._stream = stream
        self._digest = digest
        self._cancelled = cancelled

    def read(self, size: int = -1) -> bytes:
        if self._cancelled is not None and self._cancelled():
            raise BackupCancelledError("managed-data backup cancelled")
        chunk = self._stream.read(size)
        self._digest.update(chunk)
        return chunk


class BackupService:
    def __init__(self) -> None:
        self._operation_lock = asyncio.Lock()
        self._safety_gates = SafetyGateService()

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
        async with (
            self._operation_lock,
            await self._safety_gates.acquire(
                SafetyRequirements.exclusive_maintenance(),
                cancelled=lambda: False,
                deadline_seconds=30,
            ),
        ):
            return await self._create_backup_with_lock_held()

    async def create_backup_with_maintenance_held(
        self,
        *,
        process_launcher=None,
        cancelled: Callable[[], bool] | None = None,
    ) -> BackupResult:
        """Create a backup when the canonical attempt already owns the exclusive barrier."""
        async with self._operation_lock:
            if process_launcher is None:
                return await self._create_backup_with_lock_held()
            identity = await self._backup_identity()
            backup_id, temp_root, backup_root = await asyncio.to_thread(
                self._allocate_backup, identity
            )
            try:
                await self._snapshot_db_tracked(temp_root / _DB_FILENAME, process_launcher)
                result = await asyncio.to_thread(
                    self._complete_backup_sync,
                    identity,
                    backup_id,
                    temp_root,
                    backup_root,
                    cancelled,
                )
            except BaseException:
                await asyncio.to_thread(shutil.rmtree, temp_root, ignore_errors=True)
                raise
            await asyncio.to_thread(self._rotate_backups_sync, settings.BACKUP_RETENTION_DAYS)
            return result

    async def _create_backup_with_lock_held(self) -> BackupResult:
        identity = await self._backup_identity()
        result = await asyncio.to_thread(self._create_backup_sync, identity)
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

    async def restore_offline(
        self,
        backup_id: str,
        *,
        target_database: str,
        confirm_database_name: str,
        target_data_dir: Path,
        allow_create_target: bool,
    ) -> dict[str, object]:
        if not allow_create_target or target_database != confirm_database_name:
            raise OfflineRestoreError(
                "restore requires exact database confirmation and acknowledgement"
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", target_database):
            raise OfflineRestoreError("restore target database name is unsafe")
        source = self._database_url()
        if target_database == source.database:
            raise OfflineRestoreError("restore target must not be the running source database")
        approved_root = os.getenv("MARQUEE_RESTORE_ROOT")
        if not approved_root:
            raise OfflineRestoreError("restore requires an explicit approved target root")
        root = Path(approved_root).resolve(strict=True)
        target = target_data_dir.resolve(strict=False)
        if not target.is_relative_to(root) or target == root:
            raise OfflineRestoreError("restore target data directory is outside the approved root")
        if target.exists() and any(target.iterdir()):
            raise OfflineRestoreError("restore target data directory must be absent or empty")
        async with self._operation_lock:
            backup_root = self._backup_root(backup_id)
            await asyncio.to_thread(self._validate_backup_pair, backup_root)
            await asyncio.to_thread(self._validate_backup_db, backup_root / _DB_FILENAME)
            await self._restore_database(backup_root / _DB_FILENAME, target_database)
            try:
                await self._verify_restored_database(target_database)
                await asyncio.to_thread(self._restore_data_target, backup_root, target)
            except BaseException:
                await self._drop_owned_database(target_database)
                if target.exists():
                    shutil.rmtree(target)
                raise
        return {
            "backup_id": backup_id,
            "restored": True,
            "target_database": target_database,
            "target_data_dir": str(target),
            "offline_cutover_required": True,
        }

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
                self._validate_backup_pair(path)
                manifest = self._read_manifest(path)
                backups.append(
                    BackupInfo(
                        backup_id=manifest["backup_id"],
                        created_at=manifest["created_at"],
                        db_size=int(manifest["db_size"]),
                        state_size=int(manifest["state_size"]),
                    )
                )
            except Exception:
                logger.warning("Skipping unreadable backup directory %s", path, exc_info=True)
        return backups

    async def _backup_identity(self) -> dict[str, object]:
        connection = await connect_admin("marquee:managed-backup")
        try:
            server_version = await connection.fetchval("SHOW server_version_num")
            markers = await connection.fetch(
                "SELECT component, expected_version, durability, catalog_fingerprint "
                "FROM schema_contracts WHERE component = ANY($1::text[]) ORDER BY component",
                ["marquee", "pgqueuer"],
            )
            configuration = await connection.fetchrow(
                "SELECT current_version, checksum, schema_version FROM configuration_current "
                "JOIN configuration_revisions ON configuration_revisions.version = "
                "configuration_current.current_version WHERE singleton_id = 1"
            )
        finally:
            await connection.close()
        marker_map = {str(row["component"]): dict(row) for row in markers}
        url = self._database_url()
        source = hashlib.sha256(
            f"{url.host or ''}:{url.port or 5432}/{url.database}".encode()
        ).hexdigest()
        return {
            "application_build": os.getenv("MARQUEE_BUILD", "unknown"),
            "database_identity": source,
            "server_version": str(server_version),
            "marquee": marker_map.get("marquee", {}),
            "pgqueuer": marker_map.get("pgqueuer", {}),
            "configuration": {} if configuration is None else dict(configuration),
        }

    def _allocate_backup(self, identity: dict[str, object] | None = None) -> tuple[str, Path, Path]:
        backup_id = self._timestamp()
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        (self.backup_dir / _TMP_DIR_NAME).mkdir(parents=True, exist_ok=True)
        temp_root = self._temp_root(backup_id)
        backup_root = self._backup_root(backup_id)
        if backup_root.exists() or temp_root.exists():
            raise RuntimeError(f"Backup id collision: {backup_id}")
        temp_root.mkdir(parents=True, exist_ok=False)
        return backup_id, temp_root, backup_root

    def _create_backup_sync(self, identity: dict[str, object] | None = None) -> BackupResult:
        backup_id, temp_root, backup_root = self._allocate_backup(identity)
        try:
            db_path = temp_root / _DB_FILENAME
            self._snapshot_db(db_path)
            return self._complete_backup_sync(identity, backup_id, temp_root, backup_root)
        except Exception:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise

    def _complete_backup_sync(
        self,
        identity: dict[str, object] | None,
        backup_id: str,
        temp_root: Path,
        backup_root: Path,
        cancelled: Callable[[], bool] | None = None,
    ) -> BackupResult:
        try:
            db_path = temp_root / _DB_FILENAME
            state_path = temp_root / _STATE_FILENAME
            manifest = self._build_state_archive(
                state_path,
                backup_id,
                cancelled=cancelled,
            )
            if identity is not None:
                manifest.update(identity)
            manifest["database"] = self._component_metadata(db_path, "postgresql-custom")
            manifest["data"] = {
                **manifest["data"],
                "archive": self._component_metadata(state_path, "tar-gzip"),
            }
            manifest["db_size"] = manifest["database"]["size"]
            manifest["state_size"] = manifest["data"]["archive"]["size"]
            manifest_path = temp_root / _MANIFEST_FILENAME
            with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(manifest, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_directory(temp_root)

            os.replace(temp_root, backup_root)
            self._fsync_directory(self.backup_dir)
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

    async def _snapshot_db_tracked(self, db_backup_path: Path, process_launcher) -> None:
        await asyncio.to_thread(db_backup_path.parent.mkdir, parents=True, exist_ok=True)
        url = self._database_url()
        pgpass = db_backup_path.parent / ".pgpass"
        try:
            await asyncio.to_thread(self._write_pgpass, pgpass, url)
            process = await process_launcher.launch(
                "pg_dump",
                [
                    "--format=custom",
                    "--no-owner",
                    "--file",
                    str(db_backup_path),
                    "--host",
                    url.host or "",
                    "--port",
                    str(url.port or 5432),
                    "--username",
                    url.username or "",
                    url.database or "",
                ],
                environment={"PGPASSFILE": str(pgpass)},
            )
            summary = await process.wait()
            if summary.exit_code != 0 or summary.exit_signal is not None:
                stderr = summary.stderr.captured.decode("utf-8", errors="replace")
                raise RuntimeError(f"pg_dump failed: {self._bounded_stderr(stderr)}")
        finally:
            await asyncio.to_thread(pgpass.unlink, missing_ok=True)

    def _snapshot_db(self, db_backup_path: Path) -> None:
        db_backup_path.parent.mkdir(parents=True, exist_ok=True)
        url = self._database_url()
        pgpass = db_backup_path.parent / ".pgpass"
        try:
            self._write_pgpass(pgpass, url)
            environment = os.environ.copy()
            environment["PGPASSFILE"] = str(pgpass)
            result = subprocess.run(
                [
                    "pg_dump",
                    "--format=custom",
                    "--no-owner",
                    "--file",
                    str(db_backup_path),
                    "--host",
                    url.host or "",
                    "--port",
                    str(url.port or 5432),
                    "--username",
                    url.username or "",
                    url.database or "",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            if result.returncode:
                raise RuntimeError(f"pg_dump failed: {self._bounded_stderr(result.stderr)}")
        finally:
            pgpass.unlink(missing_ok=True)

    async def _restore_database(self, archive: Path, target_database: str) -> None:
        source = self._database_url()
        admin_url = source.set(database="postgres")
        connection = await asyncpg.connect(asyncpg_dsn(admin_url))
        try:
            exists = await connection.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", target_database
            )
            if exists:
                raise OfflineRestoreError("restore target database already exists")
            await connection.execute(f'CREATE DATABASE "{target_database}" TEMPLATE template0')
        finally:
            await connection.close()
        pgpass = archive.parent / ".restore.pgpass"
        try:
            # pg_restore connects to the freshly created target, not the source
            # database, and libpq matches .pgpass entries on the database field.
            self._write_pgpass(pgpass, source.set(database=target_database))
            environment = os.environ.copy()
            environment["PGPASSFILE"] = str(pgpass)
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    "pg_restore",
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--host",
                    source.host or "",
                    "--port",
                    str(source.port or 5432),
                    "--username",
                    source.username or "",
                    "--dbname",
                    target_database,
                    str(archive),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            if result.returncode:
                raise OfflineRestoreError(
                    f"pg_restore failed: {self._bounded_stderr(result.stderr)}"
                )
        except BaseException:
            await self._drop_owned_database(target_database)
            raise
        finally:
            pgpass.unlink(missing_ok=True)

    async def _drop_owned_database(self, database: str) -> None:
        source = self._database_url()
        admin_url = source.set(database="postgres")
        connection = await asyncpg.connect(asyncpg_dsn(admin_url))
        try:
            await connection.execute(f'DROP DATABASE IF EXISTS "{database}"')
        finally:
            await connection.close()

    async def _verify_restored_database(self, database: str) -> None:
        target_url = self._database_url().set(database=database)
        connection = await asyncpg.connect(asyncpg_dsn(target_url))
        try:
            await verify_runtime_schema(connection)
        except Exception as exc:
            raise OfflineRestoreError(
                "restored database failed schema or PgQueuer certification"
            ) from exc
        finally:
            await connection.close()

    def _restore_data_target(self, backup_root: Path, target: Path) -> None:
        target.mkdir(mode=0o700, parents=True, exist_ok=False)
        archive = backup_root / _STATE_FILENAME
        manifest = self._read_manifest(backup_root)
        self._validate_state_archive(archive, manifest)
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                destination = target / member.name
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise OfflineRestoreError("restored archive member cannot be read")
                with stream, destination.open("xb") as output:
                    shutil.copyfileobj(stream, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())

    @staticmethod
    def _bounded_stderr(stderr: str) -> str:
        return stderr.strip().replace("\n", " ")[:300]

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _component_metadata(path: Path, archive_format: str) -> dict[str, object]:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return {
            "name": path.name,
            "format": archive_format,
            "size": path.stat().st_size,
            "sha256": digest.hexdigest(),
        }

    @staticmethod
    def _database_url() -> URL:
        url = make_url(settings.db_url_resolved)
        if url.get_backend_name() != "postgresql" or not url.database:
            raise BackupVerificationError("backup requires a PostgreSQL database URL")
        return url

    @staticmethod
    def _write_pgpass(path: Path, url: URL) -> None:
        if not url.password:
            return
        value = ":".join(
            part.replace("\\", "\\\\").replace(":", "\\:")
            for part in (
                url.host or "",
                str(url.port or 5432),
                url.database or "",
                url.username or "",
                url.password,
            )
        )
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, (value + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def _managed_files(self) -> list[Path]:
        roots: list[Path] = []
        for rel_path in self.managed_directory_targets():
            source = self.data_dir / rel_path
            if source.exists():
                roots.extend(sorted(path for path in source.rglob("*") if path.is_file()))
        for rel_path in self.managed_file_targets():
            source = self.data_dir / rel_path
            if source.exists():
                roots.append(source)
        for rel_parent, pattern in self.managed_glob_targets():
            roots.extend(sorted((self.data_dir / rel_parent).glob(pattern)))
        boundary = FilesystemBoundary(
            {
                "data": RootSpec(
                    name="data",
                    path=self.data_dir,
                    purpose="managed backup source",
                    access="read",
                    allow_symlinks=False,
                )
            }
        )
        verified: list[Path] = []
        for source in roots:
            try:
                classified = boundary.classify(source.absolute(), require_file=True)
            except FilesystemBoundaryError as exc:
                raise BackupVerificationError(
                    "managed backup member is outside the confined root"
                ) from exc
            verified.append(classified.root.resolved() / classified.key.value)
        return sorted(set(verified), key=lambda path: path.relative_to(self.data_dir).as_posix())

    def _build_state_archive(
        self,
        state_path: Path,
        backup_id: str,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict:
        members: list[dict[str, object]] = []
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with (
            state_path.open("wb") as raw,
            gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped,
            tarfile.open(fileobj=zipped, mode="w") as archive,
        ):
            for source in self._managed_files():
                member = source.relative_to(self.data_dir).as_posix()
                before = source.stat(follow_symlinks=False)
                if not source.is_file() or source.is_symlink():
                    raise BackupVerificationError("managed backup member is not a regular file")
                digest = hashlib.sha256()
                info = tarfile.TarInfo(member)
                info.size = before.st_size
                info.mode = 0o600
                info.mtime = 0
                with source.open("rb") as stream:
                    archive.addfile(info, _DigestReader(stream, digest, cancelled))
                after = source.stat(follow_symlinks=False)
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise BackupVerificationError("managed backup member changed while captured")
                members.append(
                    {"key": member, "size": before.st_size, "sha256": digest.hexdigest()}
                )
        return {
            "backup_format_version": _BACKUP_FORMAT_VERSION,
            "backup_id": backup_id,
            "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "completed_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "data": {
                "included_root_classes": ["managed-data"],
                "exclusions": ["backups", "staging", "temporary", "sockets"],
                "members": members,
                "totals": {"members": len(members), "bytes": sum(int(m["size"]) for m in members)},
            },
        }

    def _validate_backup_pair(self, backup_root: Path) -> None:
        missing = [
            name
            for name in (_DB_FILENAME, _STATE_FILENAME, _MANIFEST_FILENAME)
            if not (backup_root / name).exists()
        ]
        if missing:
            raise BackupVerificationError(f"Backup {backup_root.name} is incomplete")
        manifest = self._read_manifest(backup_root)
        if manifest.get("backup_format_version") != _BACKUP_FORMAT_VERSION:
            raise BackupVerificationError("backup format is unsupported")
        if manifest.get("backup_id") != backup_root.name:
            raise BackupVerificationError("backup manifest identity does not match directory")
        for filename, metadata in (
            (_DB_FILENAME, manifest.get("database")),
            (_STATE_FILENAME, manifest.get("data", {}).get("archive")),
        ):
            if not isinstance(metadata, dict) or metadata.get("name") != filename:
                raise BackupVerificationError("backup component metadata is invalid")
            actual = self._component_metadata(
                backup_root / filename, str(metadata.get("format", ""))
            )
            if actual["size"] != metadata.get("size") or actual["sha256"] != metadata.get("sha256"):
                raise BackupVerificationError("backup component checksum mismatch")
        self._validate_state_archive(backup_root / _STATE_FILENAME, manifest)

    @staticmethod
    def _validate_state_archive(archive_path: Path, manifest: dict) -> None:
        expected = {
            item["key"]: item
            for item in manifest.get("data", {}).get("members", [])
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        }
        observed: set[str] = set()
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile() or member.issym() or member.islnk() or member.isdev():
                    raise BackupVerificationError("backup state archive contains an unsafe member")
                item = expected.get(member.name)
                if item is None or member.name in observed or member.size != item.get("size"):
                    raise BackupVerificationError(
                        "backup state archive does not match its manifest"
                    )
                digest = hashlib.sha256()
                stream = archive.extractfile(member)
                if stream is None:
                    raise BackupVerificationError("backup state archive member cannot be read")
                with stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != item.get("sha256"):
                    raise BackupVerificationError("backup state archive member checksum mismatch")
                observed.add(member.name)
        if observed != set(expected):
            raise BackupVerificationError("backup state archive is missing manifest members")

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
