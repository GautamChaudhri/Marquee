"""Blind evaluation: where the owner's poster lands when a movie is run without its own
exemplars.

An evaluation is a batch of masked runs (queued by :mod:`marquee.jobs.handlers.poster_run`).
When each masked run ends, its job records one result here: the owner's choice for the movie
(its active positive exemplar), the candidate of the run that is that poster, and what the
run did with it.

**The match** (decision 7 of the M5c brief): by TMDB file name when the exemplar has one
(feedback exemplars do; seed exemplars do not) and the run has that candidate; otherwise by
CLIP cosine between the exemplar's row in the profile the run was masked from and each
candidate's embedding from the embedding cache (keyed by model and the file's SHA-256). The
closest candidate is the match when its cosine is at least ``EVAL_MATCH_MIN_COSINE``. Nothing
loads a model: a candidate with no cached embedding (rejected before the style stage) cannot
be matched by cosine, and is counted.

**The outcome** (decision 8): ``ranked`` (its rank, of how many, and its card, where cards
are design stacks in the order the Review page shows them), ``gated`` (the stage and reason
that removed it), ``not_offered`` (no candidate reached the threshold; the closest is kept),
or ``failed`` (the run did not complete, or it ran against another taste profile than the
evaluation names: a rebuild published while the evaluation ran).

**The summary** (decision 9) is computed from the result rows whenever it is asked for, for
seed exemplars (the posters the owner picked by hand, with nothing shown first), feedback
exemplars (chosen from what Marquee showed), and both. The hand picks are a measurement only:
nothing here trains anything.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.artwork import runs
from marquee.config import pipeline_settings, settings
from marquee.db.models import (
    Evaluation,
    EvaluationResult,
    Exemplar,
    Job,
    Movie,
    PosterRun,
    RunCandidate,
)
from marquee.db.models.evaluation import (
    OUTCOME_FAILED,
    OUTCOME_GATED,
    OUTCOME_NOT_OFFERED,
    OUTCOME_RANKED,
)
from marquee.db.models.run import RUN_COMPLETED
from marquee.db.models.taste import POLARITY_POSITIVE, STATE_ACTIVE
from marquee.taste import rebuild

logger = logging.getLogger(__name__)

MATCHED_BY_FILE = "file"
MATCHED_BY_COSINE = "cosine"
SOURCES = ("seed", "feedback")
PROFILE_CHANGED = "the taste profile changed while the evaluation ran"


class EvaluationError(Exception):
    """An evaluation cannot be queued or read as asked; the message says why."""


# ── The match and the outcome (no database) ───────────────────────────────


@dataclass(frozen=True)
class Candidate:
    """What the match and the outcome read of one candidate of a masked run."""

    source_file: str
    sha256: str | None
    stage_reached: str | None = None
    gate_decision: str | None = None
    rejection_reason: str | None = None
    rank: int | None = None
    stack_rank: int | None = None
    is_pick: bool = False

    @classmethod
    def of(cls, row: RunCandidate) -> Candidate:
        return cls(
            source_file=row.source_file,
            sha256=row.sha256,
            stage_reached=row.stage_reached,
            gate_decision=row.gate_decision,
            rejection_reason=row.rejection_reason,
            rank=row.rank,
            stack_rank=row.stack_rank,
            is_pick=row.is_pick,
        )


@dataclass(frozen=True)
class Match:
    candidate: Candidate | None
    matched_by: str | None
    cosine: float | None
    best_cosine: float | None
    best_file: str | None
    unembedded: int


def _unit(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float64).reshape(-1)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-12 else vector


def match(
    exemplar: np.ndarray | None,
    candidates: Sequence[Candidate],
    embeddings: dict[str, np.ndarray],
    *,
    source_file: str | None = None,
    duplicates: dict[str, str] | None = None,
    threshold: float,
) -> Match:
    """Find the candidate that is the owner's poster.

    ``embeddings`` maps a candidate file's SHA-256 to its cached CLIP embedding.
    ``duplicates`` maps a candidate removed as a duplicate to the one it was kept as, so a
    file-name match on a duplicate follows it to its survivor. Ties in cosine go to the
    better-ranked candidate, then the file name.
    """
    by_name = {c.source_file: c for c in candidates}
    vector = _unit(exemplar) if exemplar is not None else None
    scored: list[tuple[float, Candidate]] = []
    unembedded = 0
    for candidate in candidates:
        cached = embeddings.get(candidate.sha256 or "")
        if cached is None:
            unembedded += 1
            continue
        if vector is not None:
            scored.append((float(vector @ _unit(cached)), candidate))
    scored.sort(
        key=lambda item: (
            -item[0],
            item[1].rank if item[1].rank is not None else math.inf,
            item[1].source_file,
        )
    )
    best_cosine, best = (scored[0][0], scored[0][1]) if scored else (None, None)
    cosine_of = {c.source_file: value for value, c in scored}
    if source_file is not None and source_file in by_name:
        named = by_name[source_file]
        kept = (duplicates or {}).get(source_file)
        if named.rank is None and kept in by_name:
            named = by_name[kept]
        return Match(
            named,
            MATCHED_BY_FILE,
            cosine_of.get(named.source_file),
            best_cosine,
            best.source_file if best else None,
            unembedded,
        )
    if best is not None and best_cosine is not None and best_cosine >= threshold:
        return Match(
            best, MATCHED_BY_COSINE, best_cosine, best_cosine, best.source_file, unembedded
        )
    return Match(None, None, None, best_cosine, best.source_file if best else None, unembedded)


def cards_of(candidates: Iterable[Candidate]) -> list[list[Candidate]]:
    """The ranked candidates as the Review page shows them: one card per design stack, in
    rank order of each card's best variant; a candidate with no stack is a card of its own."""
    ranked = sorted((c for c in candidates if c.rank is not None), key=lambda c: c.rank or 0)
    cards: list[list[Candidate]] = []
    by_stack: dict[int, list[Candidate]] = {}
    for candidate in ranked:
        if candidate.stack_rank is None:
            cards.append([candidate])
            continue
        card = by_stack.get(candidate.stack_rank)
        if card is None:
            card = by_stack[candidate.stack_rank] = []
            cards.append(card)
        card.append(candidate)
    return cards


