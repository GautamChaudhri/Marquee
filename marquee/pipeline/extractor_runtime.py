"""Neutral process-local pipeline runtime helpers.

A single process-wide singleton (``extractor_runtime``) that owns the small,
still-used runtime concerns extracted from the retired ``RunManager``:

  - a process-lifetime ``FeatureExtractor`` so ONNX sessions are created once
    (not per request), reset whenever the taste profile changes;
  - a conservative process-local GPU/model cache release path;
  - archived run-JSON reads for results/feedback/label routes.

It owns **no** job/run/batch lifecycle: it never serializes runs, tracks an
active run, publishes progress, or writes canonical job/run rows. Durable
execution, cancellation, retries, and progress belong to the canonical PgQueuer
job platform (``marquee.core.jobs``).
"""

from __future__ import annotations

import gc
import json
import logging
from pathlib import Path

from marquee.config import settings
from marquee.pipeline.features import FeatureExtractor

logger = logging.getLogger(__name__)


class ExtractorRuntime:
    """Process-lifetime feature extractor plus archive/GPU helpers."""

    def __init__(self) -> None:
        self._extractor: FeatureExtractor | None = None

    # ------------------------------------------------------------------
    # Extractor lifecycle
    # ------------------------------------------------------------------

    def ensure_extractor(self) -> FeatureExtractor:
        """Create + preflight the extractor once (blocking — call inside a thread)."""
        if self._extractor is None:
            extractor = FeatureExtractor()
            extractor.preflight()
            self._extractor = extractor
        return self._extractor

    def reset_extractor(self) -> None:
        """Drop the cached extractor so the next use reloads the taste profile."""
        self._extractor = None
        logger.info("ExtractorRuntime | extractor reset — next use reloads the taste profile")

    def release_gpu_resources(self) -> dict:
        """Drop process-local model caches and ask Python to release memory.

        Conservative by design: it only clears caches owned by this process.
        CUDA/ORT/Paddle native allocators may still keep pools alive, but clearing
        these references is the safest in-process release path before handing the
        GPU to another component.
        """
        had_extractor = self._extractor is not None
        self._extractor = None

        ocr_cleared = False
        try:
            from marquee.pipeline import ocr_filter  # noqa: PLC0415

            if getattr(ocr_filter, "_worker_ocr", None) is not None:
                ocr_cleared = True
            ocr_filter._worker_ocr = None
        except Exception:  # noqa: BLE001
            logger.exception("ExtractorRuntime | failed to clear OCR singleton")

        collected = gc.collect()
        result = {
            "extractor_cleared": had_extractor,
            "ocr_cleared": ocr_cleared,
            "gc_collected": collected,
        }
        logger.info("ExtractorRuntime | released GPU resources | %s", result)
        return result

    # ------------------------------------------------------------------
    # Results access
    # ------------------------------------------------------------------

    def load_archive(self, run_id: str, archive_path: str | None = None) -> dict | None:
        """Load a run's archived JSON payload."""
        path = Path(archive_path) if archive_path else settings.runs_archive_path / f"{run_id}.json"
        if not path.exists():
            return None
        try:
            # parse_constant only fires for NaN/Infinity/-Infinity literals —
            # legacy archives contain them, and Starlette responses reject them.
            return json.loads(path.read_text(), parse_constant=lambda _c: None)
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read run archive %s", path)
            return None


extractor_runtime = ExtractorRuntime()
