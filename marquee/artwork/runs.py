"""The run record: one ``poster_runs`` row per pipeline run, one ``run_candidates`` row per
candidate it considered.

A run belongs to one ``poster_run`` job and is made in four steps, each safe to repeat:

1. :func:`start` finds or creates the row for the job. On a second attempt of the job it
   deletes the first attempt's candidates and folder and sets the row running again, so a
   job never has two rows, folders, or sets of candidates.
2. The GPU child runs the pipeline in the run's folder, ``<runs dir>/<run id, 8 digits>-<title
   slug>``. The folder's name is stored, never its path.
3. :func:`read_run_folder` reads the archive the pipeline left there and hashes each
   downloaded candidate (off the event loop).
4. :func:`complete` stores the candidates and marks the pick, in one transaction. Or
   :func:`fail` or :func:`cancel` ends the run instead.

The archive (``pipeline_run.json``) stays the full record of a run; the tables hold what is
queried. Nothing here imports the engine, so the worker can use it without loading a model.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from marquee.config import settings
from marquee.db.models import ARTWORK_POSTER, Artwork, Job, Movie, PosterRun, RunCandidate
from marquee.db.models.job import ACTIVE_STATES
from marquee.db.models.run import (
    RUN_ACTIVE,
    RUN_CANCELLED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_STATES,
    in_product_loop,
)
from marquee.fs.boundary import FilesystemBoundaryError, boundary_for_roots

logger = logging.getLogger(__name__)

RUN_ARCHIVE = "pipeline_run.json"
"""The archive's name, as ``marquee.pipeline.single_run`` writes it (not imported: the engine
would load with it)."""
RESIDUAL_MARK = "residual_delta"
"""A contribution only the residual scorer writes (``pipeline/scorer.py::ResidualScorer``)."""
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
RANKED_SHOWN = 10
SLUG_LENGTH = 60
ABANDONED = "its job ended without finishing the run"

Scope = Literal["never-run", "no-poster", "all"]
SCOPES: tuple[Scope, ...] = ("never-run", "no-poster", "all")


class RunRecordError(Exception):
    """The run record cannot be changed as asked; the message says why."""


# ── Names and folders ─────────────────────────────────────────────────────


def title_slug(title: str) -> str:
    """Lower case ASCII words joined by hyphens: accents and apostrophes dropped."""
    decomposed = unicodedata.normalize("NFKD", title)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = re.sub(r"['’`]", "", stripped.casefold())
    slug = re.sub(r"[^0-9a-z]+", "-", lowered).strip("-")[:SLUG_LENGTH].strip("-")
    return slug or "untitled"


def run_folder_name(run_id: int, title: str) -> str:
    """``<run id, 8 digits>-<title slug>``: a run's folder is named after its record."""
    return f"{run_id:08d}-{title_slug(title)}"


def run_dir(name: str, runs_dir: Path | None = None) -> Path:
    """The local folder of the run whose folder is called ``name``, inside the runs directory.

    Raises :class:`RunRecordError` for anything but a plain name.
    """
    base = runs_dir or settings.runs_dir
    if not name or name in {".", ".."} or "/" in name or "\0" in name:
        raise RunRecordError(f"{name!r} is not a run folder's name")
    return base / name


def remove_run_folder(name: str, runs_dir: Path | None = None) -> bool:
    """Delete a run's folder and everything in it; ``False`` when there was none. Blocking.

    The deletion goes through :mod:`marquee.fs.boundary`, confined to the runs directory. A
    symbolic link in its place is refused; one inside it is removed as a link, never followed.
    """
    base = runs_dir or settings.runs_dir
    folder = run_dir(name, base)
    if folder.is_symlink():
        raise RunRecordError(f"run folder {name!r} is a symbolic link; not removed")
    if not folder.exists():
        return False
    boundary = boundary_for_roots({"runs": base}, access="read_write", purpose="run folders")
    try:
        boundary.cleanup_directory(boundary.from_key("runs", name))
    except FilesystemBoundaryError as exc:
        raise RunRecordError(f"run folder {name!r} was not removed: {exc}") from exc
    return True


# ── Which movies can run ──────────────────────────────────────────────────