@dataclass
class Outcome:
    """One result row's numbers, before it is stored."""

    outcome: str
    matched_by: str | None = None
    match_file: str | None = None
    match_sha256: str | None = None
    match_cosine: float | None = None
    best_cosine: float | None = None
    best_file: str | None = None
    unembedded: int | None = None
    rank: int | None = None
    ranked_count: int | None = None
    card: int | None = None
    cards: int | None = None
    pick_file: str | None = None
    pick_is_match: bool | None = None
    same_design: bool | None = None
    stage: str | None = None
    reason: str | None = None


def outcome_of(found: Match, candidates: Sequence[Candidate]) -> Outcome:
    """What a completed masked run did with the owner's poster."""
    pick = next((c for c in candidates if c.is_pick), None)
    cards = cards_of(candidates)
    base = Outcome(
        outcome=OUTCOME_NOT_OFFERED,
        best_cosine=found.best_cosine,
        best_file=found.best_file,
        unembedded=found.unembedded,
        ranked_count=sum(1 for c in candidates if c.rank is not None),
        cards=len(cards),
        pick_file=pick.source_file if pick else None,
    )
    chosen = found.candidate
    if chosen is None:
        return base
    base.matched_by = found.matched_by
    base.match_file = chosen.source_file
    base.match_sha256 = chosen.sha256
    base.match_cosine = found.cosine
    if chosen.rank is None:
        base.outcome = OUTCOME_GATED
        base.stage = chosen.stage_reached
        base.reason = chosen.rejection_reason or chosen.gate_decision
        return base
    card_of = {c.source_file: index for index, card in enumerate(cards, 1) for c in card}
    base.outcome = OUTCOME_RANKED
    base.rank = chosen.rank
    base.card = card_of[chosen.source_file]
    base.pick_is_match = pick is not None and pick.source_file == chosen.source_file
    base.same_design = pick is not None and card_of.get(pick.source_file) == base.card
    return base


# ── The summary ───────────────────────────────────────────────────────────


@dataclass
class Tally:
    evaluated: int = 0
    ranked: int = 0
    gated: int = 0
    not_offered: int = 0
    failed: int = 0
    gated_by: Counter[str] = field(default_factory=Counter)
    exact: int = 0
    same_design: int = 0
    top3: int = 0
    top5: int = 0
    cards: list[int] = field(default_factory=list)

    def add(self, row: EvaluationResult) -> None:
        self.evaluated += 1
        if row.outcome == OUTCOME_RANKED:
            self.ranked += 1
            self.exact += bool(row.pick_is_match)
            self.same_design += bool(row.same_design)
            if row.card is not None:
                self.cards.append(row.card)
                self.top3 += row.card <= 3
                self.top5 += row.card <= 5
        elif row.outcome == OUTCOME_GATED:
            self.gated += 1
            self.gated_by[f"{row.stage or '?'}: {row.reason or '?'}"] += 1
        elif row.outcome == OUTCOME_NOT_OFFERED:
            self.not_offered += 1
        else:
            self.failed += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated": self.evaluated,
            "ranked": self.ranked,
            "gated": self.gated,
            "gated_by": dict(sorted(self.gated_by.items(), key=lambda kv: (-kv[1], kv[0]))),
            "not_offered": self.not_offered,
            "failed": self.failed,
            "exact": self.exact,
            "same_design": self.same_design,
            "top3": self.top3,
            "top5": self.top5,
            "median_card": statistics.median(self.cards) if self.cards else None,
            "mean_reciprocal_card": (
                round(sum(1 / card for card in self.cards) / len(self.cards), 4)
                if self.cards
                else None
            ),
        }


