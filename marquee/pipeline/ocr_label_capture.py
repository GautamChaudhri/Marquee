from __future__ import annotations

import hashlib
import json
import math
import shutil
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from marquee.api.results import find_candidate
from marquee.config import settings
from marquee.models import PipelineRun
from marquee.pipeline.runner import _sanitise_filename

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

    title = str(archive.get("title") or f"movie-{run.movie_id}")
    image_source = candidate_image if candidate_image and candidate_image.is_file() else None
    log_lines, log_missing_artifacts, log_source_kind = _extract_log_lines(
        archive, candidate, orig_filename
    )

    capture_dir = (
        capture_root()
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

    ocr_block = _ocr_diagnostics(candidate)

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
            "movie": {
                "movie_id": archive.get("movie_id"),
                "title": archive.get("title"),
                "tmdb_id": archive.get("tmdb_id"),
            },
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
        run_dirs = [path for path in root.iterdir() if path.is_dir()] if root.exists() else []
        capture_dirs = (
            [path for path in root.glob("*/*/*") if path.is_dir()] if root.exists() else []
        )
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
    for capture_path in sorted(capture_root().glob(f"*__{run_id}/*/*/capture.json")):
        try:
            payload = json.loads(capture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
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
    lines: list[str] = ["# --- OCR trace (every detected box + the gate decision) ---"]
    size = trace.get("image_size") or {}
    passes = trace.get("passes_run") or []
    retry = trace.get("enhance_retry") or {}
    lines.append(
        "image_size={}x{} | passes={} | enhance_retry: triggered={} recovered_text={}".format(
            size.get("width"),
            size.get("height"),
            ",".join(passes) if isinstance(passes, list) else passes,
            retry.get("triggered"),
            retry.get("recovered_text"),
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

    decision = trace.get("decision") or {}
    lines.append(
        "decision: mode={} accepted={} reason={} has_title={} require_title={} "
        "significant_residual={}/{} significant_area={}/{}".format(
            decision.get("mode"),
            decision.get("accepted"),
            decision.get("reason"),
            decision.get("has_title"),
            decision.get("require_title"),
            decision.get("significant_residual_count"),
            decision.get("max_residual_boxes"),
            decision.get("significant_area_fraction"),
            decision.get("max_residual_area_fraction"),
        )
    )

    boxes = trace.get("detected_boxes") or []
    lines.append(f"detected_boxes ({len(boxes) if isinstance(boxes, list) else 0}):")
    for box in boxes if isinstance(boxes, list) else []:
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
            "  [{}] conf={} cat={} {} text={!r} bbox={}".format(
                box.get("pass"),
                f"{conf:.2f}" if isinstance(conf, (int, float)) else conf,
                box.get("category"),
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