def ineligible_reason(movie: Movie) -> str | None:
    """Why ``movie`` cannot be run, or ``None`` when it can.

    The text gate reads the director, studios, and tagline TMDB gave the sync; a run without
    them would decide differently without saying so.
    """
    if not movie.is_present:
        return f"movie {movie.id} ({movie.title}) is no longer listed by Radarr"
    if not movie.has_file:
        return (
            f"movie {movie.id} ({movie.title}) has no file, so it has no folder to put a poster in"
        )
    if movie.tmdb_id is None:
        return f"movie {movie.id} ({movie.title}) has no TMDB id"
    if movie.tmdb_enriched_at is None:
        return (
            f"movie {movie.id} ({movie.title}) has no TMDB details yet; "
            "run a library sync first (`marquee sync`)"
        )
    return None


def eligible() -> ColumnElement[bool]:
    """:func:`ineligible_reason` as SQL: the movies a run accepts."""
    return and_(
        Movie.is_present.is_(True),
        Movie.has_file.is_(True),
        Movie.tmdb_id.is_not(None),
        Movie.tmdb_enriched_at.is_not(None),
    )


async def scope_movie_ids(
    session: AsyncSession, scope: Scope, *, limit: int | None = None
) -> list[int]:
    """The eligible movies a batch of this scope covers, by title.

    ``never-run``: no run record of the product loop (a masked run does not count);
    ``no-poster``: no poster row; ``all``: every one.
    """
    conditions: list[ColumnElement[bool]] = [eligible()]
    if scope == "never-run":
        conditions.append(
            ~exists(select(PosterRun.id).where(PosterRun.movie_id == Movie.id, in_product_loop()))
        )
    elif scope == "no-poster":
        conditions.append(
            ~exists(
                select(Artwork.id).where(
                    Artwork.movie_id == Movie.id, Artwork.artwork_kind == ARTWORK_POSTER
                )
            )
        )
    elif scope != "all":
        raise ValueError(f"unknown scope {scope!r}; one of {', '.join(SCOPES)}")
    statement = select(Movie.id).where(*conditions).order_by(Movie.sort_title, Movie.id)
    if limit is not None:
        statement = statement.limit(limit)
    return list(await session.scalars(statement))


# ── What the child returns, and what the folder holds ─────────────────────


