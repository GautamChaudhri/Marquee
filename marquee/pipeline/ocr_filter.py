"""Three-pass PaddleOCR text gate with title and residual box emission."""

from __future__ import annotations

import difflib
import logging
import multiprocessing
import os
import queue
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import numpy as np
from PIL import Image

from marquee.core.pipeline_config import pipeline_settings
from marquee.pipeline.types import BoundingBox, OCRCandidateResult, OCRTextBox

logger = logging.getLogger(__name__)

FORMAT_BLOCKLIST: frozenset[str] = frozenset(
    {
        "4k", "uhd", "hdr", "bluray", "blu", "ray", "dolby", "atmos",
        "imax", "dts", "hevc", "remux", "1080p", "2160p", "720p", "ultra",
        "cinerama", "panavision", "metrocolor",
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


@dataclass(frozen=True)
class _DetectedBox:
    text: str
    confidence: float
    bbox: BoundingBox


def _normalise(text: str) -> str:
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return " ".join(text.split())


def _add_digit_words(tokens: set[str]) -> None:
    for digit, word in _DIGIT_WORDS.items():
        if digit in tokens:
            tokens.add(word)
        if word in tokens:
            tokens.add(digit)


def _load_ocr() -> object:
    from paddleocr import PaddleOCR

    return PaddleOCR(
        use_textline_orientation=True,
        lang="en",
        device="cpu",
        text_det_box_thresh=0.3,
        text_det_unclip_ratio=2.0,
    )


def _polygon_area(bbox: BoundingBox) -> float:
    points = np.asarray(bbox, dtype=np.float32)
    x = points[:, 0]
    y = points[:, 1]
    return float(abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2)


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
    result = ocr.predict(image)
    if not result:
        return []
    first = result[0]
    texts = first.get("rec_texts", [])
    scores = first.get("rec_scores", [])
    polygons = first.get("rec_polys", [])
    boxes: list[_DetectedBox] = []
    for text, score, polygon in zip(texts, scores, polygons, strict=False):
        if float(score) < confidence:
            continue
        boxes.append(
            _DetectedBox(
                text=str(text),
                confidence=float(score),
                bbox=_as_bbox(polygon, scale=scale, y_offset=y_offset),
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
        if word in allowed_tokens:
            continue
        if difflib.get_close_matches(word, allowed_tokens, n=1, cutoff=0.70):
            continue
        if any(token in word for token in allowed_tokens if len(token) >= 3):
            continue
        return False
    return True


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
) -> NoReturn:
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
        index, path_string = task
        result_queue.put((_WORKER_RESULT, index, _process_image(path_string)))

    task_queue.cancel_join_thread()
    _exit_worker(result_queue, 0)


def _process_image(path_string: str) -> OCRCandidateResult:
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
        top = _detect_boxes(
            _worker_ocr,
            image[:strip_rows],
            pipeline_settings.OCR_STRIP_CONFIDENCE_THRESHOLD,
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
    if not detected_text:
        return OCRCandidateResult(path, False, "", "no_text", None)

    all_tokens = _worker_title_tokens | _worker_director_tokens
    words = set(detected_text.split())
    if words & FORMAT_BLOCKLIST:
        return OCRCandidateResult(path, False, detected_text, "format_blocklist", None)

    title_candidates = [
        box for box in boxes if _matches_allowed(box.text, _worker_title_tokens)
    ]
    title_box = max(
        title_candidates,
        key=lambda box: _title_match_score(box.text, _worker_title_tokens),
        default=None,
    )
    residual = [
        OCRTextBox(
            text=box.text,
            confidence=box.confidence,
            bbox=box.bbox,
            area=_polygon_area(box.bbox),
        )
        for box in boxes
        if not _matches_allowed(box.text, all_tokens)
    ]
    significant_residual = [
        box
        for box in residual
        if any(len(word) >= 4 for word in _normalise(box.text).split())
    ]
    accepted = not significant_residual
    return OCRCandidateResult(
        image_path=path,
        accepted=accepted,
        detected_text=detected_text,
        reason=None if accepted else "text_heavy",
        title_bbox=title_box.bbox if title_box else None,
        residual_boxes=residual,
    )


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
        self.num_workers = num_workers or pipeline_settings.OCR_WORKERS
        self.title_tokens = set(self.title.split())
        _add_digit_words(self.title_tokens)
        self.director_tokens = set(self.director.split())

    def filter_batch(self, paths: list[Path]) -> list[OCRCandidateResult]:
        if not paths:
            return []

        context = multiprocessing.get_context("spawn")
        worker_count = min(self.num_workers, len(paths))
        task_queue = context.Queue()
        result_queue = context.Queue()
        workers = [
            context.Process(
                target=_worker_main,
                args=(
                    task_queue,
                    result_queue,
                    self.title_tokens,
                    self.director_tokens,
                ),
                name=f"poster-ocr-{index + 1}",
            )
            for index in range(worker_count)
        ]

        try:
            for worker in workers:
                worker.start()
            self._wait_for_workers_ready(result_queue, workers)

            for index, path in enumerate(paths):
                task_queue.put((index, str(path)))
            for _ in workers:
                task_queue.put(None)

            ordered_results: list[OCRCandidateResult | None] = [None] * len(paths)
            remaining = len(paths)
            while remaining:
                message_type, key, payload = self._get_worker_message(
                    result_queue,
                    workers,
                )
                if message_type == _WORKER_RESULT:
                    ordered_results[key] = payload
                    remaining -= 1
                elif message_type == _WORKER_INIT_ERROR:
                    raise RuntimeError(
                        f"OCR worker {key} failed to initialize: {payload}"
                    )

            self._join_workers(workers)
            results = [result for result in ordered_results if result is not None]
        finally:
            self._stop_workers(workers)
            self._close_queue(task_queue)
            self._close_queue(result_queue)

        logger.info(
            "OCR complete: %d accepted, %d rejected",
            sum(result.accepted for result in results),
            sum(not result.accepted for result in results),
        )
        return results

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

    @staticmethod
    def _stop_workers(workers: list[Any]) -> None:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
        for worker in workers:
            if worker.pid is not None:
                worker.join()

    @staticmethod
    def _close_queue(worker_queue: Any) -> None:
        worker_queue.close()
        worker_queue.join_thread()

    def is_acceptable(self, path: Path) -> OCRCandidateResult:
        _init_worker(self.title_tokens, self.director_tokens)
        return _process_image(str(path))
