"""Stage 5 absolute hard gates."""

from __future__ import annotations

from dataclasses import dataclass

from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.pipeline.types import FeatureVector


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str | None = None
    detail: str | None = None


class PosterGate:
    def __init__(self, config: PipelineSettings = pipeline_settings):
        self.config = config

    def evaluate(self, features: FeatureVector, *, original_width: int) -> GateResult:
        if original_width < self.config.GATE_MIN_WIDTH:
            return GateResult(
                False,
                "resolution_floor",
                f"width={original_width} < {self.config.GATE_MIN_WIDTH}",
            )
        if features.aesthetic < self.config.GATE_MIN_AESTHETIC:
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
        return GateResult(True)
