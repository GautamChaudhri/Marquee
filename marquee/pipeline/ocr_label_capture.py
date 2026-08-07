from __future__ import annotations

import hashlib
import json
import math
import shutil
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from marquee.api.results import find_candidate
from marquee.core.runtime_settings import effective_settings as settings
from marquee.models import PipelineRun
from marquee.pipeline.runner import _sanitise_filename
from marquee.pipeline.types import BoundingBox

LabelKind = Literal["false_rejection", "false_acceptance"]

REQUIRED_OCR_SNAPSHOT_KEYS = frozenset(
    {
        "device",
        "workers",
        "detail_passes",
        "max_residual_boxes",
        "max_residual_area_fraction",
        "mode",
        "require_title",
        "accept_no_text_fallback",
        "allow_title",
        "allow_director",
        "allow_studio",
        "allow_rating",
        "allow_tagline",
        "allow_billing",
        "confidence_threshold",
        "strip_confidence_threshold",
        "bottom_confidence_threshold",
        "fuzzy_cutoff",
        "title_proximity_pixels",
        "residual_significant_area_fraction",
        "residual_significant_width_fraction",
        "enhance_retry",
        "title_recovery_enabled",
        "title_recovery_confidence_threshold",
    }
)

_CAPTURE_LOCK = threading.Lock()
_CAPTURE_FOLDER_BY_MEDIA_TYPE = {
    "movie": "movie-posters",
    "series": "show-posters",
    "season": "season-posters",
}


class OcrLabelCaptureError(Exception):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class CaptureResult:
    label_kind: LabelKind
    capture_dir: Path
    image_copied: bool
    log_captured: bool
    missing_artifacts: tuple[str, ...]
    metadata: dict[str, object]


def capture_root() -> Path:
    root = settings.data_dir_path / "debug" / "ocr-labels"
    root.mkdir(parents=True, exist_ok=True)
    return root


def missing_ocr_snapshot_keys(archive: dict[str, object]) -> list[str]:
    config = archive.get("config")
    if not isinstance(config, dict):
        return sorted(REQUIRED_OCR_SNAPSHOT_KEYS)
    ocr = config.get("ocr")
    if not isinstance(ocr, dict):
        return sorted(REQUIRED_OCR_SNAPSHOT_KEYS)
    return sorted(key for key in REQUIRED_OCR_SNAPSHOT_KEYS if key not in ocr)


