"""Exemplar-calibrated feature normalization (taste typicality).

The problem with fixed higher-is-better ramps: taste is not monotonic. The
user does not want the *darkest* poster or the *most* colorful title — they
want values in the bands their hand-picked exemplars live in. Since the
taste profile already encodes those preferences, the trainer records the
distribution of every measurable feature across the exemplars, and this
module turns a candidate's raw value into a **typicality score**: how dense
is the exemplar distribution at this value, relative to its densest point.

Why KDE and not a Gaussian band around the mean
-----------------------------------------------
The design's core thesis (04 §3, §6) is that taste is multimodal — a dark
horror cluster and a bright animation cluster coexist. A mean±σ band would
put the scoring peak in the meaningless middle of those clusters, which is
exactly the centroid-blur failure the k-NN scorer was built to avoid. A 1-D
Gaussian KDE rewards *any* dense region of the exemplar distribution: a
candidate sitting on either taste cluster scores high, the watered-down
middle scores low.

Mechanics
---------
- ``typicality(x) = f̂(x) / max(f̂)`` where ``f̂`` is the Gaussian KDE over
  the exemplar values. Result is in [0, 1], hitting 1.0 at the densest
  taste region, decaying smoothly outside the exemplar range.
- Bandwidth: Silverman's rule with a robust sigma
  (``min(std, IQR/1.349)``) so a few outlier exemplars can't smear the
  bands, times the configurable ``CALIBRATION_BANDWIDTH_SCALE``.
- ``max(f̂)`` is approximated by evaluating the KDE at every sample point —
  for a Gaussian KDE the global maximum lies in the convex hull of the
  samples and is attained (to excellent approximation) at or next to the
  densest sample.
- NaN exemplar values (e.g. OCR found no title on that exemplar) are
  dropped per-feature; a feature with fewer than ``CALIBRATION_MIN_SAMPLES``
  usable values is reported as uncalibrated and the caller falls back to
  the fixed normalization.
- Degenerate features (zero spread, e.g. every exemplar has face_count=0)
  get a floor bandwidth derived from the value scale so typicality is 1.0
  at the shared value and decays for anything else — which is the correct
  reading: "your exemplars are unanimous here".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from marquee.core.pipeline_config import PipelineSettings, pipeline_settings

logger = logging.getLogger(__name__)

# Profile npz keys written by the taste trainer.
CALIB_NAMES_KEY = "calib_feature_names"
CALIB_VALUES_KEY = "calib_feature_values"


@dataclass(frozen=True)
class FeatureBand:
    """Per-feature calibration state plus log-friendly summary stats."""

    name: str
    samples: np.ndarray  # 1-D, NaN-free, float64
    bandwidth: float
    density_max: float
    median: float
    p10: float
    p90: float

    @property
    def n(self) -> int:
        return int(self.samples.size)

    def describe(self) -> str:
        return (
            f"{self.name}: n={self.n} median={self.median:.4f} "
            f"p10={self.p10:.4f} p90={self.p90:.4f} bw={self.bandwidth:.4f}"
        )


def _robust_sigma(values: np.ndarray) -> float:
    """Outlier-resistant spread estimate: min(std, IQR/1.349)."""
    std = float(values.std())
    q25, q75 = np.percentile(values, [25, 75])
    iqr_sigma = float(q75 - q25) / 1.349
    candidates = [s for s in (std, iqr_sigma) if s > 0]
    return min(candidates) if candidates else 0.0


def _silverman_bandwidth(values: np.ndarray, scale: float) -> float:
    """Silverman's rule of thumb with a degenerate-distribution floor."""
    sigma = _robust_sigma(values)
    n = values.size
    if sigma > 0:
        return float(scale * 0.9 * sigma * n ** (-1 / 5))
    # Zero spread: exemplars are unanimous. Use a floor proportional to the
    # value's own scale so typicality stays 1.0 at the unanimous value and
    # decays meaningfully away from it.
    anchor = float(np.abs(values).max())
    return float(scale * max(0.05 * anchor, 1e-3))


