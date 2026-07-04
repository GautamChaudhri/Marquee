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
from typing import TYPE_CHECKING, Any, NoReturn

import numpy as np
from PIL import Image

from marquee.core.pipeline_config import pipeline_settings

if TYPE_CHECKING:
    from marquee.core.text_profiles import TextProfile
from marquee.ml.hardware import effective_ocr_omp_threads, effective_ocr_workers
from marquee.pipeline.types import BoundingBox, OCRCandidateResult, OCRTextBox


@dataclass
class OcrPool:
    """Handle to a running pool of PaddleOCR worker processes.

    Created by ``PosterTextFilter.start_ocr_pool()`` — workers are already
    loaded and ready to accept tasks.  Feed them with ``run_ocr_tasks()``
    and shut down with ``stop_ocr_pool()``.
    """

    workers: list[Any]
    task_queue: Any
    result_queue: Any

    @property
    def worker_count(self) -> int:
        return len(self.workers)


logger = logging.getLogger(__name__)

FORMAT_BLOCKLIST: frozenset[str] = frozenset(
    {
        "4k",
        "uhd",
        "hdr",
        "bluray",
        "blu",
        "ray",
        "dolby",
        "atmos",
        "imax",
        "dts",
        "hevc",
        "remux",
        "1080p",
        "2160p",
        "720p",
        "ultra",
        "cinerama",
        "panavision",
        "metrocolor",
    }
)
STUDIO_KEYWORDS: frozenset[str] = frozenset(
    {
        "paramount",
        "warner",
        "bros",
        "disney",
        "universal",
        "sony",
        "columbia",
        "lionsgate",
        "mgm",
        "netflix",
        "a24",
        "focus",
        "features",
        "dreamworks",
        "pixar",
        "searchlight",
        "miramax",
        "orion",
        "touchstone",
        "blumhouse",
        "legendary",
    }
)
TOP_STRIP_FRACTION = 0.18
_DIGIT_WORDS = {
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}

_worker_ocr = None
_worker_title_text = ""
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


@dataclass(frozen=True)
class _TitleEvidence:
    source: str
    group_text: str
    is_fragment: bool


def _compact_text(text: str) -> str:
    return _normalise(text).replace(" ", "")


def _bbox_bounds(bbox: BoundingBox) -> tuple[float, float, float, float]:
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_width(bbox: BoundingBox) -> float:
    left, _, right, _ = _bbox_bounds(bbox)
    return right - left


def _bbox_height(bbox: BoundingBox) -> float:
    _, top, _, bottom = _bbox_bounds(bbox)
    return bottom - top


def _bbox_union(boxes: list[_DetectedBox]) -> BoundingBox:
    left = min(min(p[0] for p in box.bbox) for box in boxes)
    top = min(min(p[1] for p in box.bbox) for box in boxes)
    right = max(max(p[0] for p in box.bbox) for box in boxes)
    bottom = max(max(p[1] for p in box.bbox) for box in boxes)
    return ((left, top), (right, top), (right, bottom), (left, bottom))


def _box_center(box: _DetectedBox) -> tuple[float, float]:
    return sum(p[0] for p in box.bbox) / 4, sum(p[1] for p in box.bbox) / 4


def _reading_order(box: _DetectedBox) -> tuple[float, float]:
    x_center, y_center = _box_center(box)
    return y_center, x_center


def _is_ordered_subsequence(needle: str, haystack: str) -> bool:
    if not needle:
        return False
    position = 0
    for char in needle:
        position = haystack.find(char, position)
        if position < 0:
            return False
        position += 1
    return True


def _compact_matches_title(
    compact: str,
    *,
    title_compact: str,
    title_token_compacts: set[str],
    allow_letter_cluster: bool = False,
) -> bool:
    if len(compact) < 2 or (not title_compact and not title_token_compacts):
        return False
    if compact == title_compact or compact in title_token_compacts:
        return True
    if len(compact) >= 3 and title_compact and compact in title_compact:
        return True

    fuzzy_cutoff = max(0.72, pipeline_settings.OCR_FUZZY_CUTOFF)
    for token in title_token_compacts:
        if len(token) < 3:
            continue
        if len(compact) >= 3 and len(compact) <= len(token) and compact in token:
            return True
        if (
            len(compact) >= 3
            and 0.40 <= len(compact) / len(token) <= 1.10
            and difflib.SequenceMatcher(None, compact, token).ratio() >= fuzzy_cutoff
        ):
            return True
        if (
            len(compact) >= 4
            and len(compact) / len(token) >= 0.55
            and len(compact) <= len(token)
            and _is_ordered_subsequence(compact, token)
        ):
            return True

    if title_compact and len(compact) >= 4:
        if (
            0.35 <= len(compact) / len(title_compact) <= 1.15
            and difflib.SequenceMatcher(None, compact, title_compact).ratio() >= fuzzy_cutoff
        ):
            return True
        if len(compact) / len(title_compact) >= 0.35 and _is_ordered_subsequence(
            compact, title_compact
        ):
            return True

    if allow_letter_cluster:
        haystacks = {title_compact, *title_token_compacts}
        for haystack in haystacks:
            if len(compact) < 3 or len(haystack) < 3:
                continue
            if len(compact) / len(haystack) < 0.50:
                continue
            if len(compact) > len(haystack) + 1:
                continue
            if _is_ordered_subsequence(compact, haystack):
                return True
            if len(compact) / len(haystack) >= 0.75 and difflib.SequenceMatcher(
                None, compact, haystack
            ).ratio() >= max(0.80, fuzzy_cutoff):
                return True
    return False


def _same_line_groups(boxes: list[_DetectedBox], image_height: int) -> list[list[_DetectedBox]]:
    groups: list[list[_DetectedBox]] = []
    for box in sorted((b for b in boxes if b.geometry_valid), key=_reading_order):
        _, y_center = _box_center(box)
        height = max(1.0, _bbox_height(box.bbox))
        for group in groups:
            centers = [_box_center(existing)[1] for existing in group]
            heights = [max(1.0, _bbox_height(existing.bbox)) for existing in group]
            group_center = sum(centers) / len(centers)
            threshold = max(image_height * 0.018, max([height, *heights]) * 0.70)
            if abs(y_center - group_center) <= threshold:
                group.append(box)
                break
        else:
            groups.append([box])
    return groups


