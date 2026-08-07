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
    rejection_label,
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


_OCR_REASON_BASES = frozenset(
    {
        "text_heavy",
        "no_text",
        "no_title",
        "format_blocklist",
        "has_title",
    }
)


def _is_ocr_candidate(candidate: dict) -> bool:
    reason = _rejection_base(candidate.get("rejection_reason")) or ""
    return (
        candidate.get("stage_reached") == "ocr"
        or reason in _OCR_REASON_BASES
        or reason.startswith("ocr")
    )


def _ocr_error_detail(reason: str | None) -> str | None:
    if _rejection_base(reason) != "ocr_error":
        return None
    _base, separator, detail = (reason or "").partition(":")
    if separator and detail.strip():
        return detail.strip()
    return "OCR worker was unavailable."


def _ocr_region_views(candidate: dict) -> list[dict]:
    """Normalize compact current-run regions and older residual-box archives."""
    raw_regions = candidate.get("ocr_display_regions")
    if not isinstance(raw_regions, list):
        raw_regions = candidate.get("ocr_residual_boxes")
    if not isinstance(raw_regions, list):
        return []

    regions: list[dict] = []
    for region in raw_regions:
        if not isinstance(region, dict):
            continue
        text = region.get("text")
        if not isinstance(text, str) or not text:
            continue
        confidence = region.get("confidence")
        regions.append(
            {
                "text": text,
                "confidence": float(confidence)
                if isinstance(confidence, int | float) and not isinstance(confidence, bool)
                else None,
                "category": region.get("category")
                if isinstance(region.get("category"), str)
                else None,
                "semantic_source": region.get("semantic_source")
                if isinstance(region.get("semantic_source"), str)
                else None,
                "semantic_span_text": region.get("semantic_span_text")
                if isinstance(region.get("semantic_span_text"), str)
                else None,
                "is_title": bool(region.get("is_title")),
                "is_title_fragment": bool(region.get("is_title_fragment")),
                "is_significant": bool(region.get("is_significant")),
                "counts_toward_rejection": region.get("counts_toward_rejection")
                if isinstance(region.get("counts_toward_rejection"), bool)
                else None,
            }
        )
    return regions


def _ocr_evidence(candidate: dict) -> dict | None:
    """Return bounded OCR inspection data without exposing the DEBUG trace."""
    if not _is_ocr_candidate(candidate):
        return None

    detected_text = candidate.get("ocr_detected_text")
    detected_text = detected_text if isinstance(detected_text, str) else None
    regions = _ocr_region_views(candidate)
    available = any(
        candidate.get(key) is not None
        for key in (
            "ocr_detected_text",
            "ocr_title_bbox",
            "ocr_residual_boxes",
            "ocr_display_regions",
        )
    )
    return {
        "available": available,
        "has_text": bool(detected_text) or bool(regions),
        "detected_text": detected_text,
        "title_matched": bool(candidate.get("ocr_title_bbox"))
        or any(region["is_title"] for region in regions),
        "regions": regions,
        "profile": candidate.get("ocr_text_profile")
        if isinstance(candidate.get("ocr_text_profile"), dict)
        else None,
        "error": _ocr_error_detail(candidate.get("rejection_reason")),
    }


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
        "rejection_label": rejection_label(candidate.get("rejection_reason")),
        "rejection_explanation": explain_rejection(candidate.get("rejection_reason")),
        "ocr_evidence": _ocr_evidence(candidate),
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


def _review_mode(archive: dict, *, auto_pick_available: bool) -> str:
    """Return the presentation mode supported by the canonical run projection.

    A neutral cold-start order carries ordinals solely to keep the review list
    stable.  It must never become a recommendation just because its first
    candidate has ``rank == 1``.  Likewise, historical runs without a
    persisted auto-pick fail closed into manual review.
    """
    if archive.get("personalization_mode") == "collecting":
        return "collecting"
    return "personalized" if auto_pick_available else "collecting"


def build_results_payload(
    archive: dict,
    *,
    run_id: str,
    status: str,
    reviewed: bool,
    scorer: str | None,
    auto_pick_filename: str | None,
) -> dict:
    """Assemble the §17.3 run-results payload from an archived run."""
    candidates = diagnostic_candidates(archive)

    ranked = sorted(
        (c for c in candidates if c.get("rank") is not None),
        key=lambda c: c["rank"],
    )
    ranked_views = [_candidate_view(run_id, c) for c in ranked]

    # Stacks: group ranked survivors of the same base design. One entry per
    # stack, members ordered by stack_pos (A,B,C…), stacks by stack_rank.
    stacks = _build_stacks(run_id, ranked)

    # The persisted filename is the only source of an auto-pick.  Candidate
    # ordinals can describe neutral cold-start display order, not a model
    # recommendation, so never infer an auto-pick from rank 1.
    auto_src = (
        next(
            (
                candidate
                for candidate in candidates
                if candidate.get("orig_filename") == auto_pick_filename
            ),
            None,
        )
        if auto_pick_filename is not None
        else None
    )
    review_mode = _review_mode(archive, auto_pick_available=auto_src is not None)
    if review_mode == "collecting":
        auto_src = None

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
        "scorer": scorer if review_mode == "personalized" else None,
        "review_mode": review_mode,
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
    return payload


def _counts_from_candidates(candidates: list[dict]) -> dict[str, int]:
    return {
        "total_candidates": len(candidates),
        "ranked": sum(1 for c in candidates if c.get("rank") is not None),
    }


def diagnostic_candidates(archive: dict) -> list[dict]:
    """Return bounded diagnostic records, accepting pre-7B archive shape only for history."""
    ledger = archive.get("diagnostic_ledger")
    if isinstance(ledger, dict) and isinstance(ledger.get("candidates"), list):
        return [candidate for candidate in ledger["candidates"] if isinstance(candidate, dict)]
    candidates = archive.get("candidates")
    return (
        [candidate for candidate in candidates if isinstance(candidate, dict)]
        if isinstance(candidates, list)
        else []
    )


def find_candidate(archive: dict, orig_filename: str) -> dict | None:
    """Find diagnostic evidence by reference; this does not make it reviewable."""
    for candidate in diagnostic_candidates(archive):
        if candidate.get("orig_filename") == orig_filename:
            return candidate
    return None


def find_review_survivor(archive: dict, reference: str) -> dict | None:
    """Find an explicitly persisted objective survivor, never a diagnostic record."""
    review = archive.get("review")
    if not isinstance(review, dict):
        return None
    survivors = review.get("survivors")
    if not isinstance(survivors, list):
        return None
    for survivor in survivors:
        if (
            isinstance(survivor, dict)
            and survivor.get("reference") == reference
            and survivor.get("objective_eligible") is True
        ):
            return survivor
    return None


def find_review_evidence(archive: dict, reference: str) -> dict | None:
    """Find an archived *rejected* candidate's image identity.

    Strictly separate from :func:`find_review_survivor`: these candidates failed
    an objective gate, so they are never review-eligible. They are archived only
    so the review UI can render what each stage threw away.
    """
    block = archive.get("review_evidence")
    if not isinstance(block, dict):
        return None
    candidates = block.get("candidates")
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if (
            isinstance(candidate, dict)
            and candidate.get("reference") == reference
            and candidate.get("objective_eligible") is not True
        ):
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
