"""Canonical exclusive-maintenance handlers with sealed, bounded deletion scopes."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.orm import aliased

from marquee.config import settings
from marquee.core.backup import backup_service
from marquee.core.filesystem import ClassifiedPath, FilesystemBoundary, RootSpec
from marquee.core.jobs.artifact_service import (
    expire_artifacts,
    expire_logs,
    register_physical_artifact,
)
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.mutation_documents import (
    BackupCreateRequestV1,
    MetricsPurgeRequestV1,
    PipelineCacheClearRequestV1,
    PosterMaintenanceRequestV1,
    RetentionPurgeRequestV1,
)
from marquee.core.pipeline_config import pipeline_settings
from marquee.models import (
    Job,
    JobArtifact,
    JobLog,
    Movie,
    PipelineRun,
    Season,
    Series,
    SystemMetricsSample,
)

MAX_MAINTENANCE_ITEMS = 10_000


class MaintenanceOperationError(RuntimeError):
    """A bounded maintenance plan could not be safely applied."""


@dataclass(frozen=True, slots=True)
class PlannedFile:
    category: str
    boundary: FilesystemBoundary
    classified: ClassifiedPath
    size: int

    @property
    def path(self) -> Path:
        return self.classified.root.resolved() / self.classified.key.value

    @property
    def relative(self) -> str:
        return self.classified.key.value


def _checksum(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_confirmed_plan(
    *, dry_run: bool, confirmed: str | None, checksum: str, label: str
) -> None:
    if confirmed is not None and confirmed != checksum:
        raise MaintenanceOperationError(f"confirmed {label} plan does not match current scope")
    if not dry_run and confirmed is None:
        raise MaintenanceOperationError(f"{label} mutation requires a confirmed dry-run plan")


def _result(
    *,
    operation: str,
    plan_checksum: str,
    planned: int,
    processed: int,
    deleted: int,
    counts: dict[str, int],
    dry_run: bool = False,
    cancelled: bool = False,
    backup: dict[str, object] | None = None,
) -> dict[str, object]:
    if cancelled and processed:
        outcome = "partially_succeeded"
    elif deleted or backup is not None:
        outcome = "succeeded"
    else:
        outcome = "no_change"
    return {
        "outcome": outcome,
        "operation": operation,
        "message": (
            "Maintenance stopped at a safe batch boundary."
            if cancelled
            else "Maintenance plan completed."
            if not dry_run
            else "Dry-run plan sealed without mutation."
        ),
        "dry_run": dry_run,
        "plan_checksum": plan_checksum,
        "planned_count": planned,
        "processed_count": processed,
        "deleted_count": deleted,
        "counts": counts,
        "cancelled": cancelled,
        "backup": backup,
    }


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_evidence(result) -> dict[str, object]:
    manifest_path = Path(result.manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "backup_id": result.backup_id,
        "created_at": result.created_at,
        "database_checksum": manifest["database"]["sha256"],
        "state_checksum": manifest["data"]["archive"]["sha256"],
        "manifest_checksum": _file_checksum(manifest_path),
        "database_size": result.db_size,
        "state_size": result.state_size,
    }


async def execute_backup_create(context: ExecutionContext) -> dict[str, object]:
    BackupCreateRequestV1.model_validate(context.request)
    if context.cancellation.cancel_called:
        raise MaintenanceOperationError("backup cancelled before consistency point")
    await context.progress.stage(
        "execute",
        label="Creating database and managed-data backup",
        wait_kind="external_process",
        wait_label_key="backup.pg_dump",
    )
    result = await backup_service.create_backup_with_maintenance_held(
        process_launcher=context.process_launcher,
        cancelled=lambda: bool(context.cancellation.cancel_called),
    )
    evidence = await asyncio.to_thread(_backup_evidence, result)
    boundary = FilesystemBoundary(
        {
            "backup": RootSpec(
                name="backup",
                path=settings.backup_dir_path,
                purpose="certified backup artifacts",
                access="read_write",
            )
        }
    )
    manifest = boundary.classify(
        Path(result.manifest_path), roots=("backup",), require_file=True
    )
    await register_physical_artifact(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        source=manifest,
        kind="backup_manifest",
        name="Backup manifest",
        content_type="application/json",
        retention_class="extended",
        metadata={"backup_id": result.backup_id},
    )
    plan_checksum = _checksum({"backup_id": evidence["backup_id"], "manifest": evidence})
    await context.progress.stage(
        "finalize",
        label="Registering backup evidence",
        completed=1,
        total=1,
        unit="backups",
    )
    return _result(
        operation="backup_create",
        plan_checksum=plan_checksum,
        planned=1,
        processed=1,
        deleted=0,
        counts={"backups_created": 1},
        backup=evidence,
    )


def _walk_files(category: str, root: Path, *, limit: int) -> list[PlannedFile]:
    root = root.resolve(strict=False)
    if not root.is_dir():
        return []
    boundary = FilesystemBoundary(
        {
            category: RootSpec(
                name=category,
                path=root,
                purpose=f"{category} maintenance",
                access="read_write",
            )
        }
    )
    pending = [root]
    files: list[PlannedFile] = []
    while pending:
        directory = pending.pop()
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            if child.is_symlink():
                continue
            if child.is_dir():
                pending.append(child)
                continue
            resolved = child.resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise MaintenanceOperationError("maintenance candidate escaped its root") from exc
            classified = boundary.classify(
                resolved, roots=(category,), require_file=True, write=True
            )
            files.append(PlannedFile(category, boundary, classified, resolved.stat().st_size))
            if len(files) > limit:
                raise MaintenanceOperationError(f"maintenance scope exceeds {limit} items")
    return sorted(files, key=lambda item: (item.category, item.relative))


def _is_protected(path: Path, protected: tuple[Path, ...]) -> bool:
    resolved = path.resolve(strict=False)
    return any(resolved == item or item in resolved.parents for item in protected)


async def _pipeline_references(context: ExecutionContext) -> tuple[tuple[Path, ...], bool]:
    async with context.session_factory() as session:
        rows = (
            await session.execute(select(PipelineRun.archive_path, PipelineRun.output_dir))
        ).all()
        active = bool(
            await session.scalar(
                select(
                    exists().where(
                        Job.phase != "terminal",
                        Job.id != context.delivery.canonical_job_id,
                    )
                )
            )
        )
    protected = tuple(
        Path(value).resolve(strict=False)
        for row in rows
        for value in row
        if value
    )
    return protected, active


def _planned_counts(plan: tuple[PlannedFile, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in plan:
        counts[item.category] = counts.get(item.category, 0) + 1
    return counts


async def _emit_progress(
    context: ExecutionContext,
    *,
    operation: str,
    completed: int,
    total: int,
) -> None:
    """Persist subject-aware determinate progress at maintenance batch boundaries."""
    await context.progress.stage(
        "execute",
        label=f"{operation.replace('_', ' ').title()} items",
        completed=completed,
        total=total,
        unit="items",
    )

async def _delete_file_plan(
    context: ExecutionContext,
    plan: tuple[PlannedFile, ...],
    *,
    operation: str,
    batch_size: int,
) -> tuple[int, int, dict[str, int], bool]:
    processed = deleted = 0
    counts: dict[str, int] = {}
    await _emit_progress(context, operation=operation, completed=0, total=len(plan))
    for offset in range(0, len(plan), batch_size):
        if context.cancellation.cancel_called:
            return processed, deleted, counts, True
        async with context.session_factory() as session:
            if not await context.writer.owns_current_attempt(session):
                raise MaintenanceOperationError("maintenance attempt ownership is stale")
        for item in plan[offset : offset + batch_size]:
            if await asyncio.to_thread(
                item.boundary.delete_file, item.classified, missing_ok=True
            ):
                deleted += 1
                counts[item.category] = counts.get(item.category, 0) + 1
            processed += 1
        await _emit_progress(
            context,
            operation=operation,
            completed=processed,
            total=len(plan),
        )
    return processed, deleted, counts, False


async def execute_pipeline_cache_clear(context: ExecutionContext) -> dict[str, object]:
    request = PipelineCacheClearRequestV1.model_validate(context.request)
    roots = {
        "runs_work": settings.runs_work_path,
        "staging": settings.poster_staging_path,
    }
    if request.include_embeddings:
        roots["embeddings"] = Path(pipeline_settings.EMBEDDING_CACHE_DIR)
    if request.include_archives:
        roots["archives"] = settings.runs_archive_path
    protected, active = await _pipeline_references(context)
    plan: list[PlannedFile] = []
    for category, root in roots.items():
        candidates = _walk_files(category, root, limit=request.max_items - len(plan))
        if active and category in {"runs_work", "staging", "archives"}:
            continue
        plan.extend(item for item in candidates if not _is_protected(item.path, protected))
    sealed = tuple(plan)
    checksum = _checksum([(item.category, item.relative, item.size) for item in sealed])
    _validate_confirmed_plan(
        dry_run=request.dry_run,
        confirmed=request.confirmed_plan_checksum,
        checksum=checksum,
        label="cache",
    )
    if request.dry_run:
        return _result(
            operation="pipeline_cache_clear",
            plan_checksum=checksum,
            planned=len(sealed),
            processed=0,
            deleted=0,
            counts=_planned_counts(sealed),
            dry_run=True,
        )
    processed, deleted_count, counts, cancelled = await _delete_file_plan(
        context,
        sealed,
        operation="pipeline_cache_clear",
        batch_size=request.batch_size,
    )
    return _result(
        operation="pipeline_cache_clear",
        plan_checksum=checksum,
        planned=len(sealed),
        processed=processed,
        deleted=deleted_count,
        counts=counts,
        cancelled=cancelled,
    )


async def _poster_cache_references(context: ExecutionContext) -> tuple[set[int], set[int], set[tuple[int, int]]]:
    async with context.session_factory() as session:
        movie_tmdb = set((await session.scalars(select(Movie.tmdb_id).where(Movie.tmdb_id.is_not(None)))).all())
        series_tmdb = set((await session.scalars(select(Series.tmdb_id).where(Series.tmdb_id.is_not(None)))).all())
        seasons = set(
            (
                await session.execute(
                    select(Series.tmdb_id, Season.season_number)
                    .join(Season, Season.series_id == Series.id)
                    .where(Series.tmdb_id.is_not(None))
                )
            ).all()
        )
    return movie_tmdb, series_tmdb, seasons


def _poster_cache_referenced(
    item: PlannedFile,
    movie_tmdb: set[int],
    series_tmdb: set[int],
    seasons: set[tuple[int, int]],
) -> bool:
    stem = Path(item.relative).stem.removesuffix(".meta")
    parts = Path(item.relative).parts
    if "movies" in parts and stem.isdigit():
        return int(stem) in movie_tmdb
    if "tv" in parts and "-s" in stem:
        value = stem.split("-s", 1)
        return len(value) == 2 and all(part.isdigit() for part in value) and (
            int(value[0]),
            int(value[1]),
        ) in seasons
    if "tv" in parts and stem.isdigit():
        return int(stem) in series_tmdb
    return True


async def execute_poster_maintenance(context: ExecutionContext) -> dict[str, object]:
    request = PosterMaintenanceRequestV1.model_validate(context.request)
    movie_tmdb, series_tmdb, seasons = await _poster_cache_references(context)
    candidates = _walk_files("poster_cache", settings.poster_cache_path, limit=request.max_items)
    sealed = tuple(
        item
        for item in candidates
        if not _poster_cache_referenced(item, movie_tmdb, series_tmdb, seasons)
    )
    checksum = _checksum([(item.category, item.relative, item.size) for item in sealed])
    _validate_confirmed_plan(
        dry_run=request.dry_run,
        confirmed=request.confirmed_plan_checksum,
        checksum=checksum,
        label="poster",
    )
    if request.dry_run:
        return _result(
            operation="poster_maintenance",
            plan_checksum=checksum,
            planned=len(sealed),
            processed=0,
            deleted=0,
            counts=_planned_counts(sealed),
            dry_run=True,
        )
    processed, deleted_count, counts, cancelled = await _delete_file_plan(
        context,
        sealed,
        operation="poster_maintenance",
        batch_size=request.batch_size,
    )
    return _result(
        operation="poster_maintenance",
        plan_checksum=checksum,
        planned=len(sealed),
        processed=processed,
        deleted=deleted_count,
        counts=counts,
        cancelled=cancelled,
    )


async def _delete_rows(
    context: ExecutionContext,
    *,
    operation: str,
    model,
    ids: tuple[int | str, ...],
    batch_size: int,
) -> tuple[int, bool]:
    deleted_count = 0
    await _emit_progress(context, operation=operation, completed=0, total=len(ids))
    for offset in range(0, len(ids), batch_size):
        if context.cancellation.cancel_called:
            return deleted_count, True
        batch = ids[offset : offset + batch_size]
        async with context.session_factory() as session, session.begin():
            if not await context.writer.owns_current_attempt(session):
                raise MaintenanceOperationError("maintenance attempt ownership is stale")
            result = await session.execute(delete(model).where(model.id.in_(batch)))
            deleted_count += result.rowcount or 0
        await _emit_progress(
            context,
            operation=operation,
            completed=min(offset + len(batch), len(ids)),
            total=len(ids),
        )
    return deleted_count, False


async def _expire_evidence_batches() -> tuple[dict[str, int], dict[str, int]]:
    artifact_totals = dict.fromkeys(("claimed", "expired", "deleted", "missing", "failed"), 0)
    log_totals = artifact_totals.copy()
    for _ in range(max(1, MAX_MAINTENANCE_ITEMS // 100)):
        artifact_cleanup = await expire_artifacts(data_dir=settings.DATA_DIR, limit=100)
        log_cleanup = await expire_logs(data_dir=settings.DATA_DIR, limit=100)
        for key in artifact_totals:
            artifact_totals[key] += artifact_cleanup[key]
            log_totals[key] += log_cleanup[key]
        if artifact_cleanup["claimed"] < 100 and log_cleanup["claimed"] < 100:
            return artifact_totals, log_totals
    raise MaintenanceOperationError("evidence retention scope exceeds configured bound")


async def execute_job_retention_purge(context: ExecutionContext) -> dict[str, object]:
    request = RetentionPurgeRequestV1.model_validate(context.request)
    if request.evidence_only:
        artifacts, logs = await _expire_evidence_batches()
        processed = artifacts["expired"] + logs["expired"]
        return _result(
            operation="job_retention_purge",
            plan_checksum=_checksum(()),
            planned=artifacts["claimed"] + logs["claimed"],
            processed=processed,
            deleted=artifacts["deleted"] + logs["deleted"],
            counts={
                "artifacts": artifacts["expired"],
                "logs": logs["expired"],
                "missing": artifacts["missing"] + logs["missing"],
                "failed": artifacts["failed"] + logs["failed"],
            },
        )
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=request.retention_days)
    live_artifact = exists().where(
        JobArtifact.job_id == Job.id,
        JobArtifact.status == "available",
        or_(JobArtifact.expires_at.is_(None), JobArtifact.expires_at > now),
    )
    live_log = exists().where(
        JobLog.job_id == Job.id,
        or_(JobLog.expires_at.is_(None), JobLog.expires_at > now),
    )
    active_job = aliased(Job)
    active_dependency = exists().where(
        active_job.phase != "terminal",
        or_(
            active_job.parent_id == Job.id,
            active_job.retry_of_job_id == Job.id,
            active_job.root_id == Job.id,
        ),
    )
    async with context.session_factory() as session:
        ids = tuple(
            (
                await session.scalars(
                    select(Job.id)
                    .where(
                        Job.phase == "terminal",
                        Job.terminal_at < cutoff,
                        Job.id != context.delivery.canonical_job_id,
                        ~live_artifact,
                        ~live_log,
                        ~active_dependency,
                    )
                    .order_by(Job.terminal_at, Job.id)
                    .limit(request.max_records + 1)
                )
            ).all()
        )
    if len(ids) > request.max_records:
        raise MaintenanceOperationError("job retention scope exceeds configured bound")
    checksum = _checksum(ids)
    _validate_confirmed_plan(
        dry_run=request.dry_run,
        confirmed=request.confirmed_plan_checksum,
        checksum=checksum,
        label="job retention",
    )
    if request.dry_run:
        return _result(
            operation="job_retention_purge",
            plan_checksum=checksum,
            planned=len(ids),
            processed=0,
            deleted=0,
            counts={"jobs": len(ids)},
            dry_run=True,
        )
    await _expire_evidence_batches()

    if ids:
        async with context.session_factory() as session:
            artifact_blockers = await session.scalar(
                select(func.count())
                .select_from(JobArtifact)
                .where(
                    JobArtifact.job_id.in_(ids),
                    JobArtifact.storage_key.is_not(None),
                    JobArtifact.status != "expired",
                )
            )
            log_blockers = await session.scalar(
                select(func.count())
                .select_from(JobLog)
                .where(JobLog.job_id.in_(ids), JobLog.seal_status != "expired")
            )
        if artifact_blockers or log_blockers:
            raise MaintenanceOperationError(
                "physical evidence cleanup is incomplete; job rows were retained"
            )
    deleted_count, cancelled = await _delete_rows(
        context,
        operation="job_retention_purge",
        model=Job,
        ids=ids,
        batch_size=request.batch_size,
    )
    return _result(
        operation="job_retention_purge",
        plan_checksum=checksum,
        planned=len(ids),
        processed=deleted_count,
        deleted=deleted_count,
        counts={"jobs": deleted_count},
        cancelled=cancelled,
    )


async def execute_system_metrics_purge(context: ExecutionContext) -> dict[str, object]:
    request = MetricsPurgeRequestV1.model_validate(context.request)
    cutoff = datetime.now(UTC) - timedelta(days=request.retention_days)
    async with context.session_factory() as session:
        ids = tuple(
            (
                await session.scalars(
                    select(SystemMetricsSample.id)
                    .where(SystemMetricsSample.created_at < cutoff)
                    .order_by(SystemMetricsSample.created_at, SystemMetricsSample.id)
                    .limit(request.max_records + 1)
                )
            ).all()
        )
    if len(ids) > request.max_records:
        raise MaintenanceOperationError("metrics retention scope exceeds configured bound")
    checksum = _checksum(ids)
    _validate_confirmed_plan(
        dry_run=request.dry_run,
        confirmed=request.confirmed_plan_checksum,
        checksum=checksum,
        label="metrics retention",
    )
    if request.dry_run:
        return _result(
            operation="system_metrics_purge",
            plan_checksum=checksum,
            planned=len(ids),
            processed=0,
            deleted=0,
            counts={"samples": len(ids)},
            dry_run=True,
        )
    deleted_count, cancelled = await _delete_rows(
        context,
        operation="system_metrics_purge",
        model=SystemMetricsSample,
        ids=ids,
        batch_size=request.batch_size,
    )
    return _result(
        operation="system_metrics_purge",
        plan_checksum=checksum,
        planned=len(ids),
        processed=deleted_count,
        deleted=deleted_count,
        counts={"samples": deleted_count},
        cancelled=cancelled,
    )


register_execution_handler("backup_create", execute_backup_create)
register_execution_handler("poster_maintenance", execute_poster_maintenance)
register_execution_handler("pipeline_cache_clear", execute_pipeline_cache_clear)
register_execution_handler("job_retention_purge", execute_job_retention_purge)
register_execution_handler("system_metrics_purge", execute_system_metrics_purge)