# Spaced/stylized title bands. PaddleOCR splits a widely letter-spaced title
# ("A L I E N", big red "R U N") into one box per glyph. Those per-letter boxes
# rarely re-assemble through text matching, and left as residuals each large
# glyph trips the geometry-significance rule (big_area) — so a single stray
# title letter rejects the poster, or no title forms at all. _letter_band_evidence
# folds a wide, tall row (or a stacked pair of rows) of short high-confidence
# boxes into the title when their letters cover a title token. Coverage is
# order-independent, so it tolerates the scrambled reading order and misreads
# ("3"/"7"/"P") that defeat the sequence matchers above.
_BAND_MIN_BOXES = 3
_BAND_MIN_WIDTH_FRACTION = 0.18
_BAND_MIN_GLYPH_HEIGHT_FRACTION = 0.035
_BAND_MAX_MEMBER_CHARS = 4
_BAND_LETTER_COVERAGE = 0.55
_BAND_LETTER_PRECISION = 0.55
# High-precision rescue: when nearly every large glyph in the band belongs to the
# title (taglines mix in off-title letters and miss this bar), a band that only
# covers part of a heavily-misread title is still unmistakably the title.
_BAND_HIGH_PRECISION = 0.80
_BAND_HIGH_PRECISION_COVERAGE = 0.40
_BAND_MERGE_GAP_RATIO = 1.2
_TOP_BILLING_MIN_BOXES = 3
_TOP_BILLING_MIN_WIDTH_FRACTION = 0.28
_TOP_BILLING_MAX_HEIGHT_FRACTION = 0.055
_TOP_BILLING_MAX_HEIGHT_RATIO = 1.8


def _band_letters(boxes: list[_DetectedBox]) -> set[str]:
    return {ch for box in boxes for ch in _compact_text(box.text) if ch.isalpha()}


def _coverage_precision(letters: set[str], target: str) -> tuple[float, float]:
    """Order-independent overlap of *letters* with the alphabetic letters of
    *target*. Returns (coverage, precision): how much of the title the band
    covers, and how much of the band belongs to the title."""
    target_letters = {ch for ch in target if ch.isalpha()}
    if not target_letters or not letters:
        return 0.0, 0.0
    overlap = letters & target_letters
    return len(overlap) / len(target_letters), len(overlap) / len(letters)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _letter_band_evidence(
    boxes: list[_DetectedBox],
    *,
    title_compact: str,
    title_token_compacts: set[str],
    image_width: int,
    image_height: int,
) -> dict[int, _TitleEvidence]:
    conf_thr = pipeline_settings.OCR_CONFIDENCE_THRESHOLD
    members = [
        box
        for box in boxes
        if box.geometry_valid
        and box.confidence >= conf_thr
        and 1 <= len(_compact_text(box.text)) <= _BAND_MAX_MEMBER_CHARS
    ]
    if len(members) < _BAND_MIN_BOXES:
        return {}
    targets = [t for t in (title_compact, *title_token_compacts) if len(t) >= 2]
    if not targets:
        return {}

    groups = _same_line_groups(members, image_height)
    groups.sort(key=lambda g: sum(_box_center(b)[1] for b in g) / len(g))

    # Candidate bands: each line on its own, plus runs of vertically adjacent
    # lines (a stacked title like "ALIEN" over "ROMULUS"). Only merge lines whose
    # vertical gap is within ~1 glyph height so the title never fuses with a
    # distant credits block.
    candidates: list[list[_DetectedBox]] = list(groups)
    for i in range(len(groups) - 1):
        run = list(groups[i])
        for j in range(i + 1, len(groups)):
            run_bottom = _bbox_bounds(_bbox_union(run))[3]
            next_top = _bbox_bounds(_bbox_union(groups[j]))[1]
            median_h = _median([_bbox_height(b.bbox) for b in (*run, *groups[j])])
            if next_top - run_bottom > _BAND_MERGE_GAP_RATIO * median_h:
                break
            run = [*run, *groups[j]]
            candidates.append(list(run))

    evidence: dict[int, _TitleEvidence] = {}
    for band in candidates:
        if len(band) < _BAND_MIN_BOXES:
            continue
        if _bbox_width(_bbox_union(band)) < image_width * _BAND_MIN_WIDTH_FRACTION:
            continue
        if _median([_bbox_height(b.bbox) for b in band]) < (
            image_height * _BAND_MIN_GLYPH_HEIGHT_FRACTION
        ):
            continue
        letters = _band_letters(band)
        if not letters:
            continue
        coverage, precision = max(
            (_coverage_precision(letters, t) for t in targets), key=lambda cp: cp[0]
        )
        strong = coverage >= _BAND_LETTER_COVERAGE and precision >= _BAND_LETTER_PRECISION
        high_precision = (
            precision >= _BAND_HIGH_PRECISION and coverage >= _BAND_HIGH_PRECISION_COVERAGE
        )
        if strong or high_precision:
            group_text = " ".join(b.text for b in sorted(band, key=lambda b: _box_center(b)[0]))
            for box in band:
                evidence.setdefault(
                    id(box),
                    _TitleEvidence(source="letter_band", group_text=group_text, is_fragment=True),
                )
    return evidence


