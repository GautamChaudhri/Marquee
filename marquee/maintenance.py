"""Offline/operator maintenance entrypoint for managed backup operations."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from marquee.config import settings
from marquee.core.backup import backup_service
from marquee.core.jobs.artifact_service import expire_artifacts, expire_logs


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
    args = parser.parse_args()
    if args.command == "backup":
        payload = asyncio.run(_backup())
    elif args.command == "verify-backup":
        payload = asyncio.run(_verify(args.backup_id))
    elif args.command == "restore-backup":
        payload = asyncio.run(_restore(args))
    else:
        payload = asyncio.run(_retention(args.limit))
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
