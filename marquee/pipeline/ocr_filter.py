"""Poster text filter using PaddleOCR — Stage 3 of the poster pipeline.

Rejects posters that contain text beyond the movie/series title and
optionally the director's name.  This is Marquee's primary differentiator —
most poster tools don't filter out text-heavy promotional posters.

The core algorithm is salvaged from the old project's
``experiments/filtering/filter_posters_ocr.py`` and refactored into a class
with no module-level globals.

Architecture::

    PosterTextFilter(title, director=None, media_type="movie")
        │
        ├── filter_batch(paths)        # multiprocessed batch (pipeline entry)
        │       │
        │       └── [worker processes with per-worker PaddleOCR instance]
        │               │
        │               └── _process_image(path)  → (accepted, text)
        │                       │
        │                       ├── detect_text()          # full image
        │                       ├── detect_text_strip()    # top 18%
        │                       └── detect_text_bottom()   # bottom 18% (2× upscale)
        │
        └── is_acceptable(path)         # single-image (no multiprocessing)

Usage::

    from marquee.pipeline.ocr_filter import PosterTextFilter

    ocr = PosterTextFilter("Dune Part Two")
    accepted, rejected = ocr.filter_batch(candidate_paths)
"""

from __future__ import annotations

import difflib
import logging
import multiprocessing
import re
from pathlib import Path

import numpy as np
from PIL import Image

from marquee.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
)

# Known media/format/distribution terms — any of these on a poster
# means instant rejection regardless of how OCR splits the words.
FORMAT_BLOCKLIST: frozenset[str] = frozenset(
    {
        "4k", "uhd", "hdr", "bluray", "blu", "ray", "dolby", "atmos",
        "imax", "dts", "hevc", "remux", "1080p", "2160p", "720p", "ultra",
        "cinerama", "panavision", "metrocolor",
    }
)

# Fraction of image height for top/bottom strip OCR passes
TOP_STRIP_FRACTION: float = 0.18

