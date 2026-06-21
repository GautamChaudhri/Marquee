"""Three-pass PaddleOCR text gate with title and residual box emission."""

from __future__ import annotations

import difflib
import logging
import multiprocessing
import os
import queue
import re
import sys
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, NoReturn

import numpy as np
from PIL import Image

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.hardware import effective_ocr_omp_threads, effective_ocr_workers
from marquee.pipeline.types import BoundingBox, OCRCandidateResult, OCRTextBox

logger = logging.getLogger(__name__)

FORMAT_BLOCKLIST: frozenset[str] = frozenset(
    {
        "4k", "uhd", "hdr", "bluray", "blu", "ray", "dolby", "atmos",
        "imax", "dts", "hevc", "remux", "1080p", "2160p", "720p", "ultra",
        "cinerama", "panavision", "metrocolor",
    }
)
STUDIO_KEYWORDS: frozenset[str] = frozenset(
    {
        "paramount", "warner", "bros", "disney", "universal", "sony",
        "columbia", "lionsgate", "mgm", "netflix", "a24", "focus",
        "features", "dreamworks", "pixar", "searchlight", "miramax",
        "orion", "touchstone", "blumhouse", "legendary",
    }
)
TOP_STRIP_FRACTION = 0.18
_DIGIT_WORDS = {
    "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
    "6": "six", "7": "seven", "8": "eight", "9": "nine",
}

_worker_ocr = None
_worker_title_tokens: set[str] = set()
_worker_director_tokens: set[str] = set()
_WORKER_READY = "ready"
_WORKER_RESULT = "result"
_WORKER_INIT_ERROR = "init_error"
_WORKER_POLL_SECONDS = 0.5
_WORKER_SHUTDOWN_SECONDS = 15.0
_active_worker_pids: dict[int, str] = {}


@dataclass(frozen=True)
class _DetectedBox:
    text: str
    confidence: float
    bbox: BoundingBox
    # False when the polygon came back in a rotated frame (vertical-textline
    # reads, e.g. stacked title letters). The TEXT is real — vertical titles
    # like Interstellar's teaser are only readable through this pathway — but
    # the coordinates are unusable for geometry decisions.
    geometry_valid: bool = True


def _normalise(text: str) -> str:
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return " ".join(text.split())


def _add_digit_words(tokens: set[str]) -> None:
    for digit, word in _DIGIT_WORDS.items():
        if digit in tokens:
            tokens.add(word)
        if word in tokens:
            tokens.add(digit)


def _resolve_ocr_device(*, paddle_cuda_available: bool) -> str:
    requested = pipeline_settings.OCR_DEVICE
    if requested == "cpu":
        return "cpu"
    if requested == "gpu":
        if not paddle_cuda_available:
            raise RuntimeError("OCR_DEVICE=gpu requested but Paddle CUDA is unavailable")
        return "gpu"
    return "gpu" if paddle_cuda_available else "cpu"


def paddle_cuda_available(*, allow_import: bool = False) -> bool:
    if not allow_import and "paddle" not in sys.modules:
        return False
    try:
        import paddle

        return bool(paddle.device.is_compiled_with_cuda())
    except Exception:
        return False


def active_worker_status() -> dict:
    alive: list[dict[str, object]] = []
    stale: list[dict[str, object]] = []
    for pid, name in list(_active_worker_pids.items()):
        entry = {"pid": pid, "name": name}
        if _pid_alive(pid):
            alive.append(entry)
        else:
            stale.append(entry)
            _active_worker_pids.pop(pid, None)
    return {"active": alive, "stale_reaped": stale}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _register_worker(worker: Any) -> None:
    if worker.pid is None:
        return
    _active_worker_pids[int(worker.pid)] = worker.name
    logger.info("OCR worker started: name=%s pid=%s", worker.name, worker.pid)


def _unregister_worker(worker: Any) -> None:
    if worker.pid is None:
        return
    _active_worker_pids.pop(int(worker.pid), None)
    logger.info(
        "OCR worker exited: name=%s pid=%s exitcode=%s",
        worker.name,
        worker.pid,
        worker.exitcode,
    )