def _find_title_evidence(
    boxes: list[_DetectedBox],
    *,
    title_text: str,
    title_tokens: set[str],
    image_width: int,
    image_height: int,
) -> dict[int, _TitleEvidence]:
    title_compact = _compact_text(title_text)
    token_compacts = {_compact_text(token) for token in title_tokens if _compact_text(token)}
    evidence: dict[int, _TitleEvidence] = {}

    for box in boxes:
        compact = _compact_text(box.text)
        if _matches_allowed(box.text, title_tokens) or _compact_matches_title(
            compact,
            title_compact=title_compact,
            title_token_compacts=token_compacts,
        ):
            evidence[id(box)] = _TitleEvidence(
                source="box",
                group_text=box.text,
                is_fragment=not _matches_allowed(box.text, title_tokens),
            )

    for group in _same_line_groups(boxes, image_height):
        ordered = sorted(group, key=lambda box: _box_center(box)[0])
        for start in range(len(ordered)):
            stop_limit = min(len(ordered), start + 8)
            for stop in range(start + 2, stop_limit + 1):
                window = ordered[start:stop]
                compact_parts = [_compact_text(box.text) for box in window]
                compact = "".join(compact_parts)
                if not compact:
                    continue
                all_short = all(len(part) <= 2 for part in compact_parts if part)
                window_width = _bbox_width(_bbox_union(window))
                allow_letter_cluster = all_short and window_width >= image_width * 0.18
                if not _compact_matches_title(
                    compact,
                    title_compact=title_compact,
                    title_token_compacts=token_compacts,
                    allow_letter_cluster=allow_letter_cluster,
                ):
                    continue
                group_text = " ".join(box.text for box in window)
                for box in window:
                    evidence[id(box)] = _TitleEvidence(
                        source="line_window",
                        group_text=group_text,
                        is_fragment=True,
                    )

    # Spaced-title band: absorb whole rows of large glyphs that cover a title
    # token but never matched as text. Existing per-box / line-window matches win.
    for box_id, band_evidence in _letter_band_evidence(
        boxes,
        title_compact=title_compact,
        title_token_compacts=token_compacts,
        image_width=image_width,
        image_height=image_height,
    ).items():
        evidence.setdefault(box_id, band_evidence)
    return evidence


def _detect_top_billing_bands(
    boxes: list[_DetectedBox],
    *,
    image_width: int,
    image_height: int,
) -> set[int]:
    candidates = [
        box
        for box in boxes
        if box.geometry_valid
        and box.confidence >= pipeline_settings.OCR_STRIP_CONFIDENCE_THRESHOLD
        and _box_center(box)[1] <= image_height * TOP_STRIP_FRACTION
        and _compact_text(box.text)
    ]
    if len(candidates) < _TOP_BILLING_MIN_BOXES:
        return set()

    billing_ids: set[int] = set()
    for group in _same_line_groups(candidates, image_height):
        meaningful = [
            box for box in group if not (set(_normalise(box.text).split()) & FORMAT_BLOCKLIST)
        ]
        if len(meaningful) < _TOP_BILLING_MIN_BOXES:
            continue
        union = _bbox_union(meaningful)
        if _bbox_width(union) < image_width * _TOP_BILLING_MIN_WIDTH_FRACTION:
            continue
        heights = [max(1.0, _bbox_height(box.bbox)) for box in meaningful]
        if _median(heights) > image_height * _TOP_BILLING_MAX_HEIGHT_FRACTION:
            continue
        if max(heights) / min(heights) > _TOP_BILLING_MAX_HEIGHT_RATIO:
            continue
        billing_ids.update(id(box) for box in meaningful)
    return billing_ids


def _synthetic_title_box(
    boxes: list[_DetectedBox],
    *,
    fallback_text: str,
) -> _DetectedBox | None:
    if not boxes:
        return None
    ordered = sorted(boxes, key=_reading_order)
    text = _normalise(" ".join(box.text for box in ordered)) or fallback_text
    confidence = max(box.confidence for box in boxes)
    return _DetectedBox(
        text=text,
        confidence=confidence,
        bbox=_bbox_union(boxes),
        geometry_valid=all(box.geometry_valid for box in boxes),
    )


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


def effective_gate_knobs(profile: dict | None) -> dict:
    """Resolve the text-gate knobs from an injected profile payload.

    A text profile (resolved per movie by the runner and pickled into the OCR
    workers) wins over the raw ``pipeline_settings`` knobs; any key the
    profile omits falls back to the live setting, so built-in presets pin only
    the mode while the advanced knobs keep working.
    """
    prof = profile or {}
    return {
        "mode": prof.get("mode") or pipeline_settings.OCR_TEXT_MODE,
        "allow_map": {
            "title": bool(prof.get("allow_title", pipeline_settings.OCR_ALLOW_TITLE)),
            "director": bool(prof.get("allow_director", pipeline_settings.OCR_ALLOW_DIRECTOR)),
            "studio": bool(prof.get("allow_studio", pipeline_settings.OCR_ALLOW_STUDIO)),
            "rating": bool(prof.get("allow_rating", pipeline_settings.OCR_ALLOW_RATING)),
            "tagline": bool(prof.get("allow_tagline", pipeline_settings.OCR_ALLOW_TAGLINE)),
            "billing": bool(prof.get("allow_billing", pipeline_settings.OCR_ALLOW_BILLING)),
            "season": bool(prof.get("allow_season", pipeline_settings.OCR_ALLOW_SEASON)),
        },
        "max_residual_boxes": int(
            prof.get("max_residual_boxes", pipeline_settings.OCR_MAX_RESIDUAL_BOXES)
        ),
        "max_residual_area_fraction": float(
            prof.get(
                "max_residual_area_fraction",
                pipeline_settings.OCR_MAX_RESIDUAL_AREA_FRACTION,
            )
        ),
        "require_title": bool(prof.get("require_title", pipeline_settings.OCR_REQUIRE_TITLE)),
    }


_SEASON_PATTERNS = [
    re.compile(r"^season\s*\d{1,3}$"),
    re.compile(r"^s\d{1,2}$"),
    re.compile(r"^\d{1,2}(?:st|nd|rd|th)?\s+season$"),
    re.compile(r"^(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+season$"),
    re.compile(r"^season\s+(?:one|two|three|four|five|six|seven|eight|nine|ten)$"),
    re.compile(r"^(?:the\s+)?(?:final|last)\s+season$"),
    re.compile(r"^part\s+(?:\d{1,2}|one|two|three|four|five|six)$"),
    re.compile(r"^vol(?:ume)?\s*\d{1,2}$"),
    re.compile(r"^book\s+\w+$"),
    re.compile(r"^chapter\s+\w+$"),
    re.compile(r"^[ivxlc]{1,4}$"),
    re.compile(r"^\d{1,2}$"),
]