def summarize(rows: Iterable[EvaluationResult]) -> dict[str, dict[str, Any]]:
    """Seed exemplars, feedback exemplars, and both; the headline is ``seed``."""
    tallies = {"seed": Tally(), "feedback": Tally(), "all": Tally()}
    for row in rows:
        tallies["all"].add(row)
        if row.exemplar_source in tallies:
            tallies[row.exemplar_source].add(row)
    return {name: tally.to_dict() for name, tally in tallies.items()}


# ── Recording a result ────────────────────────────────────────────────────


def _profile_rows(sha256: str) -> dict[str, np.ndarray]:
    """The profile's positives by staged name. Blocking."""
    from marquee.ml.artifact_codec import decode_unicode_list

    path = rebuild.profile_copy(f"{sha256}{rebuild.PROFILE_SUFFIX}")
    with np.load(path, allow_pickle=False) as data:
        names = decode_unicode_list(data["poster_names"])
        vectors = np.asarray(data["embeddings"], dtype=np.float32)
    return dict(zip(names, vectors, strict=True))


def cached_embedding(sha256: str) -> np.ndarray | None:
    """A candidate's CLIP embedding from the engine's cache, or ``None``. Blocking.

    The cache is the engine's (``pipeline/features.py::embedding_cache_path``): one file per
    model and file hash, which records both. It is read here rather than imported, so the
    worker does not load the engine to record a result.
    """
    path = (
        Path(pipeline_settings.EMBEDDING_CACHE_DIR) / pipeline_settings.AI_MODEL / f"{sha256}.npz"
    )
    if not path.is_file():
        return None
    try:
        with np.load(path, allow_pickle=False) as cached:
            if (
                str(np.asarray(cached["model_name"]).item()) == pipeline_settings.AI_MODEL
                and str(np.asarray(cached["sha256"]).item()) == sha256
            ):
                return np.asarray(cached["embedding"], dtype=np.float32)
    except (OSError, ValueError, KeyError):
        logger.warning("EVAL | discarding an unreadable embedding cache entry %s", path.name)
    return None