def _load_ocr() -> object:
    from paddleocr import PaddleOCR

    # engine="paddle_dynamic" uses eager execution (safetensors weights) instead of
    # the static Paddle Inference API, which has a PIR+oneDNN bug on Intel 13th-gen CPUs.
    # paddle_dynamic works identically on macOS, Linux CPU, and Linux GPU.
    # PP-OCRv5_mobile_det is used instead of the default server_det because the server
    # model is ~15× slower in dynamic mode on CPU with no meaningful accuracy improvement
    # for the title-detection task. On GPU the difference is smaller but mobile is still
    # the better choice for throughput across many workers.
    device = _resolve_ocr_device(paddle_cuda_available=paddle_cuda_available(allow_import=True))
    logger.info("Loading PaddleOCR on device=%s", device)
    return PaddleOCR(
        use_textline_orientation=True,
        lang="en",
        device=device,
        engine="paddle_dynamic",
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_det_box_thresh=0.3,
        text_det_unclip_ratio=2.0,
    )


def _polygon_area(bbox: BoundingBox) -> float:
    points = np.asarray(bbox, dtype=np.float32)
    x = points[:, 0]
    y = points[:, 1]
    return float(abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2)


_RATING_PATTERN = re.compile(
    r"\b(rated\s+)?(g|pg|pg-13|nc-17|r|nr|unrated|not\s*rated)\b",
    re.IGNORECASE,
)


def classify_text_box(
    box: _DetectedBox,
    *,
    image_w: int,
    image_h: int,
    title_box: _DetectedBox | None,
    title_tokens: set[str],
    director_tokens: set[str],
) -> str:
    """Classify a detected text box into a semantic category.

    Returns one of: ``title``, ``director``, ``rating``, ``studio``,
    ``tagline``, ``billing``, ``other``.

    See design 18 §4 for the full classification spec.
    """
    text = _normalise(box.text)
    words = set(text.split())
    if not words:
        return "other"

    # Title: already matched via _matches_allowed against title_tokens.
    if title_box is not None and box is title_box:
        return "title"

    # Director: regex match OR word match against director_tokens.
    if re.search(r"directed\s+by", box.text, re.IGNORECASE):
        return "director"
    if director_tokens and _matches_allowed(box.text, director_tokens):
        return "director"

    # Rating: MPAA-ish certification marks.
    if _RATING_PATTERN.search(text):
        return "rating"

    # Studio: any word in the known studio keyword set.
    if words & STUDIO_KEYWORDS:
        return "studio"

    # Tagline heuristic: not any of the above, short-ish, roughly centered,
    # upper/mid zone — promotional text.
    y_center = sum(p[1] for p in box.bbox) / 4
    y_center_frac = y_center / image_h
    x_center = sum(p[0] for p in box.bbox) / 4
    x_center_frac = x_center / image_w
    word_count = len(words)
    is_centered = 0.30 < x_center_frac < 0.70
    is_upper_mid = 0.05 < y_center_frac < 0.65
    is_short = word_count <= 8
    if is_centered and is_upper_mid and is_short:
        return "tagline"

    # Billing: dense small bottom-zone text — only classify as billing
    # when the box is unambiguously in the bottom region.
    if y_center_frac > 0.85:
        return "billing"

    return "other"


def _as_bbox(
    polygon: np.ndarray,
    *,
    scale: float = 1.0,
    y_offset: float = 0.0,
) -> BoundingBox:
    points = np.asarray(polygon, dtype=np.float32).reshape(4, 2)
    points[:, 0] /= scale
    points[:, 1] = points[:, 1] / scale + y_offset
    return tuple((float(x), float(y)) for x, y in points)  # type: ignore[return-value]


def _detect_boxes(
    ocr: object,
    image: np.ndarray,
    confidence: float,
    *,
    scale: float = 1.0,
    y_offset: float = 0.0,
) -> list[_DetectedBox]:
    try:
        import paddle
    except ModuleNotFoundError:
        paddle = None

    grad_context = paddle.no_grad() if paddle is not None else nullcontext()
    with grad_context:
        result = ocr.predict(image)
    if not result:
        return []
    height, width = image.shape[0], image.shape[1]
    first = result[0]
    texts = first.get("rec_texts", [])
    scores = first.get("rec_scores", [])
    polygons = first.get("rec_polys", [])
    boxes: list[_DetectedBox] = []
    for text, score, polygon in zip(texts, scores, polygons, strict=False):
        if float(score) < confidence:
            continue
        # Vertical-textline reads come back with coordinates in a rotated
        # frame — outside the analyzed image. The text itself is real
        # (vertical titles are only readable through this pathway) but the
        # geometry is unusable for size/position decisions.
        points = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
        geometry_valid = bool(
            points[:, 0].min() >= -2
            and points[:, 0].max() <= width + 2
            and points[:, 1].min() >= -2
            and points[:, 1].max() <= height + 2
        )
        boxes.append(
            _DetectedBox(
                text=str(text),
                confidence=float(score),
                bbox=_as_bbox(polygon, scale=scale, y_offset=y_offset),
                geometry_valid=geometry_valid,
            )
        )
    return boxes