def _is_season_text(text: str) -> bool:
    return any(pattern.match(text) for pattern in _SEASON_PATTERNS)


def classify_text_box(
    box: _DetectedBox,
    *,
    image_w: int,
    image_h: int,
    title_box: _DetectedBox | None,
    title_tokens: set[str],
    director_tokens: set[str],
    studio_tokens: set[str] | None = None,
    tagline_text: str | None = None,
    billing_band_ids: set[int] | None = None,
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

    if billing_band_ids and id(box) in billing_band_ids:
        return "billing"

    # Director: regex match OR word match against director_tokens.
    if re.search(r"directed\s+by", box.text, re.IGNORECASE):
        return "director"
    if director_tokens and _contains_director_tokens(box.text, director_tokens):
        return "director"

    # Rating: MPAA-ish certification marks.
    if _RATING_PATTERN.search(text):
        return "rating"

    # Studio: any word in the known studio keyword set.
    if words & STUDIO_KEYWORDS:
        return "studio"
    if studio_tokens and _matches_allowed(box.text, studio_tokens):
        return "studio"

    if _is_season_text(text):
        return "season"

    compact_tagline = _compact_text(tagline_text or "")
    compact_text = _compact_text(box.text)
    if (
        compact_tagline
        and compact_text
        and difflib.SequenceMatcher(None, compact_text, compact_tagline).ratio() >= 0.70
    ):
        return "tagline"

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


def _contains_director_tokens(text: str, director_tokens: set[str]) -> bool:
    words = _normalise(text).split()
    if not words or not director_tokens:
        return False
    tokens = {token for token in director_tokens if len(token) > 2} or set(director_tokens)
    matched = sum(
        1 for token in tokens if any(_word_matches_token(word, {token}) for word in words)
    )
    required = max(1, (len(tokens) + 1) // 2)
    return len(words) <= len(tokens) + 5 and matched >= required


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


def _residual_significance(
    box: _DetectedBox | OCRTextBox,
    *,
    title_box: _DetectedBox | None,
    min_big_area: float,
    min_big_width: float,
    prox: float,
    all_tokens: set[str],
) -> tuple[bool, str | None, bool]:
    """Decide whether one residual (non-title) box is "significant" text.

    Returns ``(is_significant, reason, proximity_discounted)`` where reason is
    ``big_area`` / ``big_width`` / ``word_level`` / ``None``. This is the single
    source of truth for the residual-significance rule — used both by the live
    gate decision and by the OCR trace builder so they can never drift.
    """
    xs = [p[0] for p in box.bbox]
    area = _polygon_area(box.bbox)
    # A lone glyph is title/design typography, never a promotional text block, so
    # it must not be "significant" on size alone — this is what spaced titles
    # ("A L I E N", "R U N") shed when a stray letter escapes the title band.
    # Multi-char garble ("mm" from "70MM", "4K") stays subject to the geometry
    # rule so genuine format/junk text is still caught.
    box_is_big = (
        len(_compact_text(box.text)) >= 2
        and box.geometry_valid
        and box.confidence >= pipeline_settings.OCR_CONFIDENCE_THRESHOLD
        and (area >= min_big_area or (max(xs) - min(xs)) >= min_big_width)
    )
    if (
        not box_is_big
        and title_box is not None
        and _bbox_center_distance(box.bbox, title_box.bbox) <= prox
    ):
        return False, None, True
    if box_is_big:
        return True, ("big_area" if area >= min_big_area else "big_width"), False
    words_in_box = _normalise(box.text).split()
    if any(_is_significant_residual_word(w, all_tokens) for w in words_in_box):
        return True, "word_level", False
    return False, None, False


def _build_detected_boxes_trace(
    boxes: list[_DetectedBox],
    *,
    title_box: _DetectedBox | None,
    title_evidence: dict[int, _TitleEvidence],
    all_tokens: set[str],
    title_tokens: set[str],
    director_tokens: set[str],
    studio_tokens: set[str] | None,
    tagline_text: str | None,
    billing_band_ids: set[int] | None,
    image_width: int,
    image_height: int,
    min_big_area: float,
    min_big_width: float,
    prox: float,
    pass_by_id: dict[int, str],
) -> list[dict]:
    """Per-box trace: what OCR read, how it was classified, and the significance
    decision for residual boxes. Read-only — never changes the gate outcome."""
    out: list[dict] = []
    for box in boxes:
        evidence = title_evidence.get(id(box))
        is_title = evidence is not None
        is_residual = not is_title
        category = (
            "title"
            if is_title
            else classify_text_box(
                box,
                image_w=image_width,
                image_h=image_height,
                title_box=title_box,
                title_tokens=title_tokens,
                director_tokens=director_tokens,
                studio_tokens=studio_tokens,
                tagline_text=tagline_text,
                billing_band_ids=billing_band_ids,
            )
        )
        if is_residual:
            is_sig, sig_reason, discounted = _residual_significance(
                box,
                title_box=title_box,
                min_big_area=min_big_area,
                min_big_width=min_big_width,
                prox=prox,
                all_tokens=all_tokens,
            )
        else:
            is_sig, sig_reason, discounted = False, None, False
        out.append(
            {
                "text": box.text,
                "confidence": float(box.confidence),
                "bbox": [[float(x), float(y)] for x, y in box.bbox],
                "area": _polygon_area(box.bbox),
                "geometry_valid": bool(box.geometry_valid),
                "pass": pass_by_id.get(id(box), "full"),
                "category": category,
                "is_title": is_title,
                "is_title_fragment": bool(evidence and evidence.is_fragment),
                "title_match_source": evidence.source if evidence else None,
                "title_group_text": evidence.group_text if evidence else None,
                "is_residual": is_residual,
                "is_significant": is_sig,
                "significant_reason": sig_reason,
                "proximity_discounted": discounted,
            }
        )
    return out


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
                (difflib.SequenceMatcher(None, word, token).ratio() for token in title_tokens),
                default=0.0,
            )
        )
    return sum(scores) / len(scores)