def _json_safe(obj: object) -> object:
    """Recursively replace non-finite floats (NaN/Inf) with None.

    Feature extraction intentionally uses NaN as an "unknown" sentinel (e.g.
    title geometry when no title box is found — calibration relies on it), and
    the run archive persists those as the non-standard ``NaN`` token. Starlette
    renders JSON responses with ``allow_nan=False``, so echoing such a value
    raises and the route 500s. Sanitize only at this serialization boundary so
    both ``capture.json`` and the HTTP response are standards-compliant JSON.
    """
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {key: _json_safe(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(value) for value in obj]
    return obj


def _capture_subject(run: PipelineRun, archive: dict[str, object]) -> tuple[str, dict[str, object]]:
    """Return the immutable asset identity and its dedicated capture folder."""
    media_type = run.media_type
    capture_folder = _CAPTURE_FOLDER_BY_MEDIA_TYPE.get(media_type)
    if capture_folder is None:
        raise OcrLabelCaptureError(
            f"Run {run.run_id} has unsupported poster media type {media_type!r}", status_code=422
        )

    archive_subject = archive.get("subject")
    subject = archive_subject if isinstance(archive_subject, dict) else {}
    title = archive.get("title")
    if not isinstance(title, str) or not title.strip():
        title = subject.get("title")
    if not isinstance(title, str) or not title.strip():
        identifier = (
            run.movie_id
            if media_type == "movie"
            else run.series_id
            if media_type == "series"
            else run.season_id
        )
        title = f"{media_type}-{identifier if identifier is not None else 'unknown'}"

    return capture_folder, {
        "media_type": media_type,
        "title": title,
        "movie_id": run.movie_id if media_type == "movie" else None,
        "series_id": run.series_id if media_type in {"series", "season"} else None,
        "season_id": run.season_id if media_type == "season" else None,
        "season_number": subject.get("season_number") if media_type == "season" else None,
        "series_title": subject.get("series_title") if media_type == "season" else None,
        "tmdb_id": archive.get("tmdb_id"),
    }


def capture_ocr_label(
    run: PipelineRun,
    archive: dict[str, object],
    candidate_image: Path | None,
    orig_filename: str,
    label_kind: LabelKind,
) -> CaptureResult:
    missing_snapshot_keys = missing_ocr_snapshot_keys(archive)
    if missing_snapshot_keys:
        joined = ", ".join(missing_snapshot_keys)
        raise OcrLabelCaptureError(
            f"Run {run.run_id} predates the OCR snapshot upgrade; re-run it first "
            f"(missing OCR snapshot keys: {joined})",
            status_code=409,
        )

    candidate = find_candidate(archive, orig_filename)
    if candidate is None:
        raise OcrLabelCaptureError(
            f"No candidate {orig_filename!r} exists in run {run.run_id}",
            status_code=404,
        )

    capture_folder, subject = _capture_subject(run, archive)
    title = str(subject["title"])
    image_source = candidate_image if candidate_image and candidate_image.is_file() else None
    log_lines, log_missing_artifacts, log_source_kind = _extract_log_lines(
        archive, candidate, orig_filename
    )

    capture_dir = (
        capture_root()
        / capture_folder
        / f"{_sanitise_filename(title)}__{run.run_id}"
        / label_kind
        / _safe_capture_id(orig_filename)
    )
    missing_artifacts: list[str] = []
    image_copied = False
    log_captured = bool(log_lines)

    if image_source is None:
        missing_artifacts.append("image")
    missing_artifacts.extend(log_missing_artifacts)

    ocr_block = _json_safe(_ocr_diagnostics(candidate))
    archived_run = _json_safe(archive)

    with _CAPTURE_LOCK:
        capture_dir.mkdir(parents=True, exist_ok=True)

        if image_source is not None:
            poster_ext = image_source.suffix or Path(orig_filename).suffix or ".jpg"
            shutil.copy2(image_source, capture_dir / f"poster{poster_ext.lower()}")
            image_copied = True

        (capture_dir / "log.txt").write_text("\n".join(log_lines) + ("\n" if log_lines else ""))

        metadata = {
            "label_kind": label_kind,
            "marked_at": datetime.now(UTC).isoformat(),
            "subject": subject,
            "run": {
                "run_id": run.run_id,
                "status": run.status,
                "archive_artifact_id": run.archive_artifact_id,
            },
            # The durable OCR read, hoisted from the archived candidate so the
            # downstream tooling reads text / title_bbox / residual_boxes
            # directly without digging through `candidate`.
            "ocr": ocr_block,
            "candidate": candidate,
            "config_snapshot": archive.get("config", {}),
            "stage_timings_seconds": archive.get("stage_timings_seconds", {}),
            "source_resolution": {
                "image_source": str(image_source) if image_source is not None else None,
                "image_source_kind": "job_artifact" if image_source is not None else "missing",
                "log_source": None,
                "log_source_kind": log_source_kind,
                "log_line_count": len(log_lines),
            },
            "image_copied": image_copied,
            "log_captured": log_captured,
            "missing_artifacts": missing_artifacts,
        }
        safe_metadata = _json_safe(metadata)
        (capture_dir / "ocr.json").write_text(
            json.dumps(ocr_block, indent=2, sort_keys=True), encoding="utf-8"
        )
        (capture_dir / "pipeline-run.json").write_text(
            json.dumps(archived_run, indent=2, sort_keys=True), encoding="utf-8"
        )
        (capture_dir / "capture.json").write_text(
            json.dumps(safe_metadata, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    return CaptureResult(
        label_kind=label_kind,
        capture_dir=capture_dir,
        image_copied=image_copied,
        log_captured=log_captured,
        missing_artifacts=tuple(missing_artifacts),
        metadata=safe_metadata,
    )


def clear_ocr_labels() -> dict[str, object]:
    root = capture_root()
    with _CAPTURE_LOCK:
        capture_dirs = [path.parent for path in root.rglob("capture.json") if path.is_file()]
        run_dirs = {path.parent.parent for path in capture_dirs}
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
    return {
        "status": "cleared",
        "root_path": str(root),
        "deleted_run_dirs": len(run_dirs),
        "deleted_capture_dirs": len(capture_dirs),
    }


def list_ocr_labels(run_id: str) -> dict[str, object]:
    labels: dict[LabelKind, list[str]] = {
        "false_rejection": [],
        "false_acceptance": [],
    }
    for capture_path in sorted(capture_root().rglob("capture.json")):
        try:
            payload = json.loads(capture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        run = payload.get("run")
        if not isinstance(run, dict) or run.get("run_id") != run_id:
            continue
        label_kind = payload.get("label_kind")
        candidate = payload.get("candidate")
        if label_kind not in labels or not isinstance(candidate, dict):
            continue
        orig_filename = candidate.get("orig_filename")
        if isinstance(orig_filename, str) and orig_filename not in labels[label_kind]:
            labels[label_kind].append(orig_filename)
    return {
        "run_id": run_id,
        "labels": labels,
    }


def replay_ocr_label_capture(capture_path: Path) -> dict[str, object]:
    """Replay season semantics from a saved capture without loading PaddleOCR."""
    from marquee.pipeline.ocr_filter import (  # noqa: PLC0415 - avoid runner import cycle
        _DetectedBox,
        _normalise,
        _polygon_area,
        _season_semantic_evidence,
    )

    source = capture_path / "capture.json" if capture_path.is_dir() else capture_path
    loaded = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("OCR label capture root must be an object")
    payload: dict[str, object] = loaded

    def object_dict(value: object) -> dict[str, object]:
        return cast("dict[str, object]", value) if isinstance(value, dict) else {}

    def int_value(value: object, default: int) -> int:
        return (
            int(value)
            if isinstance(value, int | float | str) and not isinstance(value, bool)
            else default
        )

    def float_value(value: object, default: float) -> float:
        return (
            float(value)
            if isinstance(value, int | float | str) and not isinstance(value, bool)
            else default
        )

    subject = object_dict(payload.get("subject"))
    ocr = object_dict(payload.get("ocr"))
    trace = object_dict(ocr.get("trace"))
    size = object_dict(trace.get("image_size"))
    season_context = object_dict(trace.get("season_context"))
    raw_boxes_value = trace.get("detected_boxes")
    raw_boxes = raw_boxes_value if isinstance(raw_boxes_value, list) else []
    image_width = int_value(size.get("width"), 1)
    image_height = int_value(size.get("height"), 1)
    season_number = season_context.get("number", subject.get("season_number"))
    season_number = season_number if isinstance(season_number, int) else None
    season_title = season_context.get("title")
    season_title = season_title if isinstance(season_title, str) else None

    boxes: list[_DetectedBox] = []
    raw_by_key: dict[tuple[str, BoundingBox], dict[str, object]] = {}
    for raw_value in raw_boxes:
        raw = object_dict(raw_value)
        text = raw.get("text")
        if not isinstance(text, str):
            continue
        points = raw.get("bbox")
        if not isinstance(points, list) or len(points) != 4:
            continue
        try:
            bbox = cast(
                "BoundingBox",
                tuple((float(point[0]), float(point[1])) for point in points),
            )
        except (TypeError, ValueError, IndexError):
            continue
        if len(bbox) != 4:
            continue
        box = _DetectedBox(
            text=text,
            confidence=float_value(raw.get("confidence"), 0.0),
            bbox=bbox,
            geometry_valid=bool(raw.get("geometry_valid", True)),
        )
        boxes.append(box)
        raw_by_key[(_normalise(box.text), box.bbox)] = raw

    evidence = _season_semantic_evidence(
        boxes,
        image_width=image_width,
        image_height=image_height,
        season_number=season_number,
        season_title=season_title,
    )
    decision = object_dict(trace.get("decision"))
    allow_map = object_dict(decision.get("allow_map"))
    changed_regions: list[dict[str, object]] = []
    denied_count = 0
    denied_area = 0.0
    image_area = float(image_width * image_height)
    for box in boxes:
        key = (_normalise(box.text), box.bbox)
        raw = raw_by_key[key]
        semantic = evidence.get(key)
        previous_value = raw.get("category")
        previous = previous_value if isinstance(previous_value, str) else "other"
        category = semantic.category if semantic else previous
        if semantic and category != previous:
            changed_regions.append(
                {
                    "text": box.text,
                    "from": previous,
                    "to": category,
                    "source": semantic.source,
                    "span_text": semantic.span_text,
                }
            )
        if raw.get("is_significant") and not bool(allow_map.get(category, False)):
            denied_count += 1
            denied_area += _polygon_area(box.bbox) / image_area if image_area > 0 else 0.0

    max_boxes = int_value(decision.get("max_residual_boxes"), 0)
    max_area = float_value(decision.get("max_residual_area_fraction"), 0.0)
    return {
        "capture": str(source),
        "label_kind": payload.get("label_kind"),
        "media_type": subject.get("media_type"),
        "season_number": season_number,
        "season_title": season_title,
        "changed_regions": changed_regions,
        "remaining_denied_count": denied_count,
        "remaining_denied_area_fraction": denied_area,
        "replayed_acceptance": (
            decision.get("mode") == "custom"
            and denied_count <= max_boxes
            and denied_area <= max_area
        ),
    }


def replay_ocr_label_corpus(root: Path | None = None) -> dict[str, object]:
    """Replay every saved label capture and retain per-file failures in the report."""
    source_root = root or (settings.data_dir_path / "debug" / "ocr-labels")
    reports: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    if not source_root.exists():
        return {"root": str(source_root), "captures": reports, "errors": errors}
    for capture_path in sorted(source_root.rglob("capture.json")):
        try:
            reports.append(replay_ocr_label_capture(capture_path))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append({"capture": str(capture_path), "error": f"{type(exc).__name__}: {exc}"})
    return {"root": str(source_root), "captures": reports, "errors": errors}


def _extract_log_lines(
    archive: dict[str, object],
    candidate: dict[str, object],
    orig_filename: str,
) -> tuple[list[str], list[str], str]:
    """Render bounded diagnostics from the immutable canonical archive."""
    reconstructed = _synthesise_log_lines(archive, candidate, orig_filename)
    if _has_ocr_diagnostics(candidate):
        return reconstructed, [], "archive"
    missing = ["ocr_diagnostics"] if _reached_ocr(candidate) else []
    return reconstructed, missing, "synthesized"


# stage_reached values that mean the candidate actually ran through OCR.
_OCR_STAGES = frozenset({"ocr", "phash", "features", "gate", "ranked"})


def _reached_ocr(candidate: dict[str, object]) -> bool:
    """True when the candidate progressed far enough to have an OCR read."""
    return candidate.get("stage_reached") in _OCR_STAGES


def _has_ocr_diagnostics(candidate: dict[str, object]) -> bool:
    """True when the archived candidate carries a *populated* OCR read.

    Distinct from the key merely existing: pre-fix archives (and the batch
    engine before its fix) wrote the keys as null even for candidates that ran
    OCR, so a key-presence check reported false positives. We treat OCR data as
    present when the detected text was captured (empty string counts — OCR ran
    and found nothing) or a full trace exists.
    """
    if candidate.get("ocr_trace"):
        return True
    return candidate.get("ocr_detected_text") is not None


def _ocr_diagnostics(candidate: dict[str, object]) -> dict[str, object]:
    residual = candidate.get("ocr_residual_boxes") or []
    return {
        "available": _has_ocr_diagnostics(candidate),
        "detected_text": candidate.get("ocr_detected_text"),
        "title_bbox": candidate.get("ocr_title_bbox"),
        "residual_boxes": candidate.get("ocr_residual_boxes"),
        "residual_box_count": len(residual) if isinstance(residual, list) else 0,
        # The full structured trace (every detected box + the gate decision).
        # None on non-DEBUG runs and archives predating the trace.
        "trace": candidate.get("ocr_trace"),
    }


def _format_trace_lines(trace: dict[str, object]) -> list[str]:
    """Render the structured OCR trace as a human-readable per-box table + the
    accept/reject decision — this is the 'what OCR saw and did' an LLM reads."""

    def mapping(value: object) -> dict[str, object]:
        return cast("dict[str, object]", value) if isinstance(value, dict) else {}

    lines: list[str] = ["# --- OCR trace (every detected box + the gate decision) ---"]
    size = mapping(trace.get("image_size"))
    passes = trace.get("passes_run") or []
    retry = mapping(trace.get("enhance_retry"))
    lines.append(
        "image_size={}x{} | passes={} | enhance_retry: triggered={} recovered_text={}".format(
            size.get("width"),
            size.get("height"),
            ",".join(passes) if isinstance(passes, list) else passes,
            retry.get("triggered"),
            retry.get("recovered_text"),
        )
    )
    profile = mapping(trace.get("profile"))
    season_context = mapping(trace.get("season_context"))
    lines.append(
        "profile: id={} name={!r} scope={} fingerprint={} | season: number={} title={!r}".format(
            profile.get("id"),
            profile.get("name"),
            profile.get("scope"),
            profile.get("settings_fingerprint"),
            season_context.get("number"),
            season_context.get("title"),
        )
    )
    title = trace.get("title")
    if isinstance(title, dict):
        lines.append(
            "title: text={!r} match_score={} bbox={}".format(
                title.get("text"), title.get("match_score"), title.get("bbox")
            )
        )
    else:
        lines.append("title: <none matched>")

    decision = mapping(trace.get("decision"))
    lines.append(
        "decision: mode={} accepted={} reason={} has_title={} require_title={} "
        "significant_residual={}/{} significant_area={}/{} allowed={} denied={}".format(
            decision.get("mode"),
            decision.get("accepted"),
            decision.get("reason"),
            decision.get("has_title"),
            decision.get("require_title"),
            decision.get("significant_residual_count"),
            decision.get("max_residual_boxes"),
            decision.get("significant_area_fraction"),
            decision.get("max_residual_area_fraction"),
            decision.get("allowed_significant_count"),
            decision.get("denied_significant_count"),
        )
    )

    boxes = trace.get("detected_boxes") or []
    lines.append(f"detected_boxes ({len(boxes) if isinstance(boxes, list) else 0}):")
    for box_value in boxes if isinstance(boxes, list) else []:
        box = mapping(box_value)
        if not box:
            continue
        flags = []
        if box.get("is_title"):
            flags.append("TITLE")
        if box.get("is_residual"):
            flags.append("residual")
        if box.get("is_significant"):
            flags.append(f"significant({box.get('significant_reason')})")
        if box.get("proximity_discounted"):
            flags.append("prox-discounted")
        conf = box.get("confidence")
        lines.append(
            "  [{}] conf={} cat={} semantic={} span={!r} {} text={!r} bbox={}".format(
                box.get("pass"),
                f"{conf:.2f}" if isinstance(conf, (int, float)) else conf,
                box.get("category"),
                box.get("semantic_source"),
                box.get("semantic_span_text"),
                " ".join(flags) if flags else "-",
                box.get("text"),
                box.get("bbox"),
            )
        )
    return lines


def _synthesise_log_lines(
    archive: dict[str, object],
    candidate: dict[str, object],
    orig_filename: str,
) -> list[str]:
    residual = candidate.get("ocr_residual_boxes") or []
    residual_count = len(residual) if isinstance(residual, list) else 0
    has_ocr = _has_ocr_diagnostics(candidate)
    banner = (
        "# OCR trace reconstructed from the immutable run archive"
        if has_ocr
        else "# OCR data unavailable for this candidate (rejected before OCR, or an "
        "archive predating the trace); re-run the movie to capture it"
    )
    lines = [
        banner,
        f"orig_filename={orig_filename}",
        f"movie_title={archive.get('title')!r}",
        f"run_id={archive.get('run_id')!r}",
        f"stage_reached={candidate.get('stage_reached')!r}",
        f"rejection_reason={candidate.get('rejection_reason')!r}",
        f"gate_reason={candidate.get('gate_reason')!r}",
        # The durable OCR read, in the same shape as the runner's live log line.
        "OCR | file={} | text={!r} | title_bbox={} | residual_boxes={}".format(
            orig_filename,
            candidate.get("ocr_detected_text"),
            candidate.get("ocr_title_bbox"),
            residual_count,
        ),
        "ocr_residual_boxes=" + json.dumps(residual, sort_keys=True),
    ]
    trace = candidate.get("ocr_trace")
    if isinstance(trace, dict):
        lines.extend(_format_trace_lines(trace))
    lines.extend(
        [
            "# --- ranking features ---",
            f"image_path={candidate.get('image_path')!r}",
            "raw_features=" + json.dumps(candidate.get("raw_features", {}), sort_keys=True),
            "normalized_features="
            + json.dumps(candidate.get("normalized_features", {}), sort_keys=True),
            "extended_features="
            + json.dumps(candidate.get("extended_features", {}), sort_keys=True),
            "contributions=" + json.dumps(candidate.get("contributions", {}), sort_keys=True),
            "typicality_detail="
            + json.dumps(candidate.get("typicality_detail", {}), sort_keys=True),
        ]
    )
    return lines


def _safe_capture_id(orig_filename: str) -> str:
    stem = _sanitise_filename(Path(orig_filename).stem) or "poster"
    digest = hashlib.sha1(orig_filename.encode("utf-8")).hexdigest()[:8]
    return f"{stem}__{digest}"
