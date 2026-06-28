from __future__ import annotations

import hashlib
import json
import shutil
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from marquee.api.results import find_candidate
from marquee.config import settings
from marquee.models import PipelineRun
from marquee.pipeline.run_manager import run_manager
from marquee.pipeline.runner import _sanitise_filename

LabelKind = Literal["false_positive", "false_negative"]

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
        "confidence_threshold",
        "strip_confidence_threshold",
        "bottom_confidence_threshold",
        "fuzzy_cutoff",
        "title_proximity_pixels",
        "residual_significant_area_fraction",
        "residual_significant_width_fraction",
        "enhance_retry",
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


def capture_ocr_label(run: PipelineRun, orig_filename: str, label_kind: LabelKind) -> CaptureResult:
    archive = run_manager.load_archive(run.run_id, run.archive_path)
    if archive is None:
        raise OcrLabelCaptureError(
            f"Run {run.run_id} archive is missing or unreadable",
            status_code=404,
        )
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
    work_dir, work_dir_source = _resolve_work_dir(run, archive)
    image_source, image_source_kind = _resolve_image_source(work_dir, candidate, orig_filename)
    log_source, log_source_kind = _resolve_log_source(work_dir)
    log_lines, log_missing_artifacts = _extract_log_lines(
        log_source,
        log_source_kind,
        archive,
        candidate,
        orig_filename,
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
    if log_missing_artifacts:
        missing_artifacts.extend(log_missing_artifacts)

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
                "archive_path": run.archive_path,
                "output_dir": run.output_dir,
            },
            "candidate": candidate,
            "config_snapshot": archive.get("config", {}),
            "stage_timings_seconds": archive.get("stage_timings_seconds", {}),
            "source_resolution": {
                "work_dir": str(work_dir) if work_dir is not None else None,
                "work_dir_source": work_dir_source,
                "image_source": str(image_source) if image_source is not None else None,
                "image_source_kind": image_source_kind,
                "log_source": str(log_source) if log_source is not None else None,
                "log_source_kind": log_source_kind,
                "log_line_count": len(log_lines),
            },
            "image_copied": image_copied,
            "log_captured": log_captured,
            "missing_artifacts": missing_artifacts,
        }
        (capture_dir / "capture.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    return CaptureResult(
        label_kind=label_kind,
        capture_dir=capture_dir,
        image_copied=image_copied,
        log_captured=log_captured,
        missing_artifacts=tuple(missing_artifacts),
        metadata=metadata,
    )


def clear_ocr_labels() -> dict[str, object]:
    root = capture_root()
    with _CAPTURE_LOCK:
        run_dirs = [path for path in root.iterdir() if path.is_dir()] if root.exists() else []
        capture_dirs = [
            path for path in root.glob("*/*/*") if path.is_dir()
        ] if root.exists() else []
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
        "false_positive": [],
        "false_negative": [],
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


def _resolve_work_dir(
    run: PipelineRun,
    archive: dict[str, object],
) -> tuple[Path | None, str]:
    if run.output_dir:
        return Path(run.output_dir).resolve(), "pipeline_run.output_dir"
    title = archive.get("title")
    if isinstance(title, str) and title:
        return (settings.runs_work_path / _sanitise_filename(title)).resolve(), "title_fallback"
    return None, "missing"


def _resolve_image_source(
    work_dir: Path | None,
    candidate: dict[str, object],
    orig_filename: str,
) -> tuple[Path | None, str]:
    if work_dir is not None:
        original_path = (work_dir / "0-originals" / orig_filename).resolve()
        if original_path.is_file():
            return original_path, "originals"

    image_path = candidate.get("image_path")
    if isinstance(image_path, str):
        archived_path = Path(image_path).resolve()
        if archived_path.is_file():
            return archived_path, "archived_image_path"
    return None, "missing"


def _resolve_log_source(work_dir: Path | None) -> tuple[Path | None, str]:
    if work_dir is None:
        return None, "missing"
    pipeline_log = work_dir / "pipeline.log"
    if pipeline_log.is_file():
        return pipeline_log, "pipeline.log"
    pipeline_run_json = work_dir / "pipeline_run.json"
    if pipeline_run_json.is_file():
        return pipeline_run_json, "pipeline_run.json"
    return pipeline_log, "missing"


def _extract_log_lines(
    log_source: Path | None,
    log_source_kind: str,
    archive: dict[str, object],
    candidate: dict[str, object],
    orig_filename: str,
) -> tuple[list[str], list[str]]:
    if log_source is None or not log_source.is_file():
        return [], ["log"]
    if log_source_kind == "pipeline.log":
        lines = [
            line.rstrip("\n")
            for line in log_source.read_text(encoding="utf-8", errors="replace").splitlines()
            if f"file={orig_filename}" in line
        ]
        if lines:
            return lines, []
        fallback_lines = _synthesise_log_lines(archive, candidate, orig_filename)
        if fallback_lines:
            return fallback_lines, []
        return [], ["log_lines"]
    if log_source_kind == "pipeline_run.json":
        fallback_lines = _synthesise_log_lines(archive, candidate, orig_filename)
        if fallback_lines:
            return fallback_lines, []
    return [], ["log"]


def _synthesise_log_lines(
    archive: dict[str, object],
    candidate: dict[str, object],
    orig_filename: str,
) -> list[str]:
    return [
        "# pipeline.log was unavailable; synthesized from pipeline_run.json/archive data",
        f"orig_filename={orig_filename}",
        f"movie_title={archive.get('title')!r}",
        f"run_id={archive.get('run_id')!r}",
        f"stage_reached={candidate.get('stage_reached')!r}",
        f"rejection_reason={candidate.get('rejection_reason')!r}",
        f"gate_reason={candidate.get('gate_reason')!r}",
        f"image_path={candidate.get('image_path')!r}",
        "raw_features=" + json.dumps(candidate.get("raw_features", {}), sort_keys=True),
        "normalized_features=" + json.dumps(candidate.get("normalized_features", {}), sort_keys=True),
        "extended_features=" + json.dumps(candidate.get("extended_features", {}), sort_keys=True),
        "contributions=" + json.dumps(candidate.get("contributions", {}), sort_keys=True),
        "typicality_detail=" + json.dumps(candidate.get("typicality_detail", {}), sort_keys=True),
    ]


def _safe_capture_id(orig_filename: str) -> str:
    stem = _sanitise_filename(Path(orig_filename).stem) or "poster"
    digest = hashlib.sha1(orig_filename.encode("utf-8")).hexdigest()[:8]
    return f"{stem}__{digest}"