# Digit → word mapping so "Part 2" matches "PART TWO"
_DIGIT_WORDS: dict[str, str] = {
    "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}

# ---------------------------------------------------------------------------
# Per-worker process state (set once by _init_worker)
# ---------------------------------------------------------------------------
_worker_ocr = None           # PaddleOCR instance (one per process)
_worker_title_tokens: set[str] = set()
_worker_director_tokens: set[str] = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Lowercase, strip non-alnum, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def _text_is_title_only(detected_text: str) -> bool:
    """Check whether detected text contains only title/director tokens.

    Uses the per-worker globals set by ``_init_worker``.  Returns True
    if the poster is acceptable (text-free or only title/director text).
    """
    global _worker_title_tokens, _worker_director_tokens

    normed = _normalise(detected_text)
    if not normed:
        return True  # text-free poster → accept

    words = set(normed.split())

    # Hard-reject if any format/badge/distribution term is present
    if words & FORMAT_BLOCKLIST:
        return False

    remaining = words - _worker_title_tokens - _worker_director_tokens

    # Remove short words (<4 chars, not digits).  Single-digit artifacts
    # from OCR noise are excluded — FORMAT_BLOCKLIST already catches real
    # format strings like "4k", "1080p".
    remaining = {w for w in remaining if len(w) >= 4}

    allowed_tokens = _worker_title_tokens | _worker_director_tokens

    # Fuzzy match: allow words scoring ≥ 0.70 against any title/director token.
    # Handles garbled reads: "odysse" ≈ "odyssey" (0.92), "game" ≈ "endgame" (0.73).
    remaining = {
        w for w in remaining
        if not difflib.get_close_matches(w, allowed_tokens, n=1, cutoff=0.70)
    }

    # Substring match: allow words that contain a title/director token.
    # Handles OCR concatenations: "parttwo" contains "part".
    remaining = {
        w for w in remaining
        if not any(token in w for token in allowed_tokens if len(token) >= 3)
    }

    return len(remaining) == 0


def _load_ocr() -> object:
    """Create a PaddleOCR instance for this worker process."""
    from paddleocr import PaddleOCR  # noqa: PLC0415 — import per worker

    logger.debug("Worker loading PaddleOCR ...")
    ocr = PaddleOCR(
        use_textline_orientation=True,  # handle rotated/upside-down text
        lang="en",
        device="cpu",                   # no GPU on Mac; CPU uses ARM NEON
        text_det_box_thresh=0.3,        # default 0.6 — lower catches faint/gothic text
        text_det_unclip_ratio=2.0,      # default 1.5 — expand for tight/small text
    )
    logger.debug("Worker PaddleOCR ready.")
    return ocr


# ---------------------------------------------------------------------------
# OCR passes
# ---------------------------------------------------------------------------


def _detect_text(ocr: object, img_array: np.ndarray) -> str:
    """Full-image OCR pass with confidence filtering."""
    conf = settings.OCR_CONFIDENCE_THRESHOLD
    result = ocr.predict(img_array)
    if not result:
        return ""
    texts = [
        text for text, score in zip(
            result[0].get("rec_texts", []),
            result[0].get("rec_scores", []),
        )
        if score >= conf
    ]
    return " ".join(texts)


def _detect_text_strip(ocr: object, img_array: np.ndarray) -> str:
    """OCR pass on a pre-cropped strip (4K/UHD badges, format logos)."""
    conf = settings.OCR_STRIP_CONFIDENCE_THRESHOLD
    result = ocr.predict(img_array)
    if not result or not result[0]:
        return ""
    texts = [
        text for text, score in zip(
            result[0].get("rec_texts", []),
            result[0].get("rec_scores", []),
        )
        if score >= conf
    ]
    return " ".join(texts)


def _detect_text_bottom(ocr: object, img_array: np.ndarray) -> str:
    """OCR pass on the bottom strip for credits blocks and studio logos.

    Upscaled 2× before inference so fine-print credits (6–8pt) become
    large enough to read.  Uses a lower confidence threshold than the
    top strip because credits on complex backgrounds reliably score lower.
    """
    conf = settings.OCR_BOTTOM_CONFIDENCE_THRESHOLD
    h, w = img_array.shape[:2]
    upscaled = np.array(
        Image.fromarray(img_array).resize(
            (w * 2, h * 2), Image.Resampling.LANCZOS,
        )
    )
    result = ocr.predict(upscaled)
    if not result or not result[0]:
        return ""
    texts = [
        text for text, score in zip(
            result[0].get("rec_texts", []),
            result[0].get("rec_scores", []),
        )
        if score >= conf
    ]
    return " ".join(texts)


# ---------------------------------------------------------------------------
# Multiprocessing worker
# ---------------------------------------------------------------------------


def _init_worker(title_tokens: set[str], director_tokens: set[str]) -> None:
    """Called once per worker process.  Creates the PaddleOCR instance
    and sets per-worker token globals for this process."""
    global _worker_ocr, _worker_title_tokens, _worker_director_tokens
    _worker_title_tokens = title_tokens
    _worker_director_tokens = director_tokens
    _worker_ocr = _load_ocr()


def _process_image(img_path_str: str) -> tuple[str, bool | None, str]:
    """Worker function: run all three OCR passes on one image.

    Opens the image once, crops numpy slices in memory for strip passes
    (no temp files).  Returns (filename, accepted, normalised_text) where
    ``accepted=None`` signals an OCR/processing error.
    """
    global _worker_ocr
    if _worker_ocr is None:
        return (Path(img_path_str).name, None, "OCR not initialised")

    img_path = Path(img_path_str)
    try:
        img_array = np.array(Image.open(img_path).convert("RGB"))
        h = img_array.shape[0]
        strip_rows = int(h * TOP_STRIP_FRACTION)

        full_text = _detect_text(_worker_ocr, img_array)
        strip_text = _detect_text_strip(_worker_ocr, img_array[:strip_rows])
        bottom_text = _detect_text_bottom(_worker_ocr, img_array[h - strip_rows:])
    except Exception as exc:
        return (img_path.name, None, str(exc))

    combined = (full_text + " " + strip_text + " " + bottom_text).strip()
    return (img_path.name, _text_is_title_only(combined), _normalise(combined))


# ---------------------------------------------------------------------------
# PosterTextFilter
# ---------------------------------------------------------------------------


class PosterTextFilter:
    """Filter posters by text content using PaddleOCR.

    Accepts posters that contain only the movie/series title and optionally
    the director's name.  Rejects anything with additional text: taglines,
    actor names, studio logos, format badges, credits blocks, etc.

    Args:
        title: The movie or series title (required — from DB).
        director: Optional director name.  Text matching this name is
            allowed on posters (e.g. "A Denis Villeneuve Film").
            Defaults to ``None`` — no director filtering.
        media_type: ``"movie"`` or ``"tv"``.  Reserved for future
            differentiation (TV posters may allow main cast names).
        num_workers: Number of parallel OCR processes.  Defaults to
            ``settings.OCR_WORKERS``.
    """

    def __init__(
        self,
        title: str,
        *,
        director: str | None = None,
        media_type: str = "movie",
        num_workers: int | None = None,
    ):
        self.title = _normalise(title)
        self.director = _normalise(director) if director else None
        self.media_type = media_type
        self.num_workers = (
            num_workers if num_workers is not None
            else settings.OCR_WORKERS
        )

        # Build token sets
        self.title_tokens = set(self.title.split())
        self._add_digit_words(self.title_tokens)

        if self.director:
            self.director_tokens = set(self.director.split())
        else:
            self.director_tokens = set()

        logger.debug(
            "PosterTextFilter ready: title=%r director=%r workers=%d",
            self.title, self.director, self.num_workers,
        )

    # ------------------------------------------------------------------
    # Batch filter (pipeline entry point)
    # ------------------------------------------------------------------

    def filter_batch(
        self, paths: list[Path], *, include_texts: bool = False,
    ) -> tuple[list[Path], list[tuple[Path, str]]] | tuple[list[Path], list[tuple[Path, str]], dict[Path, str]]:
        """Filter a batch of poster images using multiprocessing.

        Args:
            paths: Paths to poster candidate images.
            include_texts: If True, also return a dict mapping accepted
                paths to their normalised detected text.

        Returns:
            ``(accepted_paths, [(rejected_path, reason), ...])``.
            If ``include_texts=True``, also returns ``{accepted_path: text}``.
        """
        if not paths:
            return ([], [])

        logger.info(
            "OCR filter starting: %d candidates, %d workers, title=%r",
            len(paths), self.num_workers, self.title,
        )

        ctx = multiprocessing.get_context("spawn")
        img_map = {p.name: p for p in paths}

        accepted: list[Path] = []
        accepted_texts: dict[Path, str] = {}
        rejected: list[tuple[Path, str]] = []

        with ctx.Pool(
            processes=self.num_workers,
            initializer=_init_worker,
            initargs=(self.title_tokens, self.director_tokens),
        ) as pool:
            for name, is_ok, text in pool.imap(
                _process_image, [str(p) for p in paths],
            ):
                if is_ok is None:
                    logger.warning("OCR error for %s: %s — accepting", name, text)
                    accepted.append(img_map[name])
                    accepted_texts[img_map[name]] = ""
                elif is_ok:
                    accepted.append(img_map[name])
                    if include_texts:
                        accepted_texts[img_map[name]] = text
                    logger.debug("ACCEPTED  %s", name)
                else:
                    rejected.append((img_map[name], text))
                    logger.debug("REJECTED  %s  text=%r", name, text[:120])

            pool.close()
            pool.join()

        logger.info(
            "OCR filter done: %d accepted, %d rejected (of %d)",
            len(accepted), len(rejected), len(paths),
        )

        if include_texts:
            return (accepted, rejected, accepted_texts)
        return (accepted, rejected)

    # ------------------------------------------------------------------
    # Single-image check
    # ------------------------------------------------------------------

    def is_acceptable(self, image_path: Path) -> tuple[bool, str]:
        """Check a single poster image without multiprocessing overhead.

        Useful for quick checks or when running inside a worker pool that
        already manages concurrency.

        Returns:
            ``(accepted: bool, detected_text: str)``
        """
        try:
            img_array = np.array(Image.open(image_path).convert("RGB"))
            h = img_array.shape[0]
            strip_rows = int(h * TOP_STRIP_FRACTION)

            ocr = _load_ocr()
            full_text = _detect_text(ocr, img_array)
            strip_text = _detect_text_strip(ocr, img_array[:strip_rows])
            bottom_text = _detect_text_bottom(ocr, img_array[h - strip_rows:])

            combined = (full_text + " " + strip_text + " " + bottom_text).strip()
            normed = _normalise(combined)

            # Use instance tokens (not worker globals) for single-image mode
            accepted = self._check_text(normed)
            return (accepted, normed)

        except Exception as exc:
            logger.warning(
                "OCR failed for %s: %s — accepting", image_path.name, exc,
            )
            return (True, str(exc))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_text(self, normed_text: str) -> bool:
        """Check normalised text against instance tokens (single-image mode)."""
        if not normed_text:
            return True  # text-free poster → accept

        words = set(normed_text.split())

        if words & FORMAT_BLOCKLIST:
            return False

        remaining = words - self.title_tokens - self.director_tokens
        remaining = {w for w in remaining if len(w) >= 4}

        allowed = self.title_tokens | self.director_tokens

        remaining = {
            w for w in remaining
            if not difflib.get_close_matches(w, allowed, n=1, cutoff=0.70)
        }

        remaining = {
            w for w in remaining
            if not any(token in w for token in allowed if len(token) >= 3)
        }

        return len(remaining) == 0

    @staticmethod
    def _add_digit_words(tokens: set[str]) -> None:
        """Expand digit tokens to their word equivalents in-place."""
        tokens |= {_DIGIT_WORDS[t] for t in list(tokens) if t in _DIGIT_WORDS}
