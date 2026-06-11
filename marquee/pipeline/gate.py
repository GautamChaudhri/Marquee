"""Stage 5 absolute hard gates, split by when their inputs become available.

The gates are evaluated as early as their inputs allow so later, more
expensive stages never run on candidates that are already dead:

  - ``evaluate_metadata``: resolution floor — TMDB metadata, available
    before anything is computed.
  - ``evaluate_style``: aesthetic floor (with the knn rescue) and off-style
    floor — needs only the CLIP embedding, runs before OCR.
  - ``evaluate_detail``: the optional fan-junk combo — needs the full vector.

``evaluate`` chains all three for callers that have everything at once.
"""

from __future__ import annotations

from dataclasses import dataclass

from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.pipeline.types import FeatureVector


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str | None = None
    detail: str | None = None


_PASS = GateResult(True)


class PosterGate:
    def __init__(self, config: PipelineSettings = pipeline_settings):
        self.config = config

    def evaluate_metadata(self, *, original_width: int) -> GateResult:
        if original_width < self.config.GATE_MIN_WIDTH:
            return GateResult(
                False,
                "resolution_floor",
                f"width={original_width} < {self.config.GATE_MIN_WIDTH}",
            )
        return _PASS

    def evaluate_style(self, features: FeatureVector) -> GateResult:
        if features.aesthetic < self.config.GATE_MIN_AESTHETIC:
            # High knn_sim means the taste profile validates this poster — relax the
            # aesthetic floor for stylized/graphic designs that score low on a
            # photographic-quality model but are on-brand.
            rescued = (
                features.knn_sim >= self.config.GATE_AESTHETIC_RESCUE_KNN
                and features.aesthetic >= self.config.GATE_MIN_AESTHETIC_RESCUED
            )
            if not rescued:
                return GateResult(
                    False,
                    "aesthetic_floor",
                    f"aesthetic={features.aesthetic:.4f} < "
                    f"{self.config.GATE_MIN_AESTHETIC:.4f}",
                )
        if features.knn_sim < self.config.GATE_MIN_KNN_SIM:
            return GateResult(
                False,
                "off_style_floor",
                f"knn_sim={features.knn_sim:.4f} < "
                f"{self.config.GATE_MIN_KNN_SIM:.4f}",
            )
        return _PASS

    def evaluate_detail(self, features: FeatureVector) -> GateResult:
        if (
            self.config.GATE_FAN_JUNK_ENABLED
            and features.aesthetic < self.config.GATE_FAN_JUNK_MAX_AESTHETIC
            and features.provenance < self.config.GATE_FAN_JUNK_MAX_PROVENANCE
            and features.resolution
            < self.config.GATE_FAN_JUNK_MAX_RESOLUTION_MP
        ):
            return GateResult(
                False,
                "fan_junk_combo",
                "aesthetic, provenance, and resolution all below combo thresholds",
            )
        return _PASS

    def evaluate(self, features: FeatureVector, *, original_width: int) -> GateResult:
        """All gates at once, for callers holding the complete vector."""
        for result in (
            self.evaluate_metadata(original_width=original_width),
            self.evaluate_style(features),
            self.evaluate_detail(features),
        ):
            if not result.passed:
                return result
        return _PASS
