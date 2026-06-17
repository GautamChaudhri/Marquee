"""Shape an archived pipeline run into the three-section results payload.

The archive (``pipeline_run.json`` / ``data/runs/archive/{run_id}.json``)
stores one dict per candidate (``CandidateScore.to_dict()``). The frontend
wants those grouped into auto-pick / ranked survivors / rejected-by-stage,
with plain-language explanations and stable poster URLs. That grouping is
pure data transformation — it lives here so the route stays thin and the
feedback/rescore paths can reuse the same lookups.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass

from marquee.api.explanations import (
    explain_rejection,
    explain_top_contributions,
    suggest_for_summary,
)


@dataclass(frozen=True)
class BackupResult:
    backup_id: str
    created_at: str
    backup_dir: str
    db_path: str
    state_path: str
    manifest_path: str
    db_size: int
    state_size: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BackupInfo:
    backup_id: str
    created_at: str
    backup_dir: str
    db_size: int
    state_size: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RestoreResult:
    backup_id: str
    restored: bool
    restored_db: bool
    restored_state: bool
    restart_required: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _rejection_base(reason: str | None) -> str | None:
    if not reason:
        return None
    return reason.split(":", 1)[0]


def categorize_rejection(candidate: dict) -> str:
    """Bucket a non-ranked candidate into gate | ocr | dedup | errored."""
    reason = candidate.get("rejection_reason") or ""
    base = _rejection_base(reason) or ""
    if base.startswith("dedup_"):
        return "dedup"
    if base.startswith("feature_error") or base.startswith("download_error"):
        return "errored"
    if base.startswith("ocr") or base in ("no_text", "no_title"):
        return "ocr"
    if candidate.get("stage_reached") == "ocr":
        return "ocr"
    return "gate"


def poster_url(run_id: str, orig_filename: str) -> str:
    return f"/api/pipeline/runs/{run_id}/posters/{orig_filename}"


def _candidate_view(run_id: str, candidate: dict) -> dict:
    return {
        "orig_filename": candidate["orig_filename"],
        "rank": candidate.get("rank"),
        "final_score": candidate.get("final_score"),
        "poster_url": poster_url(run_id, candidate["orig_filename"]),
        "contributions": candidate.get("contributions"),
        "raw_features": candidate.get("raw_features"),
        "normalized_features": candidate.get("normalized_features"),
        "gate_decision": candidate.get("gate_decision"),
        "gate_reason": candidate.get("gate_reason"),
        "stage_reached": candidate.get("stage_reached"),
        "rejection_reason": candidate.get("rejection_reason"),
        "rejection_explanation": explain_rejection(candidate.get("rejection_reason")),
        "dedup_kept": candidate.get("dedup_kept"),
    }


def build_results_payload(
    archive: dict,
    *,
    run_id: str,
    status: str,
    reviewed: bool,
    scorer: str | None,
) -> dict:
    """Assemble the §17.3 run-results payload from an archived run."""
    candidates = archive.get("candidates", [])

    ranked = sorted(
        (c for c in candidates if c.get("rank") is not None),
        key=lambda c: c["rank"],
    )
    ranked_views = [_candidate_view(run_id, c) for c in ranked]

    auto_pick = None
    if ranked:
        auto_pick = _candidate_view(run_id, ranked[0])
        auto_pick["explanations"] = explain_top_contributions(
            ranked[0].get("contributions")
        )

    rejected: dict[str, list[dict]] = {"gate": [], "ocr": [], "dedup": [], "errored": []}
    summary: Counter[str] = Counter()
    for candidate in candidates:
        if candidate.get("rank") is not None:
            continue
        bucket = categorize_rejection(candidate)
        rejected[bucket].append(_candidate_view(run_id, candidate))
        base = _rejection_base(candidate.get("rejection_reason"))
        if base:
            summary[base] += 1

    rejection_summary = dict(summary)
    return {
        "run_id": run_id,
        "movie": {
            "id": archive.get("movie_id"),
            "title": archive.get("title"),
            "tmdb_id": archive.get("tmdb_id"),
        },
        "status": status,
        "scorer": scorer,
        "reviewed": reviewed,
        "auto_pick": auto_pick,
        "ranked": ranked_views,
        "rejected": rejected,
        "rejection_summary": rejection_summary,
        "suggestion": suggest_for_summary(rejection_summary),
        "counts": archive.get("counts")
        or _counts_from_candidates(candidates),
        "stage_timings_s": archive.get("stage_timings_seconds", {}),
        "config_snapshot": archive.get("config", {}),
    }


def _counts_from_candidates(candidates: list[dict]) -> dict[str, int]:
    return {
        "total_candidates": len(candidates),
        "ranked": sum(1 for c in candidates if c.get("rank") is not None),
    }


def find_candidate(archive: dict, orig_filename: str) -> dict | None:
    for candidate in archive.get("candidates", []):
        if candidate.get("orig_filename") == orig_filename:
            return candidate
    return None


def feature_vector_from_archive(candidate: dict):
    """Reconstruct a FeatureVector from an archived candidate, for rescore.

    Returns None when the candidate has no computed features (rejected before
    the style stage), since there is nothing to re-rank.
    """
    import dataclasses  # noqa: PLC0415

    from marquee.pipeline.types import FeatureVector  # noqa: PLC0415

    raw = candidate.get("raw_features")
    normalized = candidate.get("normalized_features")
    if not raw or not normalized:
        return None
    field_names = {f.name for f in dataclasses.fields(FeatureVector)}
    core = {k: v for k, v in raw.items() if k in field_names}
    try:
        fv = FeatureVector(**core)
    except TypeError:
        return None
    fv.normalized = dict(normalized)
    fv.extended = dict(candidate.get("extended_features") or {})
    return fv