def _bbox_iou(left: BoundingBox, right: BoundingBox) -> float:
    lx1, lx2 = min(p[0] for p in left), max(p[0] for p in left)
    ly1, ly2 = min(p[1] for p in left), max(p[1] for p in left)
    rx1, rx2 = min(p[0] for p in right), max(p[0] for p in right)
    ry1, ry2 = min(p[1] for p in right), max(p[1] for p in right)
    ix1, iy1 = max(lx1, rx1), max(ly1, ry1)
    ix2, iy2 = min(lx2, rx2), min(ly2, ry2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (lx2 - lx1) * (ly2 - ly1) + (rx2 - rx1) * (ry2 - ry1) - intersection
    return intersection / union if union > 0 else 0.0


def _dedupe_boxes(boxes: list[_DetectedBox]) -> list[_DetectedBox]:
    kept: list[_DetectedBox] = []
    for candidate in sorted(boxes, key=lambda box: box.confidence, reverse=True):
        normalized = _normalise(candidate.text)
        duplicate = any(
            normalized == _normalise(existing.text)
            and _bbox_iou(candidate.bbox, existing.bbox) >= 0.5
            for existing in kept
        )
        if not duplicate:
            kept.append(candidate)
    return kept


def _matches_allowed(text: str, allowed_tokens: set[str]) -> bool:
    words = set(_normalise(text).split())
    if not words:
        return False
    for word in words:
        if _word_matches_token(word, allowed_tokens):
            continue
        return False
    return True


def _word_matches_token(word: str, allowed_tokens: set[str]) -> bool:
    """Return True if *word* is explainable by any token in *allowed_tokens*.

    Checks (in order):
    1. Exact match
    2. Fuzzy similarity ≥ OCR_FUZZY_CUTOFF (catches garbled reads like "latef")
    3. A title token is a substring of the word (e.g. "reloaded" inside "thereloaded")
    4. The word is a substring of a title token (e.g. "redem" inside "redemption")
    """
    if word in allowed_tokens:
        return True
    cutoff = pipeline_settings.OCR_FUZZY_CUTOFF
    if difflib.get_close_matches(word, allowed_tokens, n=1, cutoff=cutoff):
        return True
    if any(token in word for token in allowed_tokens if len(token) >= 3):
        return True
    return any(word in token for token in allowed_tokens if len(word) >= 3)


def _is_significant_residual_word(word: str, allowed_tokens: set[str]) -> bool:
    """Return True if *word* is unambiguously non-title and long enough to matter.

    A word must be ≥ 4 characters AND not composed solely of digits to be
    considered significant.  Pure-digit strings (catalog numbers, years, VHS
    labels) are noise, not promotional text.
    """
    if _word_matches_token(word, allowed_tokens):
        return False
    return len(word) >= 4 and not word.isdigit()


def _bbox_center_distance(b1: BoundingBox, b2: BoundingBox) -> float:
    """Euclidean distance (pixels) between the centres of two bounding boxes."""
    c1x = sum(p[0] for p in b1) / 4
    c1y = sum(p[1] for p in b1) / 4
    c2x = sum(p[0] for p in b2) / 4
    c2y = sum(p[1] for p in b2) / 4
    return ((c1x - c2x) ** 2 + (c1y - c2y) ** 2) ** 0.5




def _enhance_contrast(image: np.ndarray) -> np.ndarray:
    """Return a contrast-boosted copy of *image* to help OCR find low-contrast text."""
    from PIL import ImageEnhance
    return np.asarray(ImageEnhance.Contrast(Image.fromarray(image)).enhance(2.0))


def _title_match_score(text: str, title_tokens: set[str]) -> float:
    words = set(_normalise(text).split())
    if not words:
        return 0.0
    scores = []
    for word in words:
        scores.append(
            max(
                (
                    difflib.SequenceMatcher(None, word, token).ratio()
                    for token in title_tokens
                ),
                default=0.0,
            )
        )
    return sum(scores) / len(scores)


def _init_worker(title_tokens: set[str], director_tokens: set[str]) -> None:
    global _worker_ocr, _worker_title_tokens, _worker_director_tokens
    _worker_title_tokens = title_tokens
    _worker_director_tokens = director_tokens
    # Idempotent on the model load: the taste trainer re-initializes the
    # tokens once per exemplar (every poster has a different title) and must
    # not reload PaddleOCR 430 times. Workers only ever call this once.
    if _worker_ocr is None:
        _worker_ocr = _load_ocr()


def _exit_worker(result_queue: Any, exit_code: int) -> NoReturn:
    """Flush pending messages without invoking Paddle's native destructors."""
    result_queue.close()
    result_queue.join_thread()
    os._exit(exit_code)


def _worker_main(
    task_queue: Any,
    result_queue: Any,
    title_tokens: set[str],
    director_tokens: set[str],
    omp_threads: int = 0,
) -> NoReturn:
    # Grow GPU memory on demand instead of pre-reserving most of the card, so
    # OCR workers can start even when other GPU jobs are resident (set before
    # paddle is imported in this spawned process).
    os.environ.setdefault("FLAGS_allocator_strategy", "auto_growth")
    if omp_threads > 0:
        # Must be set before paddle is imported: workers x threads ~= cores,
        # otherwise every worker spawns one thread per core and they thrash.
        os.environ.setdefault("OMP_NUM_THREADS", str(omp_threads))
        os.environ.setdefault("OPENBLAS_NUM_THREADS", str(omp_threads))
    try:
        _init_worker(title_tokens, director_tokens)
    except BaseException as exc:
        result_queue.put(
            (
                _WORKER_INIT_ERROR,
                os.getpid(),
                f"{type(exc).__name__}: {exc}",
            )
        )
        task_queue.cancel_join_thread()
        _exit_worker(result_queue, 1)

    result_queue.put((_WORKER_READY, os.getpid(), None))
    while True:
        task = task_queue.get()
        if task is None:
            break
        index, path_string, task_title_tokens, task_director_tokens = task
        result_queue.put(
            (
                _WORKER_RESULT,
                index,
                _process_image(path_string, task_title_tokens, task_director_tokens),
            )
        )

    task_queue.cancel_join_thread()
    _exit_worker(result_queue, 0)


def _process_image(
    path_string: str,
    title_tokens: set[str] | None = None,
    director_tokens: set[str] | None = None,
) -> OCRCandidateResult:
    """Detect text and decide accept/reject against this image's own title.

    Title/director tokens are passed per call so a single worker pool can serve
    many movies in one batch — the expensive PaddleOCR model load stays
    once-per-worker while the cheap title matching varies per task. When omitted
    they fall back to the worker-global tokens (the single-movie / test path).
    """
    if title_tokens is None:
        title_tokens = _worker_title_tokens
    if director_tokens is None:
        director_tokens = _worker_director_tokens
    path = Path(path_string)
    if _worker_ocr is None:
        return OCRCandidateResult(path, False, "", "ocr_error", None)
    try:
        image = np.asarray(Image.open(path).convert("RGB"))
        height = image.shape[0]
        strip_rows = max(1, int(height * TOP_STRIP_FRACTION))
        full = _detect_boxes(
            _worker_ocr,
            image,
            pipeline_settings.OCR_CONFIDENCE_THRESHOLD,
        )
        top: list[_DetectedBox] = []
        bottom: list[_DetectedBox] = []
        if pipeline_settings.OCR_DETAIL_PASSES:
            # 2x upscale like the bottom strip: small header credits
            # ("CHRISTOPHER NOLAN'S") are routinely invisible at native w500.
            top_image = np.asarray(
                Image.fromarray(image[:strip_rows]).resize(
                    (image.shape[1] * 2, strip_rows * 2),
                    Image.Resampling.LANCZOS,
                )
            )
            top = _detect_boxes(
                _worker_ocr,
                top_image,
                pipeline_settings.OCR_STRIP_CONFIDENCE_THRESHOLD,
                scale=2.0,
            )
            bottom_image = np.asarray(
                Image.fromarray(image[height - strip_rows:]).resize(
                    (image.shape[1] * 2, strip_rows * 2),
                    Image.Resampling.LANCZOS,
                )
            )
            bottom = _detect_boxes(
                _worker_ocr,
                bottom_image,
                pipeline_settings.OCR_BOTTOM_CONFIDENCE_THRESHOLD,
                scale=2.0,
                y_offset=height - strip_rows,
            )
    except Exception as exc:
        logger.warning("OCR failed for %s: %s", path.name, exc)
        return OCRCandidateResult(path, False, "", f"ocr_error: {exc}", None)

    boxes = _dedupe_boxes(full + top + bottom)
    detected_text = _normalise(" ".join(box.text for box in boxes))

    # RC-1 fix: before giving up, retry on a contrast-enhanced image.
    # Many stylized title fonts (embossed, low-contrast, metallic) are
    # invisible to the base model but emerge after a 2× contrast boost.
    if not detected_text and pipeline_settings.OCR_ENHANCE_RETRY:
        enhanced = _enhance_contrast(image)
        retry_boxes = _detect_boxes(
            _worker_ocr,
            enhanced,
            pipeline_settings.OCR_CONFIDENCE_THRESHOLD,
        )
        if retry_boxes:
            boxes = _dedupe_boxes(retry_boxes)
            detected_text = _normalise(" ".join(box.text for box in boxes))

    if not detected_text:
        # Even after retry, OCR found nothing.  The project target is
        # title-only posters, so textless art is rejected here; filter_batch
        # rescues no_text results only when the whole movie has zero titled
        # survivors (OCR_ACCEPT_NO_TEXT fallback).
        return OCRCandidateResult(path, False, "", "no_text", None)

    all_tokens = title_tokens | director_tokens
    words = set(detected_text.split())
    if words & FORMAT_BLOCKLIST:
        return OCRCandidateResult(path, False, detected_text, "format_blocklist", None)

    title_candidates = [
        box for box in boxes if _matches_allowed(box.text, title_tokens)
    ]
    title_box = max(
        title_candidates,
        key=lambda box: _title_match_score(box.text, title_tokens),
        default=None,
    )
    residual = [
        OCRTextBox(
            text=box.text,
            confidence=box.confidence,
            bbox=box.bbox,
            area=_polygon_area(box.bbox),
            geometry_valid=box.geometry_valid,
        )
        for box in boxes
        if not _matches_allowed(box.text, all_tokens)
    ]

    # RC-2 + RC-3 + RC-4 fix: evaluate significance at the *word* level, not
    # the box level.  A box that mixes title words with noise fragments (e.g.
    # "1917 ll6l") used to fail the box-level _matches_allowed check entirely,
    # causing the title words inside to trigger significant_residual.  Now:
    #   • each word is individually classified against title/director tokens
    #   • pure-digit strings (catalog numbers, scan labels) are not significant
    #   • SMALL boxes whose centre lies within OCR_TITLE_PROXIMITY_PIXELS of
    #     the title bbox are treated as OCR fragments of the title and skipped
    #   • a box past the geometry thresholds (area/width fraction) is
    #     significant no matter what the recognizer read out of it — garbled
    #     reads of big text ("70MM" -> "mm") must not slip the word rule
    prox = pipeline_settings.OCR_TITLE_PROXIMITY_PIXELS
    image_height, image_width = image.shape[0], image.shape[1]
    image_area = float(image_height * image_width)
    min_big_area = (
        pipeline_settings.OCR_RESIDUAL_SIGNIFICANT_AREA_FRACTION * image_area
    )
    min_big_width = (
        pipeline_settings.OCR_RESIDUAL_SIGNIFICANT_WIDTH_FRACTION * image_width
    )
    significant_residual: list[OCRTextBox] = []
    for box in residual:
        xs = [p[0] for p in box.bbox]
        # Geometry significance only trusts confident, geometrically valid
        # detections: the low-confidence strip passes (0.50-0.65) routinely
        # hallucinate big boxes on imagery (truck grilles, ferns), and
        # rotated-frame vertical reads shed garbled title-edge fragments at
        # high confidence — both must stay subject to the word rule.
        box_is_big = (
            box.geometry_valid
            and box.confidence >= pipeline_settings.OCR_CONFIDENCE_THRESHOLD
            and (box.area >= min_big_area or (max(xs) - min(xs)) >= min_big_width)
        )
        # Spatial proximity discount: small artifacts adjacent to the title
        # area are almost always OCR noise from the stylized title typography
        # itself. Big confident boxes near the title are real text (studio
        # branding, directed-by lines) and stay eligible.
        if (
            not box_is_big
            and title_box is not None
            and _bbox_center_distance(box.bbox, title_box.bbox) <= prox
        ):
            continue
        if box_is_big:
            significant_residual.append(box)
            continue
        # Word-level significance: only flag the box if it contains at least
        # one word that is clearly non-title (4+ chars, not all-digit).
        words_in_box = _normalise(box.text).split()
        if any(_is_significant_residual_word(w, all_tokens) for w in words_in_box):
            significant_residual.append(box)

    # Gate on validity, rank on taste: one tagline is normal on official
    # posters and is handled by the text_residual rank penalty. Only reject
    # when the poster is genuinely text-heavy — many significant boxes or a
    # large fraction of the image covered by non-title text.
    #
    # Mode-specific gate logic (design 18 §4):
    #   title_only = current strict (require title, reject residual)
    #   textless   = accept only textless (reject title + residual)
    #   custom     = per-category allow/deny toggles
    text_mode = pipeline_settings.OCR_TEXT_MODE
    significant_area_fraction = (
        sum(box.area for box in significant_residual) / image_area
        if image_area > 0
        else 0.0
    )

    if text_mode == "textless":
        # Accept only if NO title AND no significant residual.
        has_title = title_box is not None
        has_residual = len(significant_residual) > 0
        accepted = not has_title and not has_residual
        reason = None if accepted else ("has_title" if has_title else "text_heavy")

    elif text_mode == "custom":
        # Classify each significant residual box against the allow toggles.
        allow_map = {
            "title": pipeline_settings.OCR_ALLOW_TITLE,
            "director": pipeline_settings.OCR_ALLOW_DIRECTOR,
            "studio": pipeline_settings.OCR_ALLOW_STUDIO,
            "rating": pipeline_settings.OCR_ALLOW_RATING,
            "tagline": pipeline_settings.OCR_ALLOW_TAGLINE,
        }
        denied_boxes: list[OCRTextBox] = []
        for box in significant_residual:
            # Reconstruct the _DetectedBox from the OCRTextBox fields for
            # classification (OCRTextBox is derived from _DetectedBox).
            source = _DetectedBox(
                text=box.text,
                confidence=box.confidence,
                bbox=box.bbox,
                geometry_valid=box.geometry_valid,
            )
            category = classify_text_box(
                source,
                image_w=image_width,
                image_h=image_height,
                title_box=title_box,
                title_tokens=title_tokens,
                director_tokens=director_tokens,
            )
            if not allow_map.get(category, False):
                denied_boxes.append(box)

        title_allowed = pipeline_settings.OCR_ALLOW_TITLE
        denied_count = len(denied_boxes)
        denied_area = sum(b.area for b in denied_boxes) / image_area if image_area > 0 else 0.0

        accepted = (
            denied_count <= pipeline_settings.OCR_MAX_RESIDUAL_BOXES
            and denied_area <= pipeline_settings.OCR_MAX_RESIDUAL_AREA_FRACTION
        )
        reason = None if accepted else "text_heavy"
        # Title gate: if title is denied, require that one is actually present.
        if accepted and not title_allowed and title_box is not None:
            accepted = False
            reason = "has_title"
        # Title requirement: if title is required but absent (and no title box).
        if (
            accepted
            and title_allowed
            and title_box is None
            and pipeline_settings.OCR_REQUIRE_TITLE
        ):
            accepted = False
            reason = "no_title"

    else:
        # title_only (default): unchanged — require title; reject significant
        # residual above the count/area thresholds.
        accepted = (
            len(significant_residual) <= pipeline_settings.OCR_MAX_RESIDUAL_BOXES
            and significant_area_fraction
            <= pipeline_settings.OCR_MAX_RESIDUAL_AREA_FRACTION
        )
        reason = None if accepted else "text_heavy"
        # Title-only target: text that never matches the title (logos, taglines
        # read in isolation) does not make a titled poster.  Same fallback path
        # as no_text — rescued by filter_batch only if nothing titled survives.
        if accepted and title_box is None and pipeline_settings.OCR_REQUIRE_TITLE:
            accepted = False
            reason = "no_title"

    return OCRCandidateResult(
        image_path=path,
        accepted=accepted,
        detected_text=detected_text,
        reason=reason,
        title_bbox=title_box.bbox if title_box else None,
        residual_boxes=residual,
    )


def apply_no_text_fallback(
    results: list[OCRCandidateResult],
) -> list[OCRCandidateResult]:
    """Rescue no_text/no_title posters ONLY when nothing titled survived.

    Textless posters must never compete against titled ones (project target:
    title-only text), but a movie whose every poster defeats OCR should still
    get output rather than an empty run. Apply this over **one movie's** result
    subset — never across a cross-movie batch.
    """
    if not pipeline_settings.OCR_ACCEPT_NO_TEXT:
        return results
    if any(result.accepted for result in results):
        return results
    rescuable = [
        result for result in results if result.reason in ("no_text", "no_title")
    ]
    if not rescuable:
        return results
    logger.warning(
        "OCR FALLBACK | zero titled survivors — rescuing %d textless/"
        "no-title poster(s) as last resort",
        len(rescuable),
    )
    rescued_paths = {result.image_path for result in rescuable}
    return [
        replace(result, accepted=True, reason=f"{result.reason}_fallback")
        if result.image_path in rescued_paths
        else result
        for result in results
    ]


class PosterTextFilter:
    def __init__(
        self,
        title: str,
        *,
        director: str | None = None,
        media_type: str = "movie",
        num_workers: int | None = None,
    ):
        self.title = _normalise(title)
        self.director = _normalise(director) if director else ""
        self.media_type = media_type
        # Explicit argument > OCR_WORKERS env > hardware-profile auto-sizing.
        self.num_workers = num_workers or effective_ocr_workers()
        self.title_tokens = set(self.title.split())
        _add_digit_words(self.title_tokens)
        self.director_tokens = set(self.director.split())

    def filter_batch(
        self,
        paths: list[Path],
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[OCRCandidateResult]:
        """Single-movie batch: every poster matched against this movie's title."""
        if not paths:
            return []
        items = [(path, self.title_tokens, self.director_tokens) for path in paths]
        results = self.run_ocr_batch(
            items, num_workers=self.num_workers, progress=progress
        )
        results = apply_no_text_fallback(results)
        logger.info(
            "OCR complete: %d accepted, %d rejected",
            sum(result.accepted for result in results),
            sum(not result.accepted for result in results),
        )
        return results

    @staticmethod
    def run_ocr_batch(
        items: list[tuple[Path, set[str], set[str]]],
        *,
        num_workers: int | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[OCRCandidateResult]:
        """Run OCR over many ``(path, title_tokens, director_tokens)`` items in
        ONE worker pool, returning per-item results in input order.

        Each worker loads PaddleOCR once and then processes a heterogeneous
        queue, so a cross-movie batch pays the model-load cost a single time
        instead of once per movie. The per-movie no-text fallback is NOT applied
        here — callers run ``apply_no_text_fallback`` over each movie's own
        subset (a movie with zero titled survivors must rescue only its own
        textless posters, never another movie's).
        """
        if not items:
            return []

        context = multiprocessing.get_context("spawn")
        wanted = num_workers or effective_ocr_workers()
        worker_count = min(wanted, len(items))
        omp_threads = effective_ocr_omp_threads(worker_count)
        task_queue = context.Queue()
        result_queue = context.Queue()
        workers = [
            context.Process(
                target=_worker_main,
                # Per-task tokens override these init defaults — pass empty sets.
                args=(task_queue, result_queue, set(), set(), omp_threads),
                name=f"poster-ocr-{index + 1}",
            )
            for index in range(worker_count)
        ]

        try:
            for worker in workers:
                worker.start()
                _register_worker(worker)
            PosterTextFilter._wait_for_workers_ready(result_queue, workers)

            for index, (path, title_tokens, director_tokens) in enumerate(items):
                task_queue.put((index, str(path), title_tokens, director_tokens))
            for _ in workers:
                task_queue.put(None)

            ordered_results: list[OCRCandidateResult | None] = [None] * len(items)
            remaining = len(items)
            while remaining:
                message_type, key, payload = PosterTextFilter._get_worker_message(
                    result_queue,
                    workers,
                )
                if message_type == _WORKER_RESULT:
                    ordered_results[key] = payload
                    remaining -= 1
                    if progress is not None:
                        progress(len(items) - remaining, len(items))
                elif message_type == _WORKER_INIT_ERROR:
                    raise RuntimeError(
                        f"OCR worker {key} failed to initialize: {payload}"
                    )

            PosterTextFilter._join_workers(workers)
            results = [result for result in ordered_results if result is not None]
        finally:
            PosterTextFilter._stop_workers(workers)
            PosterTextFilter._close_queue(task_queue)
            PosterTextFilter._close_queue(result_queue)

        logger.info("OCR batch ran %d image(s) over %d worker(s)", len(results), worker_count)
        return results

    @staticmethod
    def _apply_no_text_fallback(
        results: list[OCRCandidateResult],
    ) -> list[OCRCandidateResult]:
        """Back-compat shim — see module-level ``apply_no_text_fallback``."""
        return apply_no_text_fallback(results)

    @staticmethod
    def _wait_for_workers_ready(result_queue: Any, workers: list[Any]) -> None:
        ready_workers = 0
        while ready_workers < len(workers):
            message_type, key, payload = PosterTextFilter._get_worker_message(
                result_queue,
                workers,
            )
            if message_type == _WORKER_READY:
                ready_workers += 1
            elif message_type == _WORKER_INIT_ERROR:
                raise RuntimeError(f"OCR worker {key} failed to initialize: {payload}")

    @staticmethod
    def _get_worker_message(result_queue: Any, workers: list[Any]) -> tuple[Any, ...]:
        while True:
            try:
                return result_queue.get(timeout=_WORKER_POLL_SECONDS)
            except queue.Empty:
                failed = [
                    worker for worker in workers if worker.exitcode not in (None, 0)
                ]
                if failed:
                    details = ", ".join(
                        f"{worker.name}={worker.exitcode}" for worker in failed
                    )
                    raise RuntimeError(
                        f"OCR worker exited unexpectedly: {details}"
                    ) from None
                if all(worker.exitcode is not None for worker in workers):
                    raise RuntimeError(
                        "OCR workers exited before returning all results"
                    ) from None

    @staticmethod
    def _join_workers(workers: list[Any]) -> None:
        deadline = time.monotonic() + _WORKER_SHUTDOWN_SECONDS
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))

        hung = [worker for worker in workers if worker.is_alive()]
        if hung:
            logger.warning(
                "Forcing shutdown of unresponsive OCR workers: %s",
                ", ".join(worker.name for worker in hung),
            )
            for worker in hung:
                worker.terminate()
            for worker in hung:
                worker.join()
            details = ", ".join(
                f"{worker.name}=pid:{worker.pid} exit:{worker.exitcode}"
                for worker in hung
            )
            raise RuntimeError(f"OCR worker forced shutdown: {details}")

        failed = [
            worker
            for worker in workers
            if worker not in hung and worker.exitcode != 0
        ]
        if failed:
            details = ", ".join(
                f"{worker.name}={worker.exitcode}" for worker in failed
            )
            raise RuntimeError(f"OCR worker shutdown failed: {details}")
        for worker in workers:
            if worker.pid is not None and _pid_alive(worker.pid):
                raise RuntimeError(
                    f"OCR worker survived shutdown: {worker.name}=pid:{worker.pid}"
                )
            _unregister_worker(worker)

    @staticmethod
    def _stop_workers(workers: list[Any]) -> None:
        cleanup_failures = []
        for worker in workers:
            if worker.is_alive():
                logger.warning(
                    "Terminating live OCR worker during cleanup: name=%s pid=%s",
                    worker.name,
                    worker.pid,
                )
                worker.terminate()
        for worker in workers:
            if worker.pid is not None:
                worker.join()
                if _pid_alive(worker.pid):
                    cleanup_failures.append(f"{worker.name}=pid:{worker.pid}")
                else:
                    _unregister_worker(worker)
        if cleanup_failures:
            raise RuntimeError(
                "OCR worker cleanup failed: " + ", ".join(cleanup_failures)
            )

    @staticmethod
    def _close_queue(worker_queue: Any) -> None:
        worker_queue.close()
        worker_queue.join_thread()

    def is_acceptable(self, path: Path) -> OCRCandidateResult:
        _init_worker(self.title_tokens, self.director_tokens)
        return _process_image(str(path), self.title_tokens, self.director_tokens)
