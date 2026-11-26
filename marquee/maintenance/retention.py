"""Retention: delete, by written rules, what has gone stale.

One pass, :func:`run`, plans what each rule would delete and, unless it is a dry run, deletes
exactly that, checking each item again just before it goes. The rules (``RETENTION_DAYS``
is 90 by default):

- **jobs**: a job finished (succeeded, failed, or cancelled) more than ``RETENTION_DAYS``
  ago, with its events. Never a queued or running job, never a batch whose child is kept
  (a batch goes whole: its children go with it), never a job a blind evaluation names or one
  of that batch's children (an evaluation's state and the Taste page's headline are read
  from them), and never the last job of a schedule.
- **runs**: a run (row, candidates, and folder, through
  :func:`marquee.artwork.runs.delete_runs`) older than ``RETENTION_DAYS`` that is not its
  movie's newest run or newest completed run, and that no deployment or preference event
  names. A masked run is kept while its evaluation is the newest one, or the newest that
  succeeded.
- **run_folders**: a folder in ``runs/`` that no run row names, untouched for 7 days.
- **thumbnails**: a file in ``cache/thumbs/`` untouched for 30 days.
- **embeddings**: a file in ``cache/embeddings/`` untouched for ``RETENTION_DAYS``. Both
  caches are rebuilt on demand.
- **leftovers**: anything in ``cache/deploys/``, ``cache/blind-profiles/``, and
  ``cache/taste-build/``, and the temporary files of a dump or restore, a profile copy, or a
  residual, untouched for 24 hours and owned by no queued or running job. A residual's
  ``.pending-*`` record goes only once a training row already records its artifact (until
  then the next rebuild reads it, and it is kept).
- **profiles** and **residuals**: copies of old taste profiles in ``ml/profiles/`` and old
  residual artifacts in ``ml/residuals/``, beyond the active one and the newest 20; their
  rows stay. The seed profile's copy is kept too (a rebuild keeps it), and so is an artifact
  a pending record names.

Never deleted: poster backups (``backups/posters/``), deployments, preference events,
exemplars and their images, evaluations and their results, ``ml/seed/``, the models, the
kernel cache, database dumps (the backup job and a restore keep their own counts), anything
outside the data folder, and anything in it these rules do not name.

Every file deletion goes through :mod:`marquee.fs.boundary`, confined to the data folder,
and retention refuses to run when the data folder is inside a movie root or a movie root is
inside the data folder. "Untouched" is a file's modification time; for a folder, the newest
modification time of anything in it. Paths are logged at DEBUG only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import stat
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Select, and_, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from marquee.artwork import runs
from marquee.config import settings
from marquee.db.models import Deployment, Job, JobEvent, JobSchedule, PosterRun, RunCandidate
from marquee.db.models.evaluation import Evaluation, EvaluationResult
from marquee.db.models.job import ACTIVE_STATES
from marquee.db.models.residual import ResidualTraining
from marquee.db.models.run import RUN_ACTIVE, RUN_COMPLETED, in_product_loop
from marquee.db.models.taste import PROFILE_SOURCE_SEED, PreferenceEvent, TasteProfile
from marquee.fs.boundary import FilesystemBoundary, FilesystemBoundaryError, boundary_for_roots

logger = logging.getLogger(__name__)

RULES = (
    "jobs",
    "runs",
    "run_folders",
    "thumbnails",
    "embeddings",
    "leftovers",
    "profiles",
    "residuals",
)
FINISHED = ("succeeded", "failed", "cancelled")
RUN_FOLDER_DAYS = 7
THUMBNAIL_DAYS = 30
LEFTOVER_HOURS = 24
KEEP_COPIES = 20
_ARTIFACT = re.compile(r"[0-9a-f]{64}\.npz")
_PENDING = re.compile(r"\.pending-([0-9a-f]{64})\.json")
_JOB_FILE = re.compile(r"\.?(\d+)(?:\.jpg|\.json)(?:\.part)?")
_ROOT = "data"


class RetentionRefusedError(Exception):
    """Retention will not run; nothing was deleted. The message says why."""


@dataclass(frozen=True)
class Item:
    rule: str
    name: str
    """``job <id>``, ``run <id>``, or the path relative to the data folder."""
    size_bytes: int = 0
    rows: int = 0
    """Database rows that go with it: a job and its children and events, a run and its
    candidates."""


@dataclass
class Report:
    now: datetime
    dry_run: bool
    items: list[Item] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    """What a rule would have named but keeps, and why (said, not deleted)."""

    def totals(self) -> dict[str, dict[str, int]]:
        found = {rule: {"count": 0, "bytes": 0, "rows": 0} for rule in RULES}
        for item in self.items:
            found[item.rule]["count"] += 1
            found[item.rule]["bytes"] += item.size_bytes
            found[item.rule]["rows"] += item.rows
        return found

    def to_result(self) -> dict[str, Any]:
        """Counts and bytes per rule, with no path: what the job records."""
        return {
            "dry_run": self.dry_run,
            "now": self.now.isoformat(),
            "rules": self.totals(),
            "count": len(self.items),
            "bytes": sum(item.size_bytes for item in self.items),
            "kept": len(self.kept),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.to_result(),
            "items": [asdict(item) for item in self.items],
            "kept_notes": list(self.kept),
        }


# ── Where retention may delete ────────────────────────────────────────────


def check_folders(data_dir: Path, movie_roots: Iterable[Path]) -> Path:
    """The data folder, resolved; refuses when it and a movie root overlap."""
    try:
        data = data_dir.resolve(strict=True)
    except OSError as exc:
        raise RetentionRefusedError(f"the data folder {data_dir} cannot be read") from exc
    for root in movie_roots:
        movie_root = root.resolve()
        if data == movie_root or data.is_relative_to(movie_root):
            raise RetentionRefusedError(
                f"the data folder {data} is inside the movie root {movie_root}; retention "
                "deletes nothing while the library could be in reach"
            )
        if movie_root.is_relative_to(data):
            raise RetentionRefusedError(
                f"the movie root {movie_root} is inside the data folder {data}; retention "
                "deletes nothing while the library could be in reach"
            )
    return data


def _last_change(path: Path) -> float | None:
    """The newest modification time of ``path`` and everything under it (links are not
    followed); ``None`` when it is gone."""
    try:
        newest = os.lstat(path).st_mtime
    except FileNotFoundError:
        return None
    if stat.S_ISDIR(os.lstat(path).st_mode):
        for folder, folders, files in os.walk(path, followlinks=False):
            for name in (*folders, *files):
                try:
                    newest = max(newest, os.lstat(os.path.join(folder, name)).st_mtime)
                except FileNotFoundError:
                    continue
    return newest


def _size(path: Path) -> int:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return 0
    if not stat.S_ISDIR(info.st_mode):
        return info.st_size
    total = 0
    for folder, _, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except FileNotFoundError:
                continue
    return total


def _relative(data: Path, path: Path) -> str:
    return path.relative_to(data).as_posix()


# ── Jobs ──────────────────────────────────────────────────────────────────


def _job_conditions(cutoff: datetime) -> list[ColumnElement[bool]]:
    """A top-level job that may go, with its children: see the module docstring."""
    evaluation_jobs = select(Evaluation.job_id).where(Evaluation.job_id.is_not(None))
    schedule_jobs = select(JobSchedule.last_job_id).where(JobSchedule.last_job_id.is_not(None))

    def gone(job: Any) -> ColumnElement[bool]:
        return and_(
            job.state.in_(FINISHED),
            job.finished_at < cutoff,
            job.id.not_in(evaluation_jobs),
            job.id.not_in(schedule_jobs),
        )

    child = aliased(Job)
    return [
        Job.parent_id.is_(None),
        gone(Job),
        ~exists(select(child.id).where(child.parent_id == Job.id, ~gone(child))),
    ]


async def _plan_jobs(session: AsyncSession, cutoff: datetime) -> list[Item]:
    ids = list(
        await session.scalars(select(Job.id).where(*_job_conditions(cutoff)).order_by(Job.id))
    )
    if not ids:
        return []
    members = dict(
        (
            await session.execute(
                select(Job.id, Job.parent_id).where(or_(Job.id.in_(ids), Job.parent_id.in_(ids)))
            )
        ).all()
    )
    events = dict(
        (
            await session.execute(
                select(JobEvent.job_id, func.count())
                .where(JobEvent.job_id.in_(list(members)))
                .group_by(JobEvent.job_id)
            )
        ).all()
    )
    items = []
    for job_id in ids:
        group = [job_id, *(m for m, parent in members.items() if parent == job_id)]
        rows = len(group) + sum(int(events.get(member, 0)) for member in group)
        items.append(Item("jobs", f"job {job_id}", rows=rows))
    return items


async def _delete_jobs(session: AsyncSession, cutoff: datetime, planned: list[Item]) -> list[Item]:
    by_id = {int(item.name.split()[1]): item for item in planned}
    if not by_id:
        return []
    deleted = await session.scalars(
        delete(Job)
        .where(Job.id.in_(by_id), *_job_conditions(cutoff))
        .returning(Job.id)
        .execution_options(synchronize_session=False)
    )
    gone = sorted(deleted)
    await session.commit()
    return [by_id[job_id] for job_id in gone]


# ── Runs ──────────────────────────────────────────────────────────────────


def _run_conditions(cutoff: datetime) -> list[ColumnElement[bool]]:
    """A run that may go: see the module docstring."""
    newest = select(func.max(PosterRun.id)).where(in_product_loop()).group_by(PosterRun.movie_id)
    newest_completed = (
        select(func.max(PosterRun.id))
        .where(in_product_loop(), PosterRun.state == RUN_COMPLETED)
        .group_by(PosterRun.movie_id)
    )
    newest_evaluation = select(func.max(Evaluation.id)).scalar_subquery()
    newest_succeeded = (
        select(func.max(Evaluation.id))
        .join(Job, Job.id == Evaluation.job_id)
        .where(Job.state == "succeeded")
        .scalar_subquery()
    )
    current_masked = select(EvaluationResult.run_id).where(
        EvaluationResult.run_id.is_not(None),
        EvaluationResult.evaluation_id.in_([newest_evaluation, newest_succeeded]),
    )
    return [
        PosterRun.state != RUN_ACTIVE,
        func.coalesce(PosterRun.finished_at, PosterRun.started_at) < cutoff,
        ~exists(select(Deployment.id).where(Deployment.run_id == PosterRun.id)),
        ~exists(select(PreferenceEvent.id).where(PreferenceEvent.run_id == PosterRun.id)),
        PosterRun.id.not_in(newest),
        PosterRun.id.not_in(newest_completed),
        PosterRun.id.not_in(current_masked),
    ]


async def _plan_runs(session: AsyncSession, cutoff: datetime, data: Path) -> list[Item]:
    candidates = (
        select(func.count())
        .select_from(RunCandidate)
        .where(RunCandidate.run_id == PosterRun.id)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(PosterRun.id, PosterRun.run_folder, candidates)
            .where(*_run_conditions(cutoff))
            .order_by(PosterRun.id)
        )
    ).all()
    items = []
    for run_id, folder, count in rows:
        size = await asyncio.to_thread(_size, settings.runs_dir / folder)
        items.append(Item("runs", f"run {run_id} ({folder})", size, 1 + int(count)))
    return items


async def _delete_runs(session: AsyncSession, cutoff: datetime, planned: list[Item]) -> list[Item]:
    by_id = {int(item.name.split()[1]): item for item in planned}
    if not by_id:
        return []
    removed = await runs.delete_runs(session, PosterRun.id.in_(by_id), *_run_conditions(cutoff))
    return [by_id[run_id] for run_id, _, _ in sorted(removed)]


# ── Files ─────────────────────────────────────────────────────────────────


def _files_older(folder: Path, before: float) -> list[Path]:
    """Every regular file under ``folder`` last modified before ``before``; links skipped."""
    found: list[Path] = []
    if not folder.is_dir() or folder.is_symlink():
        return found
    for root, _, files in os.walk(folder, followlinks=False):
        for name in files:
            path = Path(root) / name
            try:
                info = os.lstat(path)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_mtime < before:
                found.append(path)
    return sorted(found)


def _run_folders(
    data: Path, names_with_rows: set[str], before: float, kept: list[str]
) -> list[Item]:
    folder = settings.runs_dir
    items: list[Item] = []
    if not folder.is_dir():
        return items
    for entry in sorted(os.scandir(folder), key=lambda e: e.name):
        if entry.name in names_with_rows:
            continue
        if entry.is_symlink():
            kept.append(f"run_folders: {_relative(data, Path(entry.path))} is a link; left alone")
            continue
        if not entry.is_dir(follow_symlinks=False):
            continue
        last = _last_change(Path(entry.path))
        if last is not None and last < before:
            path = Path(entry.path)
            items.append(Item("run_folders", _relative(data, path), _size(path)))
    return items


@dataclass(frozen=True)
class _Owners:
    """What the queued and running jobs could own."""

    job_ids: frozenset[int]
    kinds: frozenset[str]
    masked_movies: frozenset[int]


async def _owners(session: AsyncSession) -> _Owners:
    rows = (
        await session.execute(
            select(Job.id, Job.kind, Job.payload).where(Job.state.in_(ACTIVE_STATES))
        )
    ).all()
    masked = set()
    for _, kind, payload in rows:
        if kind == "poster_run_masked" and isinstance(payload, dict):
            movie = payload.get("movie_id")
            if isinstance(movie, int):
                masked.add(movie)
    return _Owners(
        job_ids=frozenset(int(job_id) for job_id, _, _ in rows),
        kinds=frozenset(kind for _, kind, _ in rows),
        masked_movies=frozenset(masked),
    )


def _leftover_owned(area: str, name: str, owners: _Owners) -> bool:
    """Whether a queued or running job could still need this leftover."""
    if area == "deploys":
        found = _JOB_FILE.fullmatch(name)
        if found:
            return int(found.group(1)) in owners.job_ids
        return bool(owners.kinds & {"poster_deploy", "poster_restore"})
    if area == "taste-build":
        if name.isdigit():
            return int(name) in owners.job_ids
        return "taste_rebuild" in owners.kinds
    if area == "blind-profiles":
        movie = name.rsplit("-", 1)[-1].removesuffix(".npz")
        if movie.isdigit():
            return int(movie) in owners.masked_movies
        return "poster_run_masked" in owners.kinds
    if area == "dumps":
        return "db_backup" in owners.kinds
    if area in ("profiles", "residuals"):
        return "taste_rebuild" in owners.kinds
    return bool(owners.kinds)


def _leftover_candidates(data: Path, recorded: set[str], kept: list[str]) -> list[tuple[str, Path]]:
    """``(area, path)`` of every leftover the rule names, before the age and owner checks."""
    found: list[tuple[str, Path]] = []
    for area, folder in (
        ("deploys", settings.deploy_staging_dir),
        ("blind-profiles", settings.blind_profiles_dir),
        ("taste-build", settings.taste_build_dir),
    ):
        if folder.is_dir() and not folder.is_symlink():
            found.extend((area, Path(entry.path)) for entry in os.scandir(folder))
    temporaries = (
        ("dumps", settings.db_backups_dir, (".marquee-dump-", ".marquee-restore-"), ""),
        ("profiles", settings.profiles_dir, (".",), ".part"),
        ("residuals", settings.residuals_dir, (".",), ".part"),
    )
    for area, folder, prefixes, suffix in temporaries:
        if not folder.is_dir() or folder.is_symlink():
            continue
        for entry in os.scandir(folder):
            if entry.name.startswith(prefixes) and entry.name.endswith(suffix):
                found.append((area, Path(entry.path)))
            pending = _PENDING.fullmatch(entry.name) if area == "residuals" else None
            if pending is None:
                continue
            if pending.group(1) in recorded:
                found.append((area, Path(entry.path)))
            else:
                kept.append(
                    f"leftovers: {_relative(data, Path(entry.path))} is kept: the next taste "
                    "rebuild reads it to record its training"
                )
    text_profiles = settings.text_profiles_path.with_suffix(".json.tmp")
    if text_profiles.exists() or text_profiles.is_symlink():
        found.append(("text-profiles", text_profiles))
    return sorted(set(found), key=lambda pair: pair[1])


async def _plan_leftovers(
    session: AsyncSession, data: Path, before: float, kept: list[str]
) -> list[Item]:
    recorded = set(
        await session.scalars(
            select(ResidualTraining.artifact_sha256).where(
                ResidualTraining.artifact_sha256.is_not(None)
            )
        )
    )
    owners = await _owners(session)
    items = []
    for area, path in await asyncio.to_thread(_leftover_candidates, data, recorded, kept):
        last = await asyncio.to_thread(_last_change, path)
        if last is None or last >= before:
            continue
        if _leftover_owned(area, path.name, owners):
            kept.append(
                f"leftovers: {_relative(data, path)} is kept: a queued or running job may own it"
            )
            continue
        items.append(Item("leftovers", _relative(data, path), await asyncio.to_thread(_size, path)))
    return items


def _copies(rule: str, folder: Path, keep: set[str], data: Path) -> list[Item]:
    if not folder.is_dir() or folder.is_symlink():
        return []
    items = []
    for entry in sorted(os.scandir(folder), key=lambda e: e.name):
        if (
            _ARTIFACT.fullmatch(entry.name)
            and entry.name not in keep
            and entry.is_file(follow_symlinks=False)
        ):
            items.append(Item(rule, _relative(data, Path(entry.path)), entry.stat().st_size))
    return items


async def _kept_copies(session: AsyncSession) -> tuple[set[str], set[str]]:
    def newest(model: Any, column: Any) -> Select[Any]:
        return (
            select(column).where(column.is_not(None)).order_by(model.id.desc()).limit(KEEP_COPIES)
        )

    profiles = set(await session.scalars(newest(TasteProfile, TasteProfile.file_name)))
    profiles |= set(
        await session.scalars(
            select(TasteProfile.file_name).where(
                TasteProfile.is_active | (TasteProfile.source == PROFILE_SOURCE_SEED)
            )
        )
    )
    residuals = set(await session.scalars(newest(ResidualTraining, ResidualTraining.file_name)))
    residuals |= set(
        await session.scalars(
            select(ResidualTraining.file_name).where(
                ResidualTraining.is_active, ResidualTraining.file_name.is_not(None)
            )
        )
    )
    folder = settings.residuals_dir
    if folder.is_dir():
        for entry in os.scandir(folder):
            pending = _PENDING.fullmatch(entry.name)
            if pending:
                residuals.add(f"{pending.group(1)}.npz")
    return profiles, residuals


# ── The pass ──────────────────────────────────────────────────────────────


async def plan(session: AsyncSession, *, now: datetime | None = None) -> Report:
    """What each rule would delete now, and what it keeps and why. Deletes nothing."""
    check_folders(settings.DATA_DIR, settings.MOVIE_ROOTS)
    data = settings.DATA_DIR
    now = now or datetime.now(UTC)
    days = timedelta(days=settings.RETENTION_DAYS)
    report = Report(now=now, dry_run=True)
    report.items += await _plan_jobs(session, now - days)
    report.items += await _plan_runs(session, now - days, data)
    names_with_rows = set(await session.scalars(select(PosterRun.run_folder)))
    folder_cutoff = (now - timedelta(days=RUN_FOLDER_DAYS)).timestamp()
    report.items += await asyncio.to_thread(
        _run_folders, data, names_with_rows, folder_cutoff, report.kept
    )
    for rule, folder, age in (
        ("thumbnails", settings.thumbnail_cache_dir, timedelta(days=THUMBNAIL_DAYS)),
        ("embeddings", settings.embedding_cache_dir, days),
    ):
        old = await asyncio.to_thread(_files_older, folder, (now - age).timestamp())
        report.items += [Item(rule, _relative(data, p), p.stat().st_size) for p in old]
    leftover_cutoff = (now - timedelta(hours=LEFTOVER_HOURS)).timestamp()
    report.items += await _plan_leftovers(session, data, leftover_cutoff, report.kept)
    profiles, residuals = await _kept_copies(session)
    report.items += _copies("profiles", settings.profiles_dir, profiles, data)
    report.items += _copies("residuals", settings.residuals_dir, residuals, data)
    await session.commit()
    return report


def _delete_path(boundary: FilesystemBoundary, data: Path, item: Item) -> bool:
    path = data / item.name
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return False
    classified = boundary.classify(path, roots=(_ROOT,), write=True)
    if stat.S_ISDIR(info.st_mode):
        boundary.cleanup_directory(classified)
        return True
    return boundary.delete_file(classified)


async def run(
    session: AsyncSession, *, now: datetime | None = None, dry_run: bool = False
) -> Report:
    """Plan, and unless ``dry_run`` delete exactly what was planned, each item checked again
    just before it goes. The report lists what went (or, in a dry run, would go)."""
    planned = await plan(session, now=now)
    if dry_run:
        return planned
    now = planned.now
    days = timedelta(days=settings.RETENTION_DAYS)
    check_folders(settings.DATA_DIR, settings.MOVIE_ROOTS)
    data = settings.DATA_DIR
    report = Report(now=now, dry_run=False, kept=planned.kept)
    by_rule: dict[str, list[Item]] = {rule: [] for rule in RULES}
    for item in planned.items:
        by_rule[item.rule].append(item)
    report.items += await _delete_jobs(session, now - days, by_rule["jobs"])
    report.items += await _delete_runs(session, now - days, by_rule["runs"])

    # Files: checked again (still there, still old enough, still no owner) before each goes.
    names_with_rows = set(await session.scalars(select(PosterRun.run_folder)))
    owners = await _owners(session)
    keep = dict(zip(("profiles", "residuals"), await _kept_copies(session), strict=True))
    await session.commit()
    ages = {
        "run_folders": timedelta(days=RUN_FOLDER_DAYS),
        "thumbnails": timedelta(days=THUMBNAIL_DAYS),
        "embeddings": days,
        "leftovers": timedelta(hours=LEFTOVER_HOURS),
    }
    boundary = boundary_for_roots({_ROOT: data}, access="read_write", purpose="retention")
    for rule in ("run_folders", "thumbnails", "embeddings", "leftovers", "profiles", "residuals"):
        for item in by_rule[rule]:
            path = data / item.name
            if rule in ages:
                last = await asyncio.to_thread(_last_change, path)
                if last is None or last >= (now - ages[rule]).timestamp():
                    continue
            if rule == "run_folders" and path.name in names_with_rows:
                continue
            if rule == "leftovers" and _leftover_owned(_area(path), path.name, owners):
                continue
            if rule in keep and path.name in keep[rule]:
                continue
            try:
                gone = await asyncio.to_thread(_delete_path, boundary, data, item)
            except (OSError, FilesystemBoundaryError) as exc:
                logger.warning("RETENTION | %s: one item stays: %s", rule, exc)
                continue
            if gone:
                logger.debug("RETENTION | %s: deleted %s", rule, item.name)
                report.items.append(item)
    totals = report.totals()
    logger.info(
        "RETENTION | deleted %d item(s), %d bytes: %s",
        len(report.items),
        sum(item.size_bytes for item in report.items),
        ", ".join(f"{rule} {t['count']}" for rule, t in totals.items() if t["count"]),
    )
    return report


def _area(path: Path) -> str:
    parent = path.parent
    for area, folder in (
        ("deploys", settings.deploy_staging_dir),
        ("blind-profiles", settings.blind_profiles_dir),
        ("taste-build", settings.taste_build_dir),
        ("dumps", settings.db_backups_dir),
        ("profiles", settings.profiles_dir),
        ("residuals", settings.residuals_dir),
    ):
        if parent == folder:
            return area
    return "text-profiles"
