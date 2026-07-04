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
from marquee.pipeline.types import find_auto_pick_candidate


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


# Ordered pipeline stages a candidate can be eliminated at — one tab each in the
# UI. Each rejection_reason *base* maps to exactly one stage.
_STAGE_LABELS: list[tuple[str, str]] = [
    ("sha256", "Exact duplicates (SHA-256)"),
    ("resolution", "Below resolution floor"),
    ("style", "Style gate (aesthetic / off-style)"),
    ("ocr", "Text gate (PaddleOCR)"),
    ("phash", "Near-duplicates (perceptual hash)"),
    ("fan_junk", "Fan-junk combo gate"),
    ("errored", "Errored (decode / feature / download)"),
]
_REASON_STAGE: dict[str, str] = {
    "dedup_sha256": "sha256",
    "resolution_floor": "resolution",
    "aesthetic_floor": "style",
    "off_style_floor": "style",
    "text_heavy": "ocr",
    "ocr_text_heavy": "ocr",
    "no_title": "ocr",
    "no_text": "ocr",
    "format_blocklist": "ocr",
    "dedup_phash": "phash",
    "fan_junk_combo": "fan_junk",
    "feature_error": "errored",
    "download_error": "errored",
    "ocr_error": "errored",
}


def stage_for_candidate(candidate: dict) -> str:
    """Bucket a non-ranked candidate into one of the per-stage tabs."""
    base = _rejection_base(candidate.get("rejection_reason")) or ""
    if base in _REASON_STAGE:
        return _REASON_STAGE[base]
    if base.startswith("dedup_"):
        return "phash" if "phash" in base else "sha256"
    if base.startswith("ocr") or base in ("no_text", "no_title"):
        return "ocr"
    if base.startswith(("feature_error", "download_error")):
        return "errored"
    if candidate.get("stage_reached") == "ocr":
        return "ocr"
    return "style"


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
        "stack_id": candidate.get("stack_id"),
        "stack_rank": candidate.get("stack_rank"),
        "stack_pos": candidate.get("stack_pos"),
        "stack_label": candidate.get("stack_label"),
        "stack_size": candidate.get("stack_size"),
        "stack_score": candidate.get("stack_score"),
    }


def _build_stacks(run_id: str, ranked: list[dict]) -> list[dict]:
    """Group ranked survivors into per-design stacks for the UI.

    Returns [] when the run has no stack metadata (stacking disabled or an
    archive predating the stack layer), so the frontend falls back to the
    flat ranked grid.
    """
    if not ranked or ranked[0].get("stack_rank") is None:
        return []
    by_stack: dict[int, list[dict]] = {}
    for candidate in ranked:
        by_stack.setdefault(candidate.get("stack_id"), []).append(candidate)
    entries: list[dict] = []
    for members in by_stack.values():
        members.sort(key=lambda c: c.get("stack_pos") or 0)
        rep = members[0]
        entries.append(
            {
                "stack_rank": rep.get("stack_rank"),
                "stack_id": rep.get("stack_id"),
                "label": str(rep.get("stack_rank")),
                "size": rep.get("stack_size") or len(members),
                "stack_score": rep.get("stack_score"),
                "representative": _candidate_view(run_id, rep),
                "members": [_candidate_view(run_id, m) for m in members],
            }
        )
    entries.sort(key=lambda s: s["stack_rank"])
    return entries


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

    # Stacks: group ranked survivors of the same base design. One entry per
    # stack, members ordered by stack_pos (A,B,C…), stacks by stack_rank.
    stacks = _build_stacks(run_id, ranked)

    # Auto-pick is "1A" — the representative of the top stack (shared with the
    # persisted pipeline_runs.auto_pick_filename so the Review thumbnail and
    # this payload always agree on which poster won).
    auto_src = find_auto_pick_candidate(candidates)

    auto_pick = None
    if auto_src is not None:
        auto_pick = _candidate_view(run_id, auto_src)
        auto_pick["explanations"] = explain_top_contributions(auto_src.get("contributions"))

    rejected: dict[str, list[dict]] = {"gate": [], "ocr": [], "dedup": [], "errored": []}
    by_stage: dict[str, list[dict]] = {key: [] for key, _ in _STAGE_LABELS}
    summary: Counter[str] = Counter()
    for candidate in candidates:
        if candidate.get("rank") is not None:
            continue
        view = _candidate_view(run_id, candidate)
        rejected[categorize_rejection(candidate)].append(view)
        by_stage[stage_for_candidate(candidate)].append(view)
        base = _rejection_base(candidate.get("rejection_reason"))
        if base:
            summary[base] += 1

    # One ordered group per pipeline stage, for the per-stage rejection tabs.
    rejected_by_stage = [
        {"stage": key, "label": label, "count": len(by_stage[key]), "posters": by_stage[key]}
        for key, label in _STAGE_LABELS
    ]
    rejection_summary = dict(summary)
    payload = {
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
        "stacks": stacks,
        "rejected": rejected,
        "rejected_by_stage": rejected_by_stage,
        "rejection_summary": rejection_summary,
        "suggestion": suggest_for_summary(rejection_summary),
        "counts": archive.get("counts") or _counts_from_candidates(candidates),
        "stage_timings_s": archive.get("stage_timings_seconds", {}),
        "config_snapshot": archive.get("config", {}),
        "media_type": archive.get("media_type", "movie"),
    }
    if "subject" in archive:
        payload["subject"] = archive["subject"]
    if "official_pick" in archive:
        payload["official_pick"] = archive["official_pick"]
    return payload


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
