# Marquee — Internal Backup Strategy

**Status:** Implemented on 2026-06-17.

## Purpose

Marquee now ships with an internal rollback backup system for self-hosted use.
Its job is local protection against bad migrations, corrupt state, or nasty
application bugs. Copying those backups to an external disk, NAS, or cloud
target is still the operator's responsibility.

This matches the usual self-hosted pattern:

- the app creates and restores its own local snapshots;
- the operator decides whether to replicate those snapshots elsewhere.

## Runtime State Policy

Mutable Marquee app state should live under `data/`.

Current managed runtime paths:

- `data/marquee.db`
- `data/feedback/labels.jsonl`
- `data/training/positive/`
- `data/training/negative/`
- `data/ml/taste_profile.{model}.npz`
- `data/ml/learned_head.{model}.npz`
- `data/ml/zeroshot_axes.{model}.npz`
- `data/cache/posters/`
- `data/cache/embeddings/`
- `data/cache/taste_map*.npz`
- `data/cache/taste_map_history/`
- `data/runs/archive/`
- `data/pipeline_overrides.json`

Not part of this backup scope:

- `data/backups/`
- `data/staging/`
- `.env`
- `experiments/`
- `marquee/ml/models/`

On startup, Marquee migrates legacy feedback/training data from
`experiments/...` into `data/...` when the destination is still empty. If both
legacy and current paths exist, startup fails with a clear manual-resolution
error instead of guessing.

## Backup Artifact Layout

Each backup is stored as its own directory:

```text
data/backups/
  20260617-143000/
    marquee.db
    state.tar.gz
    manifest.json
```

This keeps create/list/delete/rotation operations simple and makes the final
publish step an atomic directory rename.

## What Gets Backed Up

Marquee uses an allowlist, not a broad recursive copy of `data/`.

Included:

- database snapshot from `data/marquee.db`
- `data/feedback/`
- `data/training/`
- `data/ml/`
- `data/cache/posters/`
- `data/cache/embeddings/`
- `data/cache/taste_map*.npz`
- `data/cache/taste_map_history/`
- `data/runs/archive/`
- `data/pipeline_overrides.json`

Excluded:

- live DB sidecars (`*.db-wal`, `*.db-shm`)
- `data/backups/`
- `data/staging/`
- transient scratch output outside the managed allowlist

## Create Flow

`marquee/core/backup.py` implements `BackupService.create_backup()`.

High-level flow:

1. Create `data/backups/.tmp/{backup_id}/`
2. Snapshot the live DB with `VACUUM INTO`
3. Build `state.tar.gz` from the managed allowlist
4. Write `manifest.json`
5. Atomically rename the temp directory into `data/backups/{backup_id}/`
6. Rotate old backups

`VACUUM INTO` was chosen over shelling out to `sqlite3 .backup` because it is
in-process, consistent, and produces a compact standalone `.db` file.

## Restore Semantics

Restore is exact for managed backup targets.

Behavior:

1. Validate the backup directory and DB snapshot
2. Close the live SQLAlchemy engine
3. Replace `data/marquee.db` with the backup snapshot
4. Clear the managed backup targets from `data/`
5. Restore the managed state from `state.tar.gz`
6. Leave excluded areas alone

That means restore will replace backed-up managed state, not merge into it.
Files in excluded areas such as `data/staging/`, `experiments/`, `.env`, and
`marquee/ml/models/` are untouched.

The restore endpoint returns `restart_required: true`. Operators should restart
the app after restore so every process-local handle sees the restored DB/state.

## Rotation

`BACKUP_RETENTION_DAYS` controls daily retention.

Rotation keeps:

- only the newest backup for a given UTC day;
- only the newest `N` days, where `N = BACKUP_RETENTION_DAYS`.

Everything else is deleted as whole backup directories.

## API

Implemented system endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /api/system/backup` | Create a backup immediately |
| `GET /api/system/backups` | List available backup directories |
| `POST /api/system/restore?backup_id={id}` | Restore one backup and signal restart required |
| `DELETE /api/system/backups/{backup_id}` | Delete one backup directory |

## Config

Implemented settings in `marquee/config.py`:

| Setting | Default | Purpose |
|---|---:|---|
| `BACKUP_INTERVAL_HOURS` | `24` | Automatic backup cadence |
| `BACKUP_RETENTION_DAYS` | `7` | Daily backup retention window |
| `BACKUP_INITIAL_DELAY_SECONDS` | `300` | Delay before first scheduled backup |
| `BACKUP_DIR` | `data/backups` | Backup storage root |

Scheduled backups run only when:

- `DEBUG` is false, and
- `BACKUP_INTERVAL_HOURS > 0`

Setting `BACKUP_INTERVAL_HOURS=0` leaves manual backup/restore available while
disabling the scheduler.

## Why This Direction

The app now follows the intended self-hosted boundary:

- Marquee owns local rollback snapshots of its own important state.
- Operators own external replication if they want stronger disaster recovery.

That is the same split most self-hosted apps use in practice: local app-managed
backup first, off-machine copy second.
