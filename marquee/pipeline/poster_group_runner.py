"""Workspace-confined, stage-major poster analysis for bounded subject groups.

The durable job handler owns database state, fencing, artifact registration, and
projection.  This module only analyzes posters inside one attempt workspace.  It
keeps model sessions resident while every member crosses the same stage, and
returns one independent result per subject in the original request order.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from marquee.config import settings
from marquee.core.jobs.poster_group_limits import MAX_POSTER_GROUP_MEMBERS
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.poster_sources.tmdb import TMDBClient
from marquee.core.text_profiles import OcrGateContext
from marquee.models import Movie
from marquee.pipeline.deduper import PosterDeduper
from marquee.pipeline.features import FeatureExtractor
from marquee.pipeline.gate import PosterGate
from marquee.pipeline.ocr_filter import OcrPool, PosterTextFilter, apply_no_text_fallback
from marquee.pipeline.orchestrator import PosterSubjectInput, _preloaded_ocr_pool
from marquee.pipeline.output import place_gated
from marquee.pipeline.runner import (
    NEUTRAL_REVIEW_ORDER,
    SCORED_REVIEW_ORDER,
    FetchOutcome,
    ProgressEvent,
    _attach_ocr_diagnostics,
    _copy_with_reason,
    _log_dedup_removal,
    _neutral_candidate_order,
    _stack_signal_value,
    build_run_payload,
    fetch_and_download,
    place_outputs,
    write_run_json,
)
from marquee.pipeline.scorer import (
    ResidualCompatibilityError,
    ResidualRuntimeContext,
    WeightedScorer,
    select_scorer,
)
from marquee.pipeline.stacker import assign_stacks
from marquee.pipeline.types import CandidateScore, OCRCandidateResult

logger = logging.getLogger(__name__)

_MAX_MEMBERS = MAX_POSTER_GROUP_MEMBERS
_MAX_REVIEW_FILES = 100
_COLLECTING_MESSAGE = (
    "Marquee filtered unusable posters, but has not learned your preferences yet."
)


@dataclass(frozen=True, slots=True)
class PosterGroupMemberInput:
    """One server-resolved subject entering a contained group run."""

    subject_key: str
    subject: PosterSubjectInput
    ocr_gate: OcrGateContext
    run_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True, slots=True)
class GroupProgressEvent:
    stage: str
    state: str
    scope: str | None = None
    subject: str | None = None
    done: int | None = None
    total: int | None = None
    survivors: int | None = None
    # Union stages measure one pooled workload, so ``done``/``total`` belong to
    # the group and cannot be split per subject. These three carry the one
    # member that advanced with this sample, so the roster can show each subject
    # its own numbers without the group card losing its aggregate.
    member_scope: str | None = None
    member_done: int | None = None
    member_total: int | None = None


GroupProgress = Callable[[GroupProgressEvent], None]


class _SystemicModelError(RuntimeError):
    """A shared model/session failure that invalidates the whole group."""


@dataclass(slots=True)
class PosterGroupMemberOutput:
    subject_key: str
    run_id: str
    status: str
    counts: dict[str, int]
    source_count: int
    candidate_count: int
    recommendation: dict[str, object] | None
    scorer_name: str | None
    personalization_mode: str
    payload: dict[str, object] | None
    member_index: int
    title: str
    timings: dict[str, float] = field(default_factory=dict)
    duration_seconds: float | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    candidate_files: dict[str, str] = field(default_factory=dict)
    # Rejected candidates kept only so the review UI's per-stage tabs can show
    # them. Best-effort: a member is never failed over a missing one.
    rejected_files: dict[str, str] = field(default_factory=dict)
    archive_file: str | None = None

    @property
    def outcome(self) -> str:
        if self.status == "failed":
            return "failed"
        if self.status == "no_candidates":
            return "no_change"
        if self.personalization_mode == "collecting":
            return "review_required" if self.counts.get("ranked", 0) else "no_change"
        if self.recommendation is not None:
            return "succeeded"
        return "review_required" if self.candidate_count else "no_change"

    def result_document(self) -> dict[str, object]:
        return {
            "member_index": self.member_index,
            "subject_key": self.subject_key,
            "run_id": self.run_id,
            "title": self.title[:500],
            "status": self.status,
            "outcome": self.outcome,
            "counts": self.counts,
            "timings": self.timings,
            "duration_seconds": self.duration_seconds,
            "candidate_count": self.candidate_count,
            "source_count": self.source_count,
            "recommendation": self.recommendation,
            "scorer_name": self.scorer_name,
            "personalization_mode": self.personalization_mode,
            "candidate_files": self.candidate_files,
            "rejected_files": self.rejected_files,
            "archive_file": self.archive_file,
            "error": str(self.error)[:2_000] if self.error is not None else None,
            "warnings": [str(warning)[:300] for warning in self.warnings[:20]],
        }


@dataclass(slots=True)
class PosterGroupOutput:
    library: str
    chunk_index: int
    members: list[PosterGroupMemberOutput]

    @property
    def outcome(self) -> str:
        outcomes = [member.outcome for member in self.members]
        if any(value in {"failed", "review_required"} for value in outcomes):
            return "review_required"
        if outcomes and all(value == "no_change" for value in outcomes):
            return "no_change"
        return "succeeded"

    def result_document(self) -> dict[str, object]:
        counts = _outcome_counts(self.members)
        return {
            "version": 1,
            "library": self.library,
            "chunk_index": self.chunk_index,
            "outcome": self.outcome,
            "member_count": len(self.members),
            **counts,
            "run_ids": [member.run_id for member in self.members],
            "failed_subject_keys": [
                member.subject_key for member in self.members if member.outcome == "failed"
            ],
            "members": [member.result_document() for member in self.members],
        }


@dataclass(slots=True)
class _MemberState:
    member: PosterGroupMemberInput
    index: int
    out_dir: Path
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    start_perf: float = field(default_factory=time.perf_counter)
    fetch: FetchOutcome | None = None
    timings: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    style_survivors: list[Path] = field(default_factory=list)
    ocr_survivors: list[OCRCandidateResult] = field(default_factory=list)
    passed: list[CandidateScore] = field(default_factory=list)
    gated: list[CandidateScore] = field(default_factory=list)
    ranked: list[CandidateScore] = field(default_factory=list)
    status: str = "running"
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def subject(self) -> PosterSubjectInput:
        return self.member.subject

    @property
    def scope(self) -> str:
        return _member_scope(self.index)

    @property
    def records(self) -> dict[str, CandidateScore]:
        return self.fetch.records if self.fetch is not None else {}


def _outcome_counts(members: list[PosterGroupMemberOutput]) -> dict[str, int]:
    values = [member.outcome for member in members]
    return {
        "succeeded_count": values.count("succeeded"),
        "no_change_count": values.count("no_change"),
        "review_required_count": values.count("review_required"),
        "failed_count": values.count("failed"),
    }


def _emit(
    progress: GroupProgress | None,
    ctx: _MemberState,
    stage: str,
    state: str,
    *,
    done: int | None = None,
    total: int | None = None,
    survivors: int | None = None,
) -> None:
    if progress is not None:
        progress(
            GroupProgressEvent(
                stage=stage,
                state=state,
                scope=ctx.scope,
                subject=ctx.subject.title,
                done=done,
                total=total,
                survivors=survivors,
            )
        )


def _emit_union(
    progress: GroupProgress | None,
    stage: str,
    state: str,
    *,
    done: int | None = None,
    total: int | None = None,
    survivors: int | None = None,
    member: tuple[str, int, int] | None = None,
) -> None:
    if progress is not None:
        member_scope, member_done, member_total = member or (None, None, None)
        progress(
            GroupProgressEvent(
                stage=stage,
                state=state,
                done=done,
                total=total,
                survivors=survivors,
                member_scope=member_scope,
                member_done=member_done,
                member_total=member_total,
            )
        )


def _member_scope(index: int) -> str:
    return f"m{index:02d}"


def _mark_failed(ctx: _MemberState, exc: BaseException | str) -> None:
    ctx.status = "failed"
    ctx.error = str(exc)[:2000]
    logger.warning("POSTER GROUP | member=%s failed: %s", ctx.member.subject_key, exc)


def _movie_of(subject: PosterSubjectInput) -> Movie:
    return Movie(id=subject.movie_id, title=subject.title, tmdb_id=subject.tmdb_id)


def _member_progress(ctx: _MemberState, progress: GroupProgress | None):
    def forward(event: ProgressEvent) -> None:
        _emit(
            progress,
            ctx,
            event.stage,
            event.state,
            done=event.done,
            total=event.total,
            survivors=event.survivors,
        )

    return forward


async def _fetch_all(contexts: list[_MemberState], progress: GroupProgress | None) -> None:
    if not settings.TMDB_READ_ACCESS_TOKEN:
        raise RuntimeError(
            "TMDB_READ_ACCESS_TOKEN is not configured; the poster pipeline cannot fetch candidates"
        )

    async with TMDBClient(read_access_token=settings.TMDB_READ_ACCESS_TOKEN) as tmdb:

        async def fetch_one(ctx: _MemberState) -> None:
            if ctx.subject.tmdb_id is None:
                raise RuntimeError("subject has no TMDB ID; synchronize the library first")
            ctx.out_dir.mkdir()
            started = time.perf_counter()
            ctx.fetch = await fetch_and_download(
                tmdb=tmdb,
                movie=_movie_of(ctx.subject),
                originals_dir=ctx.out_dir,
                timings=ctx.timings,
                progress=_member_progress(ctx, progress),
                media_type=ctx.subject.media_type,
                season_number=ctx.subject.season_number,
            )
            ctx.timings.setdefault("fetch", time.perf_counter() - started)
            ctx.counts.update(ctx.fetch.counts)
            if not ctx.fetch.all_files and ctx.fetch.counts.get("errors", 0):
                raise RuntimeError("every candidate download failed")

        results = await asyncio.gather(*(fetch_one(ctx) for ctx in contexts), return_exceptions=True)
    for ctx, result in zip(contexts, results, strict=True):
        if isinstance(result, BaseException):
            _mark_failed(ctx, result)


def _prelude(ctx: _MemberState, extractor: FeatureExtractor, progress: GroupProgress | None) -> None:
    if ctx.status == "failed" or ctx.fetch is None:
        return
    records = ctx.records
    fetch = ctx.fetch
    gate = PosterGate()
    errored_dir = ctx.out_dir / "errored"
    errored_dir.mkdir(exist_ok=True)

    started = time.perf_counter()
    _emit(progress, ctx, "sha256", "start", total=len(fetch.all_files))
    sha_dir = ctx.out_dir / "1-sha256-rejected"
    sha_dir.mkdir()
    sha = PosterDeduper(
        sha256_only=True,
        resolution_by_name=fetch.resolution_by_name,
    ).deduplicate(fetch.all_files)
    for removal in sha.removals:
        _log_dedup_removal(removal)
        record = records[removal.removed.name]
        record.stage_reached = "dedup"
        record.rejection_reason = f"dedup_{removal.reason}"
        record.dedup_kept = removal.kept.name if removal.kept else None
        record.image_path = _copy_with_reason(removal.removed, sha_dir, removal.reason)
    for path in sha.survivors:
        records[path.name].stage_reached = "dedup"
        records[path.name].image_path = path
    ctx.counts["sha256_survivors"] = sha.final
    ctx.timings["sha256"] = time.perf_counter() - started
    _emit(progress, ctx, "sha256", "end", survivors=sha.final)

    resolution_survivors: list[Path] = []
    _emit(progress, ctx, "gate-resolution", "start", total=sha.final)
    for path in sorted(sha.survivors):
        record = records[path.name]
        decision = gate.evaluate_metadata(original_width=fetch.candidate_map[path.name].width)
        if decision.passed:
            resolution_survivors.append(path)
        else:
            record.stage_reached = "gate"
            record.gate_decision = "gated"
            record.gate_reason = decision.reason
            record.rejection_reason = decision.reason
            ctx.gated.append(record)
    ctx.counts["resolution_gated"] = len(ctx.gated)
    _emit(progress, ctx, "gate-resolution", "end", survivors=len(resolution_survivors))

    started = time.perf_counter()
    _emit(progress, ctx, "style-features", "start", total=len(resolution_survivors))
    style_items = [(path, fetch.candidate_map[path.name]) for path in resolution_survivors]
    try:
        results = extractor.extract_style_batch(style_items, primary_name=fetch.primary_name)
    except Exception as exc:
        raise _SystemicModelError(f"shared style inference failed: {exc}") from exc
    styled: list[Path] = []
    for (path, _candidate), result in zip(style_items, results, strict=True):
        record = records[path.name]
        record.stage_reached = "features"
        if isinstance(result, Exception):
            record.rejection_reason = f"feature_error: {result}"
            record.image_path = _copy_with_reason(path, errored_dir, "feature_error")
            continue
        record.features = result
        styled.append(path)
    ctx.timings["style-features"] = time.perf_counter() - started
    _emit(progress, ctx, "style-features", "end", survivors=len(styled))

    style_survivors: list[Path] = []
    style_gated = 0
    _emit(progress, ctx, "gate-style", "start", total=len(styled))
    for path in styled:
        record = records[path.name]
        assert record.features is not None
        decision = gate.evaluate_style(
            record.features,
            personalization_mode=extractor.personalization_mode,
        )
        if decision.passed:
            style_survivors.append(path)
        else:
            record.stage_reached = "gate"
            record.gate_decision = "gated"
            record.gate_reason = decision.reason
            record.rejection_reason = decision.reason
            ctx.gated.append(record)
            style_gated += 1
    ctx.counts["style_gated"] = style_gated
    ctx.style_survivors = style_survivors
    _emit(progress, ctx, "gate-style", "end", survivors=len(style_survivors))


def _ocr_union(
    contexts: list[_MemberState],
    progress: GroupProgress | None,
    *,
    pool: OcrPool | None = None,
) -> None:
    items: list[tuple[Any, ...]] = []
    owners: list[int] = []
    active = [ctx for ctx in contexts if ctx.status != "failed" and ctx.fetch is not None]
    for ctx in active:
        (ctx.out_dir / "2-ocr-rejected").mkdir()
        tokens = PosterTextFilter(
            ctx.subject.ocr_title or ctx.subject.title,
            director=ctx.member.ocr_gate.director,
            studios=ctx.member.ocr_gate.studios,
            tagline=ctx.member.ocr_gate.tagline,
            profile=ctx.member.ocr_gate.profile,
        )
        extras = tokens.task_extras()
        for path in ctx.style_survivors:
            items.append((path, tokens.title, tokens.title_tokens, tokens.director_tokens, extras))
            owners.append(ctx.index)

    _emit_union(progress, "ocr", "start", total=len(items))
    if not items:
        for ctx in active:
            ctx.counts["ocr_survivors"] = 0
        _emit_union(progress, "ocr", "end", survivors=0)
        return

    member_totals = Counter(owners)
    # Give every subject its denominator before the first image lands, so the
    # roster bars are scaled from the outset instead of inheriting the previous
    # stage's numbers. No ``done`` here keeps these honestly indeterminate for
    # the job-level bar, which the union samples below own.
    for ctx in active:
        _emit(progress, ctx, "ocr", "start", total=member_totals.get(ctx.index, 0) or None)

    started = time.perf_counter()
    member_done: Counter[int] = Counter()
    # ``on_item`` runs immediately before ``tick`` for the same image, so this
    # holds the one subject that the next union sample should also report.
    advanced: list[int] = []

    def on_item(index: int) -> None:
        owner = owners[index]
        member_done[owner] += 1
        advanced[:] = [owner]

    def tick(done: int, total: int) -> None:
        member = (
            (_member_scope(advanced[0]), member_done[advanced[0]], member_totals[advanced[0]])
            if advanced
            else None
        )
        _emit_union(progress, "ocr", "progress", done=done, total=total, member=member)

    # This is the sole OCR pool/pass for the group. Initialization or worker
    # failure intentionally escapes as a systemic operation failure.
    results = (
        PosterTextFilter.run_ocr_tasks(pool, items, progress=tick, on_item=on_item)
        if pool is not None
        else PosterTextFilter.run_ocr_batch(items, progress=tick, on_item=on_item)
    )
    elapsed = time.perf_counter() - started
    by_owner: dict[int, list[OCRCandidateResult]] = defaultdict(list)
    for owner, result in zip(owners, results, strict=True):
        by_owner[owner].append(result)

    survivors_total = 0
    for ctx in active:
        ctx.timings["ocr"] = elapsed
        try:
            member_results = apply_no_text_fallback(by_owner.get(ctx.index, []))
            errored_dir = ctx.out_dir / "errored"
            rejected_dir = ctx.out_dir / "2-ocr-rejected"
            for result in member_results:
                record = ctx.records[result.image_path.name]
                record.stage_reached = "ocr"
                _attach_ocr_diagnostics(record, result)
                if not result.accepted:
                    reason = result.reason or "ocr_rejected"
                    record.rejection_reason = reason
                    destination = errored_dir if reason.startswith("ocr_error") else rejected_dir
                    record.image_path = _copy_with_reason(
                        result.image_path,
                        destination,
                        reason.split(":", 1)[0],
                    )
                    continue
                record.image_path = result.image_path
                ctx.ocr_survivors.append(replace(result, image_path=result.image_path))
            ctx.counts["ocr_survivors"] = len(ctx.ocr_survivors)
            survivors_total += len(ctx.ocr_survivors)
            _emit(progress, ctx, "ocr", "end", survivors=len(ctx.ocr_survivors))
        except Exception as exc:
            _mark_failed(ctx, exc)
    _emit_union(progress, "ocr", "end", survivors=survivors_total)


def _phash(
    ctx: _MemberState,
    personalization_mode: str,
    progress: GroupProgress | None,
) -> None:
    if ctx.status == "failed" or ctx.fetch is None:
        return
    _emit(progress, ctx, "phash", "start", total=len(ctx.ocr_survivors))
    if pipeline_settings.STACK_ENABLED and personalization_mode == "personalized":
        ctx.counts["phash_survivors"] = len(ctx.ocr_survivors)
        _emit(progress, ctx, "phash", "end", survivors=len(ctx.ocr_survivors))
        return

    phash_dir = ctx.out_dir / "3-phash-rejected"
    phash_dir.mkdir()
    survivor_paths = [result.image_path for result in ctx.ocr_survivors]
    preference = {
        result.image_path.name: (
            1 if result.title_bbox is not None else 0,
            -len(result.residual_boxes),
            1 if result.image_path.name == ctx.fetch.primary_name else 0,
            (
                getattr(ctx.records[result.image_path.name].features, "knn_sim", 0.0)
                if personalization_mode == "personalized"
                else 0.0
            ),
        )
        for result in ctx.ocr_survivors
        if ctx.records[result.image_path.name].features is not None
    }
    started = time.perf_counter()
    deduped = PosterDeduper(
        min_width=0,
        resolution_by_name=ctx.fetch.resolution_by_name,
        preference_by_name=preference,
    ).deduplicate(survivor_paths)
    survivor_names = {path.name for path in deduped.survivors}
    for removal in deduped.removals:
        if removal.reason != "phash":
            continue
        _log_dedup_removal(removal)
        record = ctx.records[removal.removed.name]
        record.stage_reached = "phash"
        record.rejection_reason = "dedup_phash"
        record.dedup_kept = removal.kept.name if removal.kept else None
        record.image_path = _copy_with_reason(removal.removed, phash_dir, "phash")
    ctx.ocr_survivors = [
        result for result in ctx.ocr_survivors if result.image_path.name in survivor_names
    ]
    ctx.counts["phash_survivors"] = len(ctx.ocr_survivors)
    ctx.timings["phash"] = time.perf_counter() - started
    _emit(progress, ctx, "phash", "end", survivors=len(ctx.ocr_survivors))


def _detail_union(
    contexts: list[_MemberState],
    extractor: FeatureExtractor,
    *,
    residual_path: Path | None,
    residual_context: ResidualRuntimeContext | None,
    personalization_mode: str,
    progress: GroupProgress | None,
) -> Any:
    items: list[tuple[Any, OCRCandidateResult]] = []
    owners: list[tuple[_MemberState, OCRCandidateResult]] = []
    for ctx in contexts:
        if ctx.status == "failed":
            continue
        for result in ctx.ocr_survivors:
            items.append((ctx.records[result.image_path.name].features, result))
            owners.append((ctx, result))

    _emit_union(progress, "detail-features", "start", total=len(items))
    # ``complete_batch`` below is one blocking call with no sub-progress, so the
    # roster can only be told each subject's share at the stage boundaries.
    for ctx in contexts:
        if ctx.status != "failed" and ctx.ocr_survivors:
            _emit(progress, ctx, "detail-features", "start", total=len(ctx.ocr_survivors))
    if not items:
        for ctx in contexts:
            if ctx.status != "failed":
                ctx.counts["feature_survivors"] = 0
        _emit_union(progress, "detail-features", "end", survivors=0)
        return None

    scorer = (
        select_scorer(artifact_path=residual_path, context=residual_context)
        if personalization_mode == "personalized"
        else None
    )

    started = time.perf_counter()
    dino_vectors: dict[int, Any] = {}
    # One union call. ONNX/session failures escape; candidate read/CV failures
    # remain exception slots returned by FeatureExtractor.
    results = extractor.complete_batch(items, dino_vectors_out=dino_vectors)
    elapsed = time.perf_counter() - started
    gate = PosterGate()
    diagnostic_scorer = scorer
    attributed = list(zip(owners, results, strict=True))
    for index, ((ctx, ocr_result), detail) in enumerate(attributed):
        if ctx.status == "failed":
            continue
        try:
            record = ctx.records[ocr_result.image_path.name]
            record.stage_reached = "features"
            if isinstance(detail, Exception):
                record.rejection_reason = f"feature_error: {detail}"
                record.image_path = _copy_with_reason(
                    ocr_result.image_path,
                    ctx.out_dir / "errored",
                    "feature_error",
                )
                continue
            record.features = detail
            if diagnostic_scorer is not None:
                try:
                    _, record.contributions = diagnostic_scorer.score(record.features)
                except RuntimeError as exc:
                    if pipeline_settings.SCORER == "residual":
                        raise ResidualCompatibilityError(
                            f"forced residual cannot score this feature schema: {exc}"
                        ) from exc
                    diagnostic_scorer = WeightedScorer()
                    _, record.contributions = diagnostic_scorer.score(record.features)
            decision = gate.evaluate_detail(record.features)
            record.stage_reached = "gate"
            record.gate_decision = "passed" if decision.passed else "gated"
            record.gate_reason = decision.reason
            if decision.passed:
                if pipeline_settings.STACK_ENABLED and personalization_mode == "personalized":
                    record.embedding = _stack_signal_value(
                        record,
                        ocr_result.image_path,
                        dino_vectors.get(index),
                        embedding_loader=extractor.load_run_embedding,
                    )
                ctx.passed.append(record)
            else:
                record.rejection_reason = decision.reason
                ctx.gated.append(record)
        except ResidualCompatibilityError:
            raise
        except Exception as exc:
            _mark_failed(ctx, exc)

    survivors = 0
    for ctx in contexts:
        if ctx.status == "failed":
            continue
        ctx.timings["detail-features"] = elapsed
        ctx.counts["feature_survivors"] = len(ctx.passed)
        survivors += len(ctx.passed)
        _emit(progress, ctx, "detail-features", "end", survivors=len(ctx.passed))
    _emit_union(progress, "detail-features", "end", survivors=survivors)
    return diagnostic_scorer


async def _rank_and_output(
    ctx: _MemberState,
    scorer: Any,
    *,
    personalization_mode: str,
    progress: GroupProgress | None,
) -> None:
    if ctx.status == "failed" or ctx.fetch is None:
        return
    try:
        place_gated(ctx.gated, ctx.out_dir / "gated")
        ctx.counts["gated"] = len(ctx.gated)
        if not ctx.passed:
            ctx.status = "no_candidates" if not ctx.fetch.candidate_map else "flagged_manual"
            ctx.counts["ranked"] = 0
            return

        stage = "neutral-order" if personalization_mode == "collecting" else "rank"
        _emit(progress, ctx, stage, "start", total=len(ctx.passed))
        started = time.perf_counter()
        if personalization_mode == "collecting":
            ctx.ranked = _neutral_candidate_order(
                ctx.passed,
                movie_title=ctx.subject.title,
                candidate_map=ctx.fetch.candidate_map,
            )
        else:
            if scorer is None:
                raise RuntimeError("personalized group has no scorer")
            ctx.ranked = scorer.rank(ctx.passed)
            if pipeline_settings.STACK_ENABLED:
                assign_stacks(ctx.ranked)
        ctx.timings[stage] = time.perf_counter() - started
        ctx.counts["ranked"] = len(ctx.ranked)
        _emit(progress, ctx, stage, "end", survivors=len(ctx.ranked))

        if personalization_mode == "personalized":
            await place_outputs(
                ctx.ranked,
                candidate_map=ctx.fetch.candidate_map,
                out_dir=ctx.out_dir,
                timings=ctx.timings,
                progress=_member_progress(ctx, progress),
            )
        ctx.status = "completed"
    except Exception as exc:  # member-attributable rank/output failure
        _mark_failed(ctx, exc)


def _subject_document(subject: PosterSubjectInput) -> dict[str, object]:
    return {
        "movie_id": subject.movie_id,
        "series_id": subject.series_id,
        "season_id": subject.season_id,
        "season_number": subject.season_number,
        "title": subject.title,
    }


def _build_member_output(
    ctx: _MemberState,
    *,
    scorer: Any,
    personalization_mode: str,
) -> PosterGroupMemberOutput:
    fetch = ctx.fetch
    records = ctx.records
    source_count = len(fetch.candidate_map) if fetch is not None else 0
    recommendation = None
    if personalization_mode == "personalized" and ctx.ranked and ctx.status != "failed":
        top = ctx.ranked[0]
        recommendation = {
            "orig_filename": top.orig_filename,
            "rank": top.rank,
            "final_score": top.final_score,
            "stack_label": top.stack_label,
        }
    payload: dict[str, object] | None = None
    duration_seconds = time.perf_counter() - ctx.start_perf
    try:
        payload = build_run_payload(
            movie=_movie_of(ctx.subject),
            started_at=ctx.started_at,
            status=ctx.status,
            timings=ctx.timings,
            records=records,
            total_duration=duration_seconds,
            run_id=ctx.member.run_id,
            error=ctx.error,
            media_type=ctx.subject.media_type,
            subject=_subject_document(ctx.subject),
            review_survivors=ctx.ranked,
            review_order_algorithm=(
                NEUTRAL_REVIEW_ORDER
                if personalization_mode == "collecting"
                else SCORED_REVIEW_ORDER
            ),
        )
        payload.update(
            personalization_mode=personalization_mode,
            recommendation=recommendation,
            scorer=None if personalization_mode == "collecting" else getattr(scorer, "name", None),
            message=_COLLECTING_MESSAGE if personalization_mode == "collecting" else None,
        )
    except Exception as exc:  # archive construction is member-local
        _mark_failed(ctx, exc)
    return PosterGroupMemberOutput(
        subject_key=ctx.member.subject_key,
        run_id=ctx.member.run_id,
        status=ctx.status,
        counts={key: int(value) for key, value in ctx.counts.items()},
        source_count=source_count,
        candidate_count=source_count,
        recommendation=recommendation if ctx.status != "failed" else None,
        scorer_name=(
            getattr(scorer, "name", None)
            if personalization_mode == "personalized" and ctx.ranked and ctx.status != "failed"
            else None
        ),
        personalization_mode=personalization_mode,
        payload=payload,
        member_index=ctx.index,
        title=ctx.subject.title,
        timings={key: float(value) for key, value in ctx.timings.items()},
        duration_seconds=round(duration_seconds, 3),
        error=ctx.error,
        warnings=ctx.warnings,
    )


async def run_poster_group(
    *,
    library: str,
    chunk_index: int,
    members: list[PosterGroupMemberInput],
    out_dir: Path,
    feature_extractor: FeatureExtractor,
    residual_path: Path | None = None,
    residual_context: ResidualRuntimeContext | None = None,
    personalization_mode: str = "personalized",
    progress: GroupProgress | None = None,
) -> PosterGroupOutput:
    """Analyze one bounded subject selection without opening a database session."""
    if library not in {"movies", "tv"}:
        raise ValueError("poster group library must be movies or tv")
    if not 1 <= len(members) <= _MAX_MEMBERS:
        raise ValueError(
            f"poster groups require between 1 and {MAX_POSTER_GROUP_MEMBERS} members"
        )
    if personalization_mode not in {"collecting", "personalized"}:
        raise ValueError("invalid poster group personalization mode")
    if len({member.subject_key for member in members}) != len(members):
        raise ValueError("poster group subject keys must be unique")
    if any(
        (member.subject.media_type == "movie") != (library == "movies") for member in members
    ):
        raise ValueError("poster group members do not match the declared library")
    if not settings.TMDB_READ_ACCESS_TOKEN:
        raise RuntimeError(
            "TMDB_READ_ACCESS_TOKEN is not configured; the poster pipeline cannot fetch candidates"
        )

    contexts = [
        _MemberState(member=member, index=index, out_dir=out_dir / f"s{index:03d}")
        for index, member in enumerate(members)
    ]
    # Warm one pool while network fetch/download and CLIP/style work run. The
    # shared lifecycle owns cancellation-safe resolution, inline fallback, and
    # certified teardown; the union feed below consumes the ready pool once.
    async with _preloaded_ocr_pool(enabled=True) as ocr_pool_ready:
        await _fetch_all(contexts, progress)

        # SHA, metadata/style gates, and CLIP remain per member because the primary
        # poster used for official-family similarity is subject-specific.
        for ctx in contexts:
            try:
                _prelude(ctx, feature_extractor, progress)
            except _SystemicModelError:
                raise
            except Exception as exc:
                _mark_failed(ctx, exc)

        _ocr_union(contexts, progress, pool=await ocr_pool_ready())
    for ctx in contexts:
        try:
            _phash(ctx, personalization_mode, progress)
        except Exception as exc:
            _mark_failed(ctx, exc)

    scorer = _detail_union(
        contexts,
        feature_extractor,
        residual_path=residual_path,
        residual_context=residual_context,
        personalization_mode=personalization_mode,
        progress=progress,
    )
    for ctx in contexts:
        await _rank_and_output(
            ctx,
            scorer,
            personalization_mode=personalization_mode,
            progress=progress,
        )

    outputs = [
        _build_member_output(ctx, scorer=scorer, personalization_mode=personalization_mode)
        for ctx in contexts
    ]
    return PosterGroupOutput(library=library, chunk_index=chunk_index, members=outputs)


def _materialize_member_evidence(
    member: PosterGroupMemberOutput,
    out_dir: Path,
    *,
    root: Path,
    diagnostic_paths: dict[Any, Any],
) -> list[str]:
    """Copy out the rejected candidates the review UI shows in its stage tabs.

    Best-effort by contract: an unusable entry is dropped rather than raised,
    because a missing rejection thumbnail must never fail a member whose actual
    result — the ranked survivors — materialized correctly.
    """
    payload = member.payload
    if payload is None:
        return []
    block = payload.get("review_evidence")
    entries = block.get("candidates") if isinstance(block, dict) else None
    if not isinstance(block, dict) or not isinstance(entries, list):
        return []
    keys: list[str] = []
    retained: list[dict[str, Any]] = []
    for entry in entries[:_MAX_REVIEW_FILES]:
        if not isinstance(entry, dict):
            continue
        reference = entry.get("reference")
        image_path = diagnostic_paths.get(reference) if isinstance(reference, str) else None
        if not isinstance(reference, str) or not isinstance(image_path, str) or not image_path:
            continue
        source = Path(image_path).resolve()
        if not source.is_file() or not source.is_relative_to(root):
            continue
        key = f"s{member.member_index:03d}-rejected-{len(retained):03d}.jpg"
        try:
            shutil.copyfile(source, out_dir / key)
        except OSError:
            continue
        entry["position"] = len(retained)
        entry["artifact_key"] = key
        member.rejected_files[reference] = key
        keys.append(key)
        retained.append(entry)
    block["candidates"] = retained
    block["archived_count"] = len(retained)
    block["truncated_count"] = max(0, len(entries) - len(retained))
    return keys


def materialize_group_output(output: PosterGroupOutput, out_dir: Path) -> list[str]:
    """Write flat, host-registrable files; archive failures stay member-local."""
    produced: list[str] = []
    root = out_dir.resolve()
    for member in output.members:
        if member.status == "failed" or member.payload is None:
            continue
        review = member.payload.get("review")
        ledger = member.payload.get("diagnostic_ledger")
        survivors = review.get("survivors") if isinstance(review, dict) else None
        diagnostics = ledger.get("candidates") if isinstance(ledger, dict) else None
        diagnostic_paths = {
            candidate.get("orig_filename"): candidate.get("image_path")
            for candidate in diagnostics or []
            if isinstance(candidate, dict)
            and isinstance(candidate.get("orig_filename"), str)
            and isinstance(candidate.get("image_path"), str)
        }
        candidate_keys: list[str] = []
        try:
            retained: list[dict[str, Any]] = []
            if not isinstance(review, dict) or not isinstance(survivors, list):
                raise ValueError("member archive is missing its review survivors")
            for position, survivor in enumerate(survivors[:_MAX_REVIEW_FILES]):
                if not isinstance(survivor, dict):
                    raise ValueError("member archive contains an invalid review survivor")
                reference = survivor.get("reference")
                if not isinstance(reference, str) or not reference:
                    raise ValueError("member review survivor is missing its reference")
                image_path = diagnostic_paths.get(reference)
                if not isinstance(image_path, str) or not image_path:
                    raise ValueError(f"member review survivor {reference!r} has no diagnostic path")
                source = Path(image_path).resolve()
                if not source.is_file() or not source.is_relative_to(root):
                    raise ValueError(
                        f"member review survivor {reference!r} escapes or is missing"
                    )
                key = f"s{member.member_index:03d}-candidate-{position:03d}.jpg"
                shutil.copyfile(source, out_dir / key)
                candidate_keys.append(key)
                member.candidate_files[reference] = key
                survivor["artifact_key"] = key
                retained.append(survivor)
            review["survivors"] = retained
            review["archived_count"] = len(retained)
            review["truncated_count"] = max(0, len(survivors) - len(retained))

            try:
                evidence_keys = _materialize_member_evidence(
                    member, out_dir, root=root, diagnostic_paths=diagnostic_paths
                )
            except Exception:  # noqa: BLE001 - evidence never fails a member
                evidence_keys = []
                member.rejected_files = {}

            archive = f"run-{member.member_index:03d}.json"
            write_run_json(out_dir / archive, member.payload)
            member.archive_file = archive
            produced.extend([archive, *candidate_keys, *evidence_keys])
        except Exception as exc:  # member archive/copy failure
            member.status = "failed"
            member.error = str(exc)[:2000]
            member.recommendation = None
            member.archive_file = None
            member.candidate_files = {}
            member.rejected_files = {}

    # This is required fallback evidence. Failure escapes systemically and the
    # host creates no projections.
    write_run_json(out_dir / "group-result.json", output.result_document())
    return ["group-result.json", *produced]