def _init_worker(
    title_tokens: set[str],
    director_tokens: set[str],
    title_text: str = "",
) -> None:
    global _worker_ocr, _worker_title_text, _worker_title_tokens, _worker_director_tokens
    _worker_title_text = _normalise(title_text) if title_text else " ".join(sorted(title_tokens))
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
        task_extras: dict | None = None
        if len(task) == 6:
            (
                index,
                path_string,
                task_title_text,
                task_title_tokens,
                task_director_tokens,
                task_extras,
            ) = task
        elif len(task) == 5:
            index, path_string, task_title_text, task_title_tokens, task_director_tokens = task
        else:
            index, path_string, task_title_tokens, task_director_tokens = task
            task_title_text = " ".join(sorted(task_title_tokens))
        result_queue.put(
            (
                _WORKER_RESULT,
                index,
                _process_image(
                    path_string,
                    task_title_tokens,
                    task_director_tokens,
                    title_text=task_title_text,
                    extras=task_extras,
                ),
            )
        )

    task_queue.cancel_join_thread()
    _exit_worker(result_queue, 0)


def _process_image(
    path_string: str,
    title_tokens: set[str] | None = None,
    director_tokens: set[str] | None = None,
    *,
    title_text: str | None = None,
    extras: dict | None = None,
) -> OCRCandidateResult:
    """Detect text and decide accept/reject against this image's own title.

    Title/director tokens are passed per call so a single worker pool can serve
    many movies in one batch — the expensive PaddleOCR model load stays
    once-per-worker while the cheap title matching varies per task. When omitted
    they fall back to the worker-global tokens (the single-movie / test path).

    ``extras`` carries the optional per-movie text-gate context: a resolved
    text profile dict (``TextProfileSettings`` as a plain dict — must survive
    pickling into worker processes). When absent, ``_decide`` falls back to the
    raw ``pipeline_settings`` knobs.
    """
    profile: dict | None = (extras or {}).get("profile") or None
    studio_tokens = set((extras or {}).get("studio_tokens") or ())
    tagline_text = (extras or {}).get("tagline") or None
    using_worker_title_text = title_tokens is None
    if title_tokens is None:
        title_tokens = _worker_title_tokens
    if director_tokens is None:
        director_tokens = _worker_director_tokens
    if title_text is None:
        title_text = (
            _worker_title_text if using_worker_title_text else " ".join(sorted(title_tokens))
        )
    title_text = _normalise(title_text) if title_text else " ".join(sorted(title_tokens))
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
                Image.fromarray(image[height - strip_rows :]).resize(
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

    # Track which detection pass each surviving box came from (object identity
    # is preserved through _dedupe_boxes) so the trace can attribute every read.
    image_height, image_width = int(image.shape[0]), int(image.shape[1])
    image_area = float(image_height * image_width)
    min_big_area = pipeline_settings.OCR_RESIDUAL_SIGNIFICANT_AREA_FRACTION * image_area
    min_big_width = pipeline_settings.OCR_RESIDUAL_SIGNIFICANT_WIDTH_FRACTION * image_width
    prox = pipeline_settings.OCR_TITLE_PROXIMITY_PIXELS
    pass_by_id: dict[int, str] = {}
    for _b in full:
        pass_by_id.setdefault(id(_b), "full")
    for _b in top:
        pass_by_id.setdefault(id(_b), "top")
    for _b in bottom:
        pass_by_id.setdefault(id(_b), "bottom")
    passes_run = ["full"] + (["top", "bottom"] if pipeline_settings.OCR_DETAIL_PASSES else [])
    retry_triggered = False

    boxes = _dedupe_boxes(full + top + bottom)
    detected_text = _normalise(" ".join(box.text for box in boxes))

    # RC-1 fix: before giving up, retry on a contrast-enhanced image.
    # Many stylized title fonts (embossed, low-contrast, metallic) are
    # invisible to the base model but emerge after a 2× contrast boost.
    if not detected_text and pipeline_settings.OCR_ENHANCE_RETRY:
        retry_triggered = True
        passes_run.append("enhance_retry")
        enhanced = _enhance_contrast(image)
        retry_boxes = _detect_boxes(
            _worker_ocr,
            enhanced,
            pipeline_settings.OCR_CONFIDENCE_THRESHOLD,
        )
        if retry_boxes:
            boxes = _dedupe_boxes(retry_boxes)
            for _b in boxes:
                pass_by_id[id(_b)] = "retry"
            detected_text = _normalise(" ".join(box.text for box in boxes))

    title_recovery_triggered = False
    title_recovery_recovered = False
    title_recovery_error: str | None = None

    def _build_state() -> tuple[
        dict[int, _TitleEvidence],
        _DetectedBox | None,
        list[OCRTextBox],
        list[OCRTextBox],
        float,
        set[int],
    ]:
        title_evidence = _find_title_evidence(
            boxes,
            title_text=title_text or "",
            title_tokens=title_tokens,
            image_width=image_width,
            image_height=image_height,
        )
        billing_band_ids = _detect_top_billing_bands(
            boxes,
            image_width=image_width,
            image_height=image_height,
        )
        title_boxes = [box for box in boxes if id(box) in title_evidence]
        title_box = _synthetic_title_box(
            title_boxes,
            fallback_text=title_text or " ".join(sorted(title_tokens)),
        )
        all_tokens = title_tokens | director_tokens
        residual = [
            OCRTextBox(
                text=box.text,
                confidence=box.confidence,
                bbox=box.bbox,
                area=_polygon_area(box.bbox),
                geometry_valid=box.geometry_valid,
            )
            for box in boxes
            if id(box) not in title_evidence
        ]

        # RC-2 + RC-3 + RC-4 fix: evaluate significance at the *word* level, not
        # the box level.  A box that mixes title words with noise fragments (e.g.
        # "1917 ll6l") used to fail the box-level _matches_allowed check entirely,
        # causing the title words inside to trigger significant_residual.  Now:
        #   • title-evidence fragments are removed from residuals first
        #   • each residual word is individually classified against tokens
        #   • pure-digit strings (catalog numbers, scan labels) are not significant
        #   • SMALL boxes whose centre lies within OCR_TITLE_PROXIMITY_PIXELS of
        #     the title bbox are treated as OCR fragments of the title and skipped
        #   • a box past the geometry thresholds (area/width fraction) is
        #     significant no matter what the recognizer read out of it — garbled
        #     reads of big text ("70MM" -> "mm") must not slip the word rule
        # Geometry significance only trusts confident, geometrically valid
        # detections: the low-confidence strip passes (0.50-0.65) routinely
        # hallucinate big boxes on imagery (truck grilles, ferns), and rotated-frame
        # vertical reads shed garbled title-edge fragments at high confidence — both
        # must stay subject to the word rule. Small artifacts within
        # OCR_TITLE_PROXIMITY_PIXELS of the title are discounted as title noise. The
        # rule lives in _residual_significance (shared with the trace builder).
        significant_residual = [
            box
            for box in residual
            if _residual_significance(
                box,
                title_box=title_box,
                min_big_area=min_big_area,
                min_big_width=min_big_width,
                prox=prox,
                all_tokens=all_tokens,
            )[0]
        ]
        significant_area_fraction = (
            sum(box.area for box in significant_residual) / image_area if image_area > 0 else 0.0
        )
        return (
            title_evidence,
            title_box,
            residual,
            significant_residual,
            significant_area_fraction,
            billing_band_ids,
        )

    def _decide(
        *,
        title_box: _DetectedBox | None,
        significant_residual: list[OCRTextBox],
        significant_area_fraction: float,
        detected_text_current: str,
    ) -> dict:
        knobs = effective_gate_knobs(profile)
        text_mode = knobs["mode"]
        allow_title = knobs["allow_map"]["title"]
        max_residual_boxes = knobs["max_residual_boxes"]
        max_residual_area = knobs["max_residual_area_fraction"]
        require_title = knobs["require_title"]
        if not detected_text_current:
            accepted = False
            reason = "no_text"
        elif set(detected_text_current.split()) & FORMAT_BLOCKLIST:
            accepted = False
            reason = "format_blocklist"
        elif text_mode == "textless":
            # Accept only if NO title AND no significant residual.
            has_title = title_box is not None
            has_residual = len(significant_residual) > 0
            accepted = not has_title and not has_residual
            reason = None if accepted else ("has_title" if has_title else "text_heavy")
        elif text_mode == "custom":
            # Classify each significant residual box against the allow toggles.
            allow_map = knobs["allow_map"]
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
                    studio_tokens=studio_tokens,
                    tagline_text=tagline_text,
                    billing_band_ids=billing_band_ids,
                )
                if not allow_map.get(category, False):
                    denied_boxes.append(box)

            denied_count = len(denied_boxes)
            denied_area = sum(b.area for b in denied_boxes) / image_area if image_area > 0 else 0.0

            accepted = denied_count <= max_residual_boxes and denied_area <= max_residual_area
            reason = None if accepted else "text_heavy"
            # Title gate: if title is denied, require that one is actually present.
            if accepted and not allow_title and title_box is not None:
                accepted = False
                reason = "has_title"
            # Title requirement: if title is required but absent (and no title box).
            if accepted and allow_title and title_box is None and require_title:
                accepted = False
                reason = "no_title"
        else:
            # title_only (default): require title; reject significant residual
            # above the count/area thresholds.
            accepted = (
                len(significant_residual) <= max_residual_boxes
                and significant_area_fraction <= max_residual_area
            )
            reason = None if accepted else "text_heavy"
            # Title-only target: text that never matches the title (logos, taglines
            # read in isolation) does not make a titled poster. The fallback remains
            # opt-in via OCR_ACCEPT_NO_TEXT and is disabled by default.
            if accepted and title_box is None and require_title:
                accepted = False
                reason = "no_title"

        return {
            "mode": text_mode,
            "accepted": accepted,
            "reason": reason,
            "has_text": bool(detected_text_current),
            "has_title": title_box is not None,
            "require_title": require_title,
            "significant_residual_count": len(significant_residual),
            "max_residual_boxes": max_residual_boxes,
            "significant_area_fraction": significant_area_fraction,
            "max_residual_area_fraction": max_residual_area,
        }

    def _trace(
        *,
        title_box: _DetectedBox | None,
        title_evidence: dict[int, _TitleEvidence],
        decision: dict,
    ) -> dict:
        """Assemble the JSON-native OCR trace from the current detection state."""
        title_fragments = [
            {
                "text": box.text,
                "bbox": [[float(x), float(y)] for x, y in box.bbox],
                "source": evidence.source,
                "group_text": evidence.group_text,
                "is_fragment": evidence.is_fragment,
            }
            for box in boxes
            if (evidence := title_evidence.get(id(box))) is not None
        ]
        return {
            "image_size": {"width": image_width, "height": image_height},
            "passes_run": passes_run,
            "enhance_retry": {
                "triggered": retry_triggered,
                "recovered_text": bool(detected_text),
            },
            "title_recovery": {
                "enabled": pipeline_settings.OCR_TITLE_RECOVERY_ENABLED,
                "triggered": title_recovery_triggered,
                "recovered_title": title_recovery_recovered,
                "confidence_threshold": (pipeline_settings.OCR_TITLE_RECOVERY_CONFIDENCE_THRESHOLD),
                "error": title_recovery_error,
            },
            "detected_boxes": _build_detected_boxes_trace(
                boxes,
                title_box=title_box,
                title_evidence=title_evidence,
                all_tokens=title_tokens | director_tokens,
                title_tokens=title_tokens,
                director_tokens=director_tokens,
                studio_tokens=studio_tokens,
                tagline_text=tagline_text,
                billing_band_ids=billing_band_ids,
                image_width=image_width,
                image_height=image_height,
                min_big_area=min_big_area,
                min_big_width=min_big_width,
                prox=prox,
                pass_by_id=pass_by_id,
            ),
            "title": (
                {
                    "text": title_box.text,
                    "bbox": [[float(x), float(y)] for x, y in title_box.bbox],
                    "match_score": _title_match_score(title_box.text, title_tokens),
                    "fragments": title_fragments,
                }
                if title_box is not None
                else None
            ),
            "decision": decision,
        }

    (
        title_evidence,
        title_box,
        residual,
        significant_residual,
        significant_area_fraction,
        billing_band_ids,
    ) = _build_state()
    decision = _decide(
        title_box=title_box,
        significant_residual=significant_residual,
        significant_area_fraction=significant_area_fraction,
        detected_text_current=detected_text,
    )

    if (
        pipeline_settings.OCR_TITLE_RECOVERY_ENABLED
        and not decision["accepted"]
        and decision["reason"] in {"no_text", "no_title"}
        and not significant_residual
    ):
        title_recovery_triggered = True
        passes_run.append("title_recovery")
        try:
            enhanced = _enhance_contrast(image)
            recovery_image = np.asarray(
                Image.fromarray(enhanced).resize(
                    (image_width * 2, image_height * 2),
                    Image.Resampling.LANCZOS,
                )
            )
            recovery_boxes = _dedupe_boxes(
                _detect_boxes(
                    _worker_ocr,
                    recovery_image,
                    pipeline_settings.OCR_TITLE_RECOVERY_CONFIDENCE_THRESHOLD,
                    scale=2.0,
                )
            )
            recovery_evidence = _find_title_evidence(
                recovery_boxes,
                title_text=title_text or "",
                title_tokens=title_tokens,
                image_width=image_width,
                image_height=image_height,
            )
            recovered_title_boxes = [box for box in recovery_boxes if id(box) in recovery_evidence]
            if recovered_title_boxes:
                title_recovery_recovered = True
                for box in recovered_title_boxes:
                    pass_by_id[id(box)] = "title_recovery"
                boxes = _dedupe_boxes(boxes + recovered_title_boxes)
                detected_text = _normalise(" ".join(box.text for box in boxes))
                (
                    title_evidence,
                    title_box,
                    residual,
                    significant_residual,
                    significant_area_fraction,
                    billing_band_ids,
                ) = _build_state()
                decision = _decide(
                    title_box=title_box,
                    significant_residual=significant_residual,
                    significant_area_fraction=significant_area_fraction,
                    detected_text_current=detected_text,
                )
        except Exception as exc:
            title_recovery_error = f"{type(exc).__name__}: {exc}"
            logger.warning("OCR title recovery failed for %s: %s", path.name, exc)

    return OCRCandidateResult(
        image_path=path,
        accepted=decision["accepted"],
        detected_text=detected_text,
        reason=decision["reason"],
        title_bbox=title_box.bbox if title_box else None,
        residual_boxes=residual,
        diagnostics=_trace(
            title_box=title_box,
            title_evidence=title_evidence,
            decision=decision,
        ),
    )