@dataclass(frozen=True)
class ChildResult:
    """What the GPU child reports about a run it finished."""

    status: str
    counts: dict[str, int]
    stages: list[dict[str, Any]]
    scorer: str | None
    engine_run_id: str
    ai_model: str
    profile_sha256: str
    pick_file: str | None
    residual_id: int | None = None
    """The residual training whose artifact the engine says ranked the run."""

    @classmethod
    def from_dict(cls, value: Any) -> ChildResult:
        if not isinstance(value, dict):
            raise RunRecordError(f"the GPU child returned {type(value).__name__}, not a dict")
        try:
            return cls(
                status=str(value["status"]),
                counts=dict(value["counts"]),
                stages=list(value["stages"]),
                scorer=value.get("scorer"),
                engine_run_id=str(value["engine_run_id"]),
                ai_model=str(value["ai_model"]),
                profile_sha256=str(value["profile_sha256"]),
                pick_file=value.get("pick_file"),
                residual_id=_optional_int(value.get("residual_id")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RunRecordError(f"the GPU child's result is incomplete: {exc}") from exc


@dataclass(frozen=True)
class CandidateRow:
    source_file: str
    sha256: str | None
    stage_reached: str | None
    gate_decision: str | None
    rejection_reason: str | None
    rank: int | None
    final_score: float | None
    stack_rank: int | None
    stack_label: str | None


@dataclass(frozen=True)
class RunFolder:
    """What a finished run's folder holds: its candidates and the summary to report."""

    candidates: list[CandidateRow]
    ranked_total: int
    ranked: list[dict[str, Any]] = field(default_factory=list)
    """The top :data:`RANKED_SHOWN` ranked candidates, as :func:`ranked_row` gives them."""
    residual_ranked: bool = False
    """Every ranked candidate carries the residual's contributions: the residual ranked it.
    The engine can fall back to the weighted scorer part way through a run; then this is
    false whatever it reported."""


def stack_ranks(payload: dict[str, Any]) -> dict[str, int]:
    """File name -> design stack rank, from the run archive's diagnostic ledger."""
    ledger = payload.get("diagnostic_ledger")
    candidates = ledger.get("candidates", []) if isinstance(ledger, dict) else []
    return {
        str(c["orig_filename"]): int(c["stack_rank"])
        for c in candidates
        if isinstance(c, dict) and c.get("stack_rank") is not None
    }


def ranked_row(row: dict[str, Any] | None, ranks: dict[str, int]) -> dict[str, Any] | None:
    """One ranked candidate with its full stack label (``1A``: design stack 1, variant A)."""
    if row is None:
        return None
    stack = ranks.get(str(row["orig_filename"]))
    letter = row.get("stack_label") or ""
    return {
        "rank": row["rank"],
        "stack": f"{stack}{letter}" if stack is not None else letter or None,
        "score": row["final_score"],
        "file": row["orig_filename"],
    }


def candidate_row(row: RunCandidate) -> dict[str, Any]:
    """A stored candidate in the shape of :func:`ranked_row`."""
    stack = (
        f"{row.stack_rank}{row.stack_label or ''}"
        if row.stack_rank is not None
        else row.stack_label or None
    )
    return {"rank": row.rank, "stack": stack, "score": row.final_score, "file": row.source_file}


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def read_run_folder(folder: Path) -> RunFolder:
    """Read a finished run's archive and hash its downloaded candidates. Blocking.

    Each downloaded candidate is an image at the top level of the folder named by TMDB's
    file name (copies in the stage folders below it are not counted). A candidate with no
    file there was never downloaded and gets no hash.
    """
    try:
        payload = json.loads((folder / RUN_ARCHIVE).read_text(encoding="utf-8"))
        rows = payload["diagnostic_ledger"]["candidates"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RunRecordError(f"the run archive in {folder.name} cannot be read: {exc}") from exc
    hashes = {
        path.name: file_sha256(path)
        for path in folder.iterdir()
        if path.suffix.lower() in IMAGE_SUFFIXES and path.is_file() and not path.is_symlink()
    }
    candidates = [
        CandidateRow(
            source_file=str(row["orig_filename"]),
            sha256=hashes.get(str(row["orig_filename"])),
            stage_reached=row.get("stage_reached"),
            gate_decision=row.get("gate_decision"),
            rejection_reason=row.get("rejection_reason"),
            rank=_optional_int(row.get("rank")),
            final_score=_optional_float(row.get("final_score")),
            stack_rank=_optional_int(row.get("stack_rank")),
            stack_label=row.get("stack_label"),
        )
        for row in rows
    ]
    ranks = stack_ranks(payload)
    ranked = sorted((row for row in rows if row.get("rank") is not None), key=lambda r: r["rank"])
    return RunFolder(
        candidates=candidates,
        ranked_total=len(ranked),
        ranked=[ranked_row(row, ranks) for row in ranked[:RANKED_SHOWN]],  # type: ignore[misc]
        residual_ranked=bool(ranked)
        and all(RESIDUAL_MARK in (row.get("contributions") or {}) for row in ranked),
    )


# ── What a score is made of ───────────────────────────────────────────────

RESIDUAL_PART = "residual"
"""The part that holds what the residual changed, in a run the residual ranked."""
_BASELINE_PREFIX = "baseline:"


@dataclass(frozen=True)
class ScorePart:
    """One part of a ranked candidate's score."""

    feature: str
    """The engine's name for it (``knn_sim``), or :data:`RESIDUAL_PART`."""
    contribution: float
    """What it added to the final score; the parts of one candidate sum to its score."""
    value: float | None
    """How the candidate did on the feature, from 0 to 1; none for the residual's part."""


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def score_parts(row: dict[str, Any]) -> list[ScorePart]:
    """The parts of one ledger row's score, in the engine's order.

    The weighted scorer writes one contribution per feature. The residual scorer writes the
    weighted ones under ``baseline:<feature>`` beside its own working; those are given as
    the features, and the difference the residual made to the score as one more part.
    """
    contributions = row.get("contributions")
    if not isinstance(contributions, dict):
        return []
    normalized = row.get("normalized_features")
    values = normalized if isinstance(normalized, dict) else {}
    residual = RESIDUAL_MARK in contributions
    parts: list[ScorePart] = []
    for name, raw in contributions.items():
        if residual and not str(name).startswith(_BASELINE_PREFIX):
            continue
        feature = str(name).removeprefix(_BASELINE_PREFIX) if residual else str(name)
        contribution = _finite(raw)
        if contribution is not None:
            parts.append(ScorePart(feature, contribution, _finite(values.get(feature))))
    if residual:
        final = _finite(contributions.get("final_score"))
        baseline = _finite(contributions.get("baseline_score"))
        if final is not None and baseline is not None:
            parts.append(ScorePart(RESIDUAL_PART, final - baseline, None))
    return parts


def read_score_parts(folder: Path) -> dict[str, list[ScorePart]]:
    """What each ranked candidate's score is made of, by TMDB file name. Blocking.

    Read from the run archive, which is the only place the parts are kept. They explain a
    score and are no part of the run's record, so an archive that is gone or cannot be read
    gives nothing rather than an error.
    """
    try:
        payload = json.loads((folder / RUN_ARCHIVE).read_text(encoding="utf-8"))
        rows = payload["diagnostic_ledger"]["candidates"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    found: dict[str, list[ScorePart]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("rank") is None:
            continue
        parts = score_parts(row)
        if parts:
            found[str(row.get("orig_filename"))] = parts
    return found


# ── Changing the record ───────────────────────────────────────────────────


async def start(
    session: AsyncSession, *, job_id: int, movie: Movie, masked: bool = False
) -> PosterRun:
    """Find or create the run of this job, set it running with no candidates, and commit.

    ``masked`` marks a blind evaluation's run (see :func:`in_product_loop`).

    A run of the same movie still marked running whose job is no longer active was left by
    a job that ended without finishing it (a worker killed on its last attempt); it is
    marked failed here, since one movie has at most one active run.
    """
    now = datetime.now(UTC)
    job_active = exists(
        select(Job.id).where(Job.id == PosterRun.job_id, Job.state.in_(ACTIVE_STATES))
    )
    await session.execute(
        update(PosterRun)
        .where(
            PosterRun.movie_id == movie.id,
            PosterRun.state == RUN_ACTIVE,
            or_(PosterRun.job_id.is_(None), PosterRun.job_id != job_id),
            ~job_active,
        )
        .values(state=RUN_FAILED, error=ABANDONED, finished_at=now)
    )
    run = await session.scalar(
        select(PosterRun).where(PosterRun.job_id == job_id).with_for_update(),
        execution_options={"populate_existing": True},
    )
    if run is not None and run.state == RUN_COMPLETED:
        # Feedback and deployments point at a completed run's candidates; starting it over
        # would delete them. The handler returns the recorded result instead.
        run_id = run.id
        await session.rollback()
        raise RunRecordError(f"run {run_id} of job {job_id} already completed")
    stale: set[str] = set()
    if run is None:
        # The id comes first, so the row is inserted with its folder's name in one go.
        run_id = await session.scalar(
            select(func.nextval(func.pg_get_serial_sequence("poster_runs", "id")))
        )
        assert run_id is not None
        run = PosterRun(
            id=run_id,
            movie_id=movie.id,
            job_id=job_id,
            state=RUN_ACTIVE,
            run_folder=run_folder_name(run_id, movie.title),
            started_at=now,
            masked=masked,
        )
        session.add(run)
    else:
        if run.masked != masked:
            run_id = run.id
            await session.rollback()
            raise RunRecordError(f"run {run_id} of job {job_id} is of the other kind")
        stale.add(run.run_folder)
        await session.execute(delete(RunCandidate).where(RunCandidate.run_id == run.id))
        run.state = RUN_ACTIVE
        run.run_folder = run_folder_name(run.id, movie.title)
        run.engine_run_id = run.engine_status = run.scorer_name = None
        run.ai_model = run.profile_sha256 = run.error = None
        run.residual_id = None
        run.counts = run.stages = None
        run.candidate_count = run.ranked_count = None
        run.started_at = now
        run.finished_at = None
    await session.flush()
    # The folder of an earlier attempt goes, and anything already at the new name: the
    # pipeline insists on a folder that does not exist yet.
    stale.add(run.run_folder)
    for name in sorted(stale):
        if await asyncio.to_thread(remove_run_folder, name):
            logger.info("RUN | %d: removed %s from an earlier attempt", run.id, name)
    await session.commit()
    return run


async def _running(session: AsyncSession, run_id: int) -> PosterRun:
    run = await session.scalar(
        select(PosterRun).where(PosterRun.id == run_id).with_for_update(),
        execution_options={"populate_existing": True},
    )
    if run is None:
        raise RunRecordError(f"no run {run_id}")
    if run.state != RUN_ACTIVE:
        raise RunRecordError(f"run {run_id} is {run.state}, not running")
    return run


async def complete(
    session: AsyncSession, run_id: int, result: ChildResult, folder: RunFolder
) -> PosterRun:
    """Store the run's candidates, mark the pick, and finish the run; one transaction."""
    run = await _running(session, run_id)
    names = [c.source_file for c in folder.candidates]
    if result.pick_file is not None and result.pick_file not in names:
        raise RunRecordError(
            f"run {run_id}: the pick {result.pick_file} is not among its candidates"
        )
    await session.execute(delete(RunCandidate).where(RunCandidate.run_id == run_id))
    session.add_all(
        RunCandidate(
            run_id=run_id,
            source_file=c.source_file,
            sha256=c.sha256,
            stage_reached=c.stage_reached,
            gate_decision=c.gate_decision,
            rejection_reason=c.rejection_reason,
            rank=c.rank,
            final_score=c.final_score,
            stack_rank=c.stack_rank,
            stack_label=c.stack_label,
            is_pick=c.source_file == result.pick_file,
        )
        for c in folder.candidates
    )
    run.state = RUN_COMPLETED
    run.engine_run_id = result.engine_run_id
    run.engine_status = result.status
    residual = result.scorer == "residual" and folder.residual_ranked
    if result.scorer == "residual" and not residual:
        logger.warning("RUN | %d: the residual did not rank every candidate; weighted", run_id)
    run.scorer_name = "weighted" if result.scorer == "residual" and not residual else result.scorer
    run.residual_id = result.residual_id if residual else None
    run.ai_model = result.ai_model
    run.profile_sha256 = result.profile_sha256
    run.counts = result.counts
    run.stages = result.stages
    run.candidate_count = len(folder.candidates)
    run.ranked_count = sum(1 for c in folder.candidates if c.rank is not None)
    run.error = None
    run.finished_at = datetime.now(UTC)
    await session.commit()
    return run


async def fail(session: AsyncSession, run_id: int, error: str) -> bool:
    """Mark a running run failed with a one-line error; its folder stays for its log."""
    finished = await session.scalar(
        update(PosterRun)
        .where(PosterRun.id == run_id, PosterRun.state == RUN_ACTIVE)
        .values(state=RUN_FAILED, error=error[:500], finished_at=func.now())
        .returning(PosterRun.id)
    )
    await session.commit()
    return finished is not None


async def cancel(session: AsyncSession, run_id: int) -> bool:
    """Mark a running run cancelled and remove its folder."""
    name = await session.scalar(
        update(PosterRun)
        .where(PosterRun.id == run_id, PosterRun.state == RUN_ACTIVE)
        .values(state=RUN_CANCELLED, finished_at=func.now())
        .returning(PosterRun.run_folder)
    )
    await session.commit()
    if name is None:
        return False
    await asyncio.to_thread(remove_run_folder, name)
    return True


async def delete_runs(
    session: AsyncSession, *conditions: ColumnElement[bool]
) -> list[tuple[int, str, bool]]:
    """Delete the runs matching ``conditions``, rows and folders, and commit. Returns each
    run's id, folder name, and whether its folder was removed.

    A run still running is never deleted. Candidates go with their run; a deployment, a
    preference event, or an evaluation result that named it keeps its own record and loses
    the link. The rows go first, in one transaction; then each folder, so a folder that
    cannot be removed is left behind (it then has no row) rather than a row without its
    folder.
    """
    rows = (
        await session.execute(
            delete(PosterRun)
            .where(*conditions, PosterRun.state != RUN_ACTIVE)
            .returning(PosterRun.id, PosterRun.run_folder)
        )
    ).all()
    await session.commit()
    removed: list[tuple[int, str, bool]] = []
    for run_id, folder in rows:
        try:
            gone = await asyncio.to_thread(remove_run_folder, folder)
        except (RunRecordError, OSError) as exc:
            logger.warning("RUN | %d: its folder stays: %s", run_id, exc)
            gone = False
        removed.append((run_id, folder, gone))
    return removed


async def remove_older_masked(session: AsyncSession, movie_id: int, keep_run_id: int) -> list[int]:
    """Delete the movie's masked runs older than ``keep_run_id``, rows and folders, and commit.

    A movie keeps only its newest masked run. A run still running is left alone; a normal
    run is never touched. Candidates go with their run; an evaluation result keeps its
    numbers and loses its link. Returns the ids removed.
    """
    rows = await delete_runs(
        session,
        PosterRun.movie_id == movie_id,
        PosterRun.masked.is_(True),
        PosterRun.id < keep_run_id,
    )
    if rows:
        logger.info(
            "RUN | movie %d: %d older masked run(s) removed (%s)",
            movie_id,
            len(rows),
            ", ".join(str(run_id) for run_id, _, _ in rows),
        )
    return [run_id for run_id, _, _ in rows]


# ── Reading ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunPage:
    total: int
    items: list[PosterRun]


async def get_run(session: AsyncSession, run_id: int) -> PosterRun | None:
    return await session.get(PosterRun, run_id)


async def run_for_job(session: AsyncSession, job_id: int) -> PosterRun | None:
    return await session.scalar(select(PosterRun).where(PosterRun.job_id == job_id))


async def candidates_of(session: AsyncSession, run_id: int) -> list[RunCandidate]:
    """A run's candidates: ranked ones by rank, then the rest by file name."""
    rows = await session.scalars(
        select(RunCandidate)
        .where(RunCandidate.run_id == run_id)
        .order_by(RunCandidate.rank.asc().nulls_last(), RunCandidate.source_file)
    )
    return list(rows)


async def get_candidate(
    session: AsyncSession, run_id: int, candidate_id: int
) -> RunCandidate | None:
    return await session.scalar(
        select(RunCandidate).where(RunCandidate.id == candidate_id, RunCandidate.run_id == run_id)
    )


async def pick_of(session: AsyncSession, run_id: int) -> RunCandidate | None:
    return await session.scalar(
        select(RunCandidate).where(RunCandidate.run_id == run_id, RunCandidate.is_pick)
    )


async def list_runs(
    session: AsyncSession,
    *,
    movie_id: int | None = None,
    state: str | None = None,
    masked: bool | None = False,
    page: int = 1,
    page_size: int = 50,
) -> RunPage:
    """One page of runs, newest first. Masked runs only when asked: ``masked`` is ``False``
    (the product loop's runs, the default), ``True`` (masked runs only), or ``None`` (any)."""
    if state is not None and state not in RUN_STATES:
        raise ValueError(f"unknown run state {state!r}; one of {', '.join(RUN_STATES)}")
    conditions: list[ColumnElement[bool]] = []
    if masked is not None:
        conditions.append(PosterRun.masked.is_(masked))
    if movie_id is not None:
        conditions.append(PosterRun.movie_id == movie_id)
    if state is not None:
        conditions.append(PosterRun.state == state)
    total = await session.scalar(select(func.count()).select_from(PosterRun).where(*conditions))
    rows = await session.scalars(
        select(PosterRun)
        .where(*conditions)
        .order_by(PosterRun.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    return RunPage(total=total or 0, items=list(rows))


async def find_movies_by_title(
    session: AsyncSession, title: str, *, year: int | None = None
) -> list[Movie]:
    """Movies Radarr lists whose title is ``title``, case ignored (and of ``year``, if given)."""
    conditions: list[ColumnElement[bool]] = [
        Movie.is_present.is_(True),
        func.lower(Movie.title) == title.strip().lower(),
    ]
    if year is not None:
        conditions.append(Movie.year == year)
    return list(await session.scalars(select(Movie).where(*conditions).order_by(Movie.id)))
