"""Offline/operator maintenance entrypoint for managed backup operations."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
from pathlib import Path

from marquee.config import settings
from marquee.core.backup import backup_service
from marquee.core.configuration import import_legacy_revision_values
from marquee.core.jobs.artifact_service import expire_artifacts, expire_logs
from marquee.core.managed_secrets import import_legacy_managed_secrets, rotate_managed_secrets
from marquee.database import close_db, init_db, session_factory


async def _backup() -> dict[str, object]:
    result = await backup_service.create_backup()
    return {
        "backup_id": result.backup_id,
        "created_at": result.created_at,
        "db_size": result.db_size,
        "state_size": result.state_size,
    }


async def _verify(backup_id: str) -> dict[str, object]:
    result = await backup_service.restore_backup(backup_id)
    return {
        "backup_id": result.backup_id,
        "restorable": True,
        "offline_restore_required": True,
        "restored": result.restored,
    }


async def _restore(args: argparse.Namespace) -> dict[str, object]:
    return await backup_service.restore_offline(
        args.backup_id,
        target_database=args.target_database,
        confirm_database_name=args.confirm_database_name,
        target_data_dir=args.target_data_dir,
        allow_create_target=args.allow_data_loss_or_create_target,
    )


async def _retention(limit: int) -> dict[str, object]:
    return {
        "artifacts": await expire_artifacts(data_dir=settings.DATA_DIR, limit=limit),
        "logs": await expire_logs(data_dir=settings.DATA_DIR, limit=limit),
    }


async def _migrate_settings() -> dict[str, object]:
    await init_db()
    try:
        async with session_factory()() as session:
            state, public_count = await import_legacy_revision_values(session)
            secret_count = await import_legacy_managed_secrets(session)
            await session.commit()
            return {
                "configuration_version": state.version,
                "public_settings_imported": public_count,
                "managed_credentials_imported": secret_count,
            }
    finally:
        await close_db()


async def _rotate_settings_keyring() -> dict[str, object]:
    await init_db()
    try:
        async with session_factory()() as session:
            count = await rotate_managed_secrets(
                session,
                actor={"kind": "operator", "id": "settings_keyring_rotation"},
            )
            await session.commit()
            return {"managed_credentials_rotated": count, "verified": True}
    finally:
        await close_db()


def _generate_settings_keyring(output: Path) -> dict[str, object]:
    """Create an exclusive 0600 keyring without printing its key material."""
    output = output.expanduser().resolve()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "active_key_id": "v1",
        "keys": {"v1": base64.b64encode(secrets.token_bytes(32)).decode("ascii")},
    }
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return {"created": True, "path": str(output), "permissions": "0600"}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m marquee.maintenance")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backup")
    verify = commands.add_parser("verify-backup")
    verify.add_argument("--backup-id", required=True)
    restore = commands.add_parser("restore-backup")
    restore.add_argument("--backup-id", required=True)
    restore.add_argument("--target-database", required=True)
    restore.add_argument("--confirm-database-name", required=True)
    restore.add_argument("--target-data-dir", required=True, type=Path)
    restore.add_argument("--allow-data-loss-or-create-target", action="store_true")
    retention = commands.add_parser("expire-job-evidence")
    retention.add_argument("--limit", type=int, choices=range(1, 101), default=50)
    commands.add_parser("migrate-settings")
    commands.add_parser("rotate-settings-keyring")
    generate_keyring = commands.add_parser("generate-settings-keyring")
    generate_keyring.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "backup":
        payload = asyncio.run(_backup())
    elif args.command == "verify-backup":
        payload = asyncio.run(_verify(args.backup_id))
    elif args.command == "restore-backup":
        payload = asyncio.run(_restore(args))
    elif args.command == "migrate-settings":
        payload = asyncio.run(_migrate_settings())
    elif args.command == "rotate-settings-keyring":
        payload = asyncio.run(_rotate_settings_keyring())
    elif args.command == "generate-settings-keyring":
        payload = _generate_settings_keyring(args.output)
    else:
        payload = asyncio.run(_retention(args.limit))
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