def _duplicates(folder: Path) -> dict[str, str]:
    """Candidate removed as a duplicate -> the one kept, from the run archive. Blocking."""
    try:
        payload = json.loads((folder / runs.RUN_ARCHIVE).read_text(encoding="utf-8"))
        rows = payload["diagnostic_ledger"]["candidates"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return {
        str(row["orig_filename"]): str(row["dedup_kept"])
        for row in rows
        if isinstance(row, dict) and row.get("dedup_kept")
    }


def _gather(
    run: PosterRun, exemplar: Exemplar, candidates: Sequence[Candidate]
) -> tuple[np.ndarray | None, dict[str, np.ndarray], dict[str, str]]:
    """The exemplar's embedding, the candidates' cached embeddings, and the duplicates.
    Blocking."""
    vector = None
    if run.profile_sha256 and exemplar.staged_name:
        try:
            vector = _profile_rows(run.profile_sha256).get(exemplar.staged_name)
        except (rebuild.ProfileError, OSError, KeyError, ValueError) as exc:
            logger.warning("EVAL | run %d: the profile cannot be read: %s", run.id, exc)
    embeddings = {
        c.sha256: found
        for c in candidates
        if c.sha256 is not None and (found := cached_embedding(c.sha256)) is not None
    }
    try:
        duplicates = _duplicates(runs.run_dir(run.run_folder))
    except runs.RunRecordError:
        duplicates = {}
    return vector, embeddings, duplicates


async def _active_positive(session: AsyncSession, movie_id: int) -> Exemplar | None:
    return await session.scalar(
        select(Exemplar).where(
            Exemplar.movie_id == movie_id,
            Exemplar.state == STATE_ACTIVE,
            Exemplar.polarity == POLARITY_POSITIVE,
        )
    )


async def _store(
    session: AsyncSession,
    evaluation_id: int,
    movie_id: int,
    exemplar: Exemplar | None,
    run_id: int | None,
    outcome: Outcome,
) -> EvaluationResult:
    """Insert or replace the movie's result in the evaluation, and commit."""
    row = await session.scalar(
        select(EvaluationResult)
        .where(
            EvaluationResult.evaluation_id == evaluation_id,
            EvaluationResult.movie_id == movie_id,
        )
        .with_for_update(),
        execution_options={"populate_existing": True},
    )
    if row is None:
        row = EvaluationResult(evaluation_id=evaluation_id, movie_id=movie_id)
        session.add(row)
    row.exemplar_id = exemplar.id if exemplar is not None else None
    row.exemplar_source = exemplar.source if exemplar is not None else None
    row.run_id = run_id
    for name, value in vars(outcome).items():
        setattr(row, name, value)
    await session.commit()
    return row


async def record_run(
    session: AsyncSession, evaluation_id: int, movie_id: int, run_id: int
) -> EvaluationResult:
    """Record where the owner's poster landed in this masked run, and commit."""
    run = await session.get(PosterRun, run_id)
    if run is None or not run.masked or run.movie_id != movie_id:
        raise EvaluationError(f"run {run_id} is not a masked run of movie {movie_id}")
    row = await session.get(Evaluation, evaluation_id)
    if row is None:
        raise EvaluationError(f"evaluation {evaluation_id} does not exist")
    exemplar = await _active_positive(session, movie_id)
    if run.state != RUN_COMPLETED:
        found = Outcome(outcome=OUTCOME_FAILED, reason=run.error or f"the run is {run.state}")
        return await _store(session, evaluation_id, movie_id, exemplar, run_id, found)
    if run.profile_sha256 != row.profile_sha256:
        # A rebuild published while the evaluation ran (a review answered meanwhile queues
        # one): this run measured another profile than the evaluation names.
        found = Outcome(outcome=OUTCOME_FAILED, reason=PROFILE_CHANGED)
        return await _store(session, evaluation_id, movie_id, exemplar, run_id, found)
    if exemplar is None:
        found = Outcome(outcome=OUTCOME_FAILED, reason="the movie has no active positive exemplar")
        return await _store(session, evaluation_id, movie_id, exemplar, run_id, found)
    candidates = [Candidate.of(row) for row in await runs.candidates_of(session, run_id)]
    vector, embeddings, duplicates = await asyncio.to_thread(_gather, run, exemplar, candidates)
    found = match(
        vector,
        candidates,
        embeddings,
        source_file=exemplar.source_file,
        duplicates=duplicates,
        threshold=settings.EVAL_MATCH_MIN_COSINE,
    )
    if vector is None and found.matched_by != MATCHED_BY_FILE:
        result = Outcome(
            outcome=OUTCOME_FAILED,
            reason="the exemplar is not in the profile the run was masked from",
        )
    else:
        result = outcome_of(found, candidates)
    stored = await _store(session, evaluation_id, movie_id, exemplar, run_id, result)
    logger.info("EVAL | evaluation %d, movie %d: %s", evaluation_id, movie_id, result_line(stored))
    return stored


async def record_failure(
    session: AsyncSession,
    evaluation_id: int,
    movie_id: int,
    *,
    run_id: int | None,
    reason: str,
) -> EvaluationResult:
    """Record that the movie's masked run did not complete, and commit."""
    exemplar = await _active_positive(session, movie_id)
    return await _store(
        session,
        evaluation_id,
        movie_id,
        exemplar,
        run_id,
        Outcome(outcome=OUTCOME_FAILED, reason=reason[:500]),
    )


def result_line(row: EvaluationResult) -> dict[str, Any]:
    """A result in a few fields, for a job's result and a log line."""
    return {
        "outcome": row.outcome,
        "rank": row.rank,
        "ranked_count": row.ranked_count,
        "card": row.card,
        "cards": row.cards,
        "match_file": row.match_file,
        "match_cosine": row.match_cosine,
        "best_cosine": row.best_cosine,
        "stage": row.stage,
        "reason": row.reason,
    }


# ── Queueing and reading ──────────────────────────────────────────────────


async def evaluable_movie_ids(
    session: AsyncSession, movie_ids: Sequence[int] | None = None, *, limit: int | None = None
) -> list[int]:
    """Every movie a run accepts that has an active positive exemplar, by title; or those of
    ``movie_ids`` that are."""
    has_positive = (
        select(Exemplar.id)
        .where(
            Exemplar.movie_id == Movie.id,
            Exemplar.state == STATE_ACTIVE,
            Exemplar.polarity == POLARITY_POSITIVE,
        )
        .exists()
    )
    statement = select(Movie.id).where(runs.eligible(), has_positive)
    if movie_ids is not None:
        statement = statement.where(Movie.id.in_(list(movie_ids)))
    statement = statement.order_by(Movie.sort_title, Movie.id)
    if limit is not None:
        statement = statement.limit(limit)
    return list(await session.scalars(statement))


async def running(session: AsyncSession) -> Evaluation | None:
    """The evaluation whose batch is still running, if any."""
    return await session.scalar(
        select(Evaluation)
        .join(Job, Job.id == Evaluation.job_id)
        .where(Job.state == "running")
        .order_by(Evaluation.id.desc())
        .limit(1)
    )


@dataclass(frozen=True)
class EvaluationView:
    """An evaluation with its batch's state and its summary."""

    evaluation: Evaluation
    state: str
    finished_at: Any
    results: int
    not_run: int
    summary: dict[str, dict[str, Any]]
    progress: dict[str, int]


async def view(session: AsyncSession, row: Evaluation) -> EvaluationView:
    job = await session.get(Job, row.job_id) if row.job_id is not None else None
    results = list(
        await session.scalars(
            select(EvaluationResult).where(EvaluationResult.evaluation_id == row.id)
        )
    )
    progress: dict[str, int] = {}
    if job is not None:
        counts = await session.execute(
            select(Job.state, func.count()).where(Job.parent_id == job.id).group_by(Job.state)
        )
        progress = {state: int(count) for state, count in counts}
    state = job.state if job is not None else "unknown"
    return EvaluationView(
        evaluation=row,
        state=state,
        finished_at=job.finished_at if job is not None else None,
        results=len(results),
        not_run=max(0, row.queued - len(results)) if state != "running" else 0,
        summary=summarize(results),
        progress=progress,
    )


async def latest_succeeded(
    session: AsyncSession, *, before: int | None = None
) -> Evaluation | None:
    """The newest evaluation whose batch succeeded (or the newest before ``before``). A
    cancelled or failed evaluation is never a headline; it stays in the list and on its own
    page."""
    statement = (
        select(Evaluation)
        .join(Job, Job.id == Evaluation.job_id)
        .where(Job.state == "succeeded")
        .order_by(Evaluation.id.desc())
        .limit(1)
    )
    if before is not None:
        statement = statement.where(Evaluation.id < before)
    return await session.scalar(statement)


@dataclass(frozen=True)
class ResultRow:
    result: EvaluationResult
    title: str
    year: int | None
    run_exists: bool
    exemplar_image: bool
    match_candidate_id: int | None
    pick_candidate_id: int | None


async def results_page(
    session: AsyncSession,
    evaluation_id: int,
    *,
    outcome: str | None = None,
    source: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[int, list[ResultRow]]:
    """One page of an evaluation's results, by title, with what the API links to."""
    conditions = [EvaluationResult.evaluation_id == evaluation_id]
    if outcome is not None:
        conditions.append(EvaluationResult.outcome == outcome)
    if source is not None:
        conditions.append(EvaluationResult.exemplar_source == source)
    total = await session.scalar(
        select(func.count()).select_from(EvaluationResult).where(*conditions)
    )
    rows = (
        await session.execute(
            select(EvaluationResult, Movie.title, Movie.year, PosterRun.id)
            .join(Movie, Movie.id == EvaluationResult.movie_id)
            .outerjoin(PosterRun, PosterRun.id == EvaluationResult.run_id)
            .where(*conditions)
            .order_by(Movie.sort_title, Movie.id)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).all()
    run_ids = [run_id for *_, run_id in rows if run_id is not None]
    downloaded: dict[tuple[int, str], int] = {}
    if run_ids:
        found = await session.execute(
            select(RunCandidate.run_id, RunCandidate.source_file, RunCandidate.id).where(
                RunCandidate.run_id.in_(run_ids), RunCandidate.sha256.is_not(None)
            )
        )
        downloaded = {(r.run_id, r.source_file): r.id for r in found}
    out = []
    for result, title, year, run_id in rows:
        out.append(
            ResultRow(
                result=result,
                title=title,
                year=year,
                run_exists=run_id is not None,
                exemplar_image=result.exemplar_id is not None,
                match_candidate_id=(
                    downloaded.get((run_id, result.match_file))
                    if run_id and result.match_file
                    else None
                ),
                pick_candidate_id=(
                    downloaded.get((run_id, result.pick_file))
                    if run_id and result.pick_file
                    else None
                ),
            )
        )
    return total or 0, out