def _kde_density(x: float | np.ndarray, samples: np.ndarray, bandwidth: float) -> np.ndarray:
    """Unnormalized-constant-free Gaussian KDE (constants cancel in the ratio)."""
    x_arr = np.atleast_1d(np.asarray(x, dtype=np.float64))
    z = (x_arr[:, None] - samples[None, :]) / bandwidth
    return np.exp(-0.5 * z * z).mean(axis=1)


class TasteCalibration:
    """KDE typicality scorer over the taste profile's feature distributions."""

    def __init__(
        self,
        feature_names: list[str],
        feature_values: np.ndarray,
        *,
        config: PipelineSettings = pipeline_settings,
    ):
        """
        Args:
            feature_names: F feature names.
            feature_values: (F, N) matrix of per-exemplar raw values; NaN
                marks "not measurable on this exemplar".
        """
        self.config = config
        self._bands: dict[str, FeatureBand] = {}
        self._skipped: dict[str, str] = {}

        values_matrix = np.asarray(feature_values, dtype=np.float64)
        if values_matrix.ndim != 2 or values_matrix.shape[0] != len(feature_names):
            raise ValueError(
                f"Calibration matrix shape {values_matrix.shape} does not match "
                f"{len(feature_names)} feature names"
            )

        for index, name in enumerate(feature_names):
            row = values_matrix[index]
            samples = row[~np.isnan(row)]
            if samples.size < config.CALIBRATION_MIN_SAMPLES:
                self._skipped[name] = (
                    f"only {samples.size} usable exemplar values "
                    f"(min {config.CALIBRATION_MIN_SAMPLES})"
                )
                continue
            bandwidth = _silverman_bandwidth(
                samples, config.CALIBRATION_BANDWIDTH_SCALE
            )
            density_at_samples = _kde_density(samples, samples, bandwidth)
            self._bands[name] = FeatureBand(
                name=name,
                samples=samples,
                bandwidth=bandwidth,
                density_max=float(density_at_samples.max()),
                median=float(np.median(samples)),
                p10=float(np.percentile(samples, 10)),
                p90=float(np.percentile(samples, 90)),
            )

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @classmethod
    def from_profile_arrays(
        cls,
        data: dict | np.lib.npyio.NpzFile,
        *,
        config: PipelineSettings = pipeline_settings,
    ) -> TasteCalibration | None:
        """Build from a loaded taste-profile npz; None if it has no stats."""
        if CALIB_NAMES_KEY not in data or CALIB_VALUES_KEY not in data:
            return None
        names = [str(name) for name in data[CALIB_NAMES_KEY].tolist()]
        return cls(names, np.asarray(data[CALIB_VALUES_KEY]), config=config)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def is_calibrated(self, name: str) -> bool:
        return name in self._bands

    @property
    def calibrated_features(self) -> list[str]:
        return sorted(self._bands)

    @property
    def skipped_features(self) -> dict[str, str]:
        return dict(self._skipped)

    def typicality(self, name: str, value: float) -> float | None:
        """How typical of the user's exemplars is *value* for feature *name*.

        Returns None when the feature has no usable calibration (caller
        falls back to fixed normalization) or the value itself is NaN.
        """
        band = self._bands.get(name)
        if band is None or value is None or np.isnan(value):
            return None
        density = float(
            _kde_density(float(value), band.samples, band.bandwidth)[0]
        )
        if band.density_max <= 0:
            return None
        return float(np.clip(density / band.density_max, 0.0, 1.0))

    def band(self, name: str) -> FeatureBand | None:
        return self._bands.get(name)

    def describe(self) -> list[str]:
        """Log lines summarizing every band — emitted once per run."""
        lines = [
            f"CALIBRATION | active | features={len(self._bands)} "
            f"bandwidth_scale={self.config.CALIBRATION_BANDWIDTH_SCALE}"
        ]
        lines.extend(
            f"CALIBRATION BAND | {self._bands[name].describe()}"
            for name in sorted(self._bands)
        )
        lines.extend(
            f"CALIBRATION SKIP | {name}: {reason}"
            for name, reason in sorted(self._skipped.items())
        )
        return lines