def apply_no_text_fallback(
    results: list[OCRCandidateResult],
) -> list[OCRCandidateResult]:
    """Rescue no_text/no_title posters ONLY when the opt-in fallback is enabled.

    Textless posters must never compete against titled ones (project target:
    title-only text). The default is to keep no_text/no_title rejected; when
    ``OCR_ACCEPT_NO_TEXT`` is manually enabled, apply this over **one movie's**
    result subset — never across a cross-movie batch.
    """
    if not pipeline_settings.OCR_ACCEPT_NO_TEXT:
        return results
    if any(result.accepted for result in results):
        return results
    rescuable = [result for result in results if result.reason in ("no_text", "no_title")]
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
        studios: list[str] | None = None,
        tagline: str | None = None,
        media_type: str = "movie",
        num_workers: int | None = None,
        profile: TextProfile | None = None,
    ):
        self.title = _normalise(title)
        self.director = _normalise(director) if director else ""
        self.studios = [_normalise(studio) for studio in (studios or []) if _normalise(studio)]
        self.tagline = _normalise(tagline) if tagline else ""
        self.media_type = media_type
        # Explicit argument > OCR_WORKERS env > hardware-profile auto-sizing.
        self.num_workers = num_workers or effective_ocr_workers()
        self.title_tokens = set(self.title.split())
        _add_digit_words(self.title_tokens)
        self.director_tokens = set(self.director.split())
        self.studio_tokens = {token for studio in self.studios for token in studio.split()}
        # Resolved text profile, serialized as a plain dict so it pickles into
        # spawned worker processes. None → _decide uses pipeline_settings.
        self.profile_payload: dict | None = profile.gate_payload() if profile else None

    def task_extras(self) -> dict | None:
        """Per-task text-gate context for run_ocr_tasks item tuples."""
        extras: dict[str, object] = {}
        if self.profile_payload is not None:
            extras["profile"] = self.profile_payload
        if self.studio_tokens:
            extras["studio_tokens"] = self.studio_tokens
        if self.tagline:
            extras["tagline"] = self.tagline
        if not extras:
            return None
        return extras

    def filter_batch(
        self,
        paths: list[Path],
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[OCRCandidateResult]:
        """Single-movie batch: every poster matched against this movie's title."""
        if not paths:
            return []
        extras = self.task_extras()
        items = [
            (path, self.title, self.title_tokens, self.director_tokens, extras) for path in paths
        ]
        results = self.run_ocr_batch(items, num_workers=self.num_workers, progress=progress)
        results = apply_no_text_fallback(results)
        logger.info(
            "OCR complete: %d accepted, %d rejected",
            sum(result.accepted for result in results),
            sum(not result.accepted for result in results),
        )
        return results

    # ── Pool lifecycle (exposed for batch-runner preloading) ──────────────

    @staticmethod
    def start_ocr_pool(num_workers: int | None = None) -> OcrPool:
        """Spawn OCR workers and block until every one has loaded PaddleOCR.

        Call this early (e.g. during the fetch/download phase) so the
        expensive model load overlaps with network I/O.  The returned
        ``OcrPool`` can be passed to ``run_ocr_tasks`` and then
        ``stop_ocr_pool``.
        """
        wanted = num_workers or effective_ocr_workers()
        worker_count = max(1, wanted)
        omp_threads = effective_ocr_omp_threads(worker_count)
        context = multiprocessing.get_context("spawn")
        task_queue = context.Queue()
        result_queue = context.Queue()
        workers = [
            context.Process(
                target=_worker_main,
                args=(task_queue, result_queue, set(), set(), omp_threads),
                name=f"poster-ocr-{idx + 1}",
            )
            for idx in range(worker_count)
        ]
        for worker in workers:
            worker.start()
            _register_worker(worker)
        PosterTextFilter._wait_for_workers_ready(result_queue, workers)
        logger.info("OCR pool started: %d worker(s)", worker_count)
        return OcrPool(workers=workers, task_queue=task_queue, result_queue=result_queue)

    @staticmethod
    def run_ocr_tasks(
        pool: OcrPool,
        items: list[tuple[Path, set[str], set[str]] | tuple[Path, str, set[str], set[str]]],
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[OCRCandidateResult]:
        """Feed *items* to a ready pool and collect per-item results in order.

        The pool must have been created by ``start_ocr_pool`` and its workers
        must still be alive.  After this returns, the pool can be reused for
        another batch or shut down with ``stop_ocr_pool``.
        """
        if not items:
            return []

        for index, item in enumerate(items):
            extras: dict | None = None
            if len(item) == 5:
                path, title_text, title_tokens, director_tokens, extras = item
            elif len(item) == 4:
                path, title_text, title_tokens, director_tokens = item
            else:
                path, title_tokens, director_tokens = item
                title_text = " ".join(sorted(title_tokens))
            pool.task_queue.put(
                (index, str(path), title_text, title_tokens, director_tokens, extras)
            )
        for _ in pool.workers:
            pool.task_queue.put(None)

        ordered_results: list[OCRCandidateResult | None] = [None] * len(items)
        remaining = len(items)
        while remaining:
            message_type, key, payload = PosterTextFilter._get_worker_message(
                pool.result_queue, pool.workers
            )
            if message_type == _WORKER_RESULT:
                ordered_results[key] = payload
                remaining -= 1
                if progress is not None:
                    progress(len(items) - remaining, len(items))
            elif message_type == _WORKER_INIT_ERROR:
                raise RuntimeError(f"OCR worker {key} failed to initialize: {payload}")

        PosterTextFilter._join_workers(pool.workers)
        results = [r for r in ordered_results if r is not None]
        logger.info("OCR tasks ran %d image(s) over %d worker(s)", len(results), pool.worker_count)
        return results

    @staticmethod
    def stop_ocr_pool(pool: OcrPool) -> None:
        """Shut down worker processes and release queues.

        Safe to call from any thread — workers are terminated if they
        haven't exited cleanly. After this the pool is dead.
        """
        PosterTextFilter._stop_workers(pool.workers)
        PosterTextFilter._close_queue(pool.task_queue)
        PosterTextFilter._close_queue(pool.result_queue)
        logger.info("OCR pool stopped: %d worker(s)", pool.worker_count)

    @staticmethod
    def run_ocr_batch(
        items: list[tuple[Path, set[str], set[str]] | tuple[Path, str, set[str], set[str]]],
        *,
        num_workers: int | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[OCRCandidateResult]:
        """Run OCR over many OCR task items in ONE worker pool.

        Each worker loads PaddleOCR once and then processes a heterogeneous
        queue, so a cross-movie batch pays the model-load cost a single time
        instead of once per movie. The per-movie no-text fallback is NOT applied
        here — callers run ``apply_no_text_fallback`` over each movie's own
        subset (a movie with zero titled survivors must rescue only its own
        textless posters, never another movie's).
        """
        if not items:
            return []

        pool = PosterTextFilter.start_ocr_pool(num_workers)
        try:
            return PosterTextFilter.run_ocr_tasks(pool, items, progress=progress)
        finally:
            PosterTextFilter.stop_ocr_pool(pool)

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
                failed = [worker for worker in workers if worker.exitcode not in (None, 0)]
                if failed:
                    details = ", ".join(f"{worker.name}={worker.exitcode}" for worker in failed)
                    raise RuntimeError(f"OCR worker exited unexpectedly: {details}") from None
                if all(worker.exitcode is not None for worker in workers):
                    raise RuntimeError("OCR workers exited before returning all results") from None

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
                f"{worker.name}=pid:{worker.pid} exit:{worker.exitcode}" for worker in hung
            )
            raise RuntimeError(f"OCR worker forced shutdown: {details}")

        failed = [worker for worker in workers if worker not in hung and worker.exitcode != 0]
        if failed:
            details = ", ".join(f"{worker.name}={worker.exitcode}" for worker in failed)
            raise RuntimeError(f"OCR worker shutdown failed: {details}")
        for worker in workers:
            if worker.pid is not None and _pid_alive(worker.pid):
                raise RuntimeError(f"OCR worker survived shutdown: {worker.name}=pid:{worker.pid}")
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
            raise RuntimeError("OCR worker cleanup failed: " + ", ".join(cleanup_failures))

    @staticmethod
    def _close_queue(worker_queue: Any) -> None:
        worker_queue.close()
        worker_queue.join_thread()

    def is_acceptable(self, path: Path) -> OCRCandidateResult:
        _init_worker(self.title_tokens, self.director_tokens, self.title)
        return _process_image(
            str(path),
            self.title_tokens,
            self.director_tokens,
            title_text=self.title,
            extras=self.task_extras(),
        )
