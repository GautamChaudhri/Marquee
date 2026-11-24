"""Tests for the extended-features layer: calibration, CV pack, scorer, head."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from marquee.config.pipeline import PipelineSettings
from marquee.integrations.tmdb.client import PosterCandidate
from marquee.ml.artifact_codec import unicode_array
from marquee.ml.calibration import TasteCalibration
from marquee.ml.residual import (
    ResidualArtifact,
    ResidualEvaluation,
    baseline_signature,
)
from marquee.ml.visual_features import (
    blockiness,
    composition_features,
    face_geometry,
    noise_sigma,
    palette_features,
    standardize_width,
    title_geometry,
)
from marquee.pipeline.features import (
    FeatureExtractor,
    embedding_cache_path,
    file_sha256,
    load_cached_embedding,
)
from marquee.pipeline.normalize import normalize_features, quality_artifact_raw
from marquee.pipeline.scorer import (
    ResidualRuntimeContext,
    ResidualScorer,
    WeightedScorer,
    select_scorer,
)
from marquee.pipeline.types import FeatureVector


def _features(**overrides) -> FeatureVector:
    values = {
        "knn_sim": 0.65,
        "aesthetic": 6.0,
        "title_colorfulness": 30.0,
        "text_residual": 0.25,
        "resolution": 3.0,
        "sharpness": 500.0,
        "face_area": 0.20,
        "provenance": 0.70,
        "lang_match": 1.0,
    }
    values.update(overrides)
    return FeatureVector(**values)


# ---------------------------------------------------------------------------
# Exemplar calibration — the KDE typicality core
# ---------------------------------------------------------------------------


def _calibration(values_by_feature: dict[str, list[float]]) -> TasteCalibration:
    names = list(values_by_feature)
    width = max(len(v) for v in values_by_feature.values())
    matrix = np.full((len(names), width), np.nan)
    for row, name in enumerate(names):
        vals = values_by_feature[name]
        matrix[row, : len(vals)] = vals
    config = PipelineSettings(CALIBRATION_MIN_SAMPLES=5)
    return TasteCalibration(names, matrix, config=config)


def test_typicality_peaks_at_dense_region_and_decays_outside():
    rng = np.random.default_rng(7)
    calibration = _calibration({"darkness": list(rng.normal(0.6, 0.05, 200))})
    at_center = calibration.typicality("darkness", 0.6)
    at_edge = calibration.typicality("darkness", 0.75)
    far_out = calibration.typicality("darkness", 0.95)
    assert at_center == pytest.approx(1.0, abs=0.05)
    assert at_center > at_edge > far_out
    assert far_out < 0.05


def test_typicality_is_multimodal_not_centroid_blurred():
    """Bimodal taste (dark horror + bright animation): BOTH modes must score
    high and the meaningless middle must score LOW — the anti-centroid test."""
    rng = np.random.default_rng(3)
    dark = rng.normal(0.2, 0.03, 150)
    bright = rng.normal(0.8, 0.03, 150)
    calibration = _calibration({"darkness": list(dark) + list(bright)})

    dark_score = calibration.typicality("darkness", 0.2)
    bright_score = calibration.typicality("darkness", 0.8)
    middle_score = calibration.typicality("darkness", 0.5)

    assert dark_score > 0.8
    assert bright_score > 0.8
    assert middle_score < 0.2  # a Gaussian band would have peaked HERE


def test_typicality_handles_nan_and_min_samples():
    calibration = _calibration(
        {
            "sparse": [0.5, 0.6, float("nan"), float("nan")],  # < min samples
            "good": [0.1, 0.12, 0.11, 0.13, 0.09, 0.1, 0.12],
        }
    )
    assert not calibration.is_calibrated("sparse")
    assert "sparse" in calibration.skipped_features
    assert calibration.is_calibrated("good")
    assert calibration.typicality("sparse", 0.5) is None
    assert calibration.typicality("good", float("nan")) is None


def test_typicality_degenerate_unanimous_feature():
    """All exemplars share one value: that value = 1.0, others decay."""
    calibration = _calibration({"face_count": [0.0] * 50})
    assert calibration.typicality("face_count", 0.0) == pytest.approx(1.0)
    assert calibration.typicality("face_count", 3.0) < 0.01


def test_calibration_from_profile_arrays_roundtrip(tmp_path: Path):
    names = ["darkness", "symmetry"]
    values = np.array([[0.5, 0.6, 0.55] * 10, [0.9, 0.92, 0.88] * 10])
    np.savez(
        tmp_path / "p.npz",
        calib_feature_names=unicode_array(names),
        calib_feature_values=values,
    )
    with np.load(tmp_path / "p.npz", allow_pickle=False) as data:
        config = PipelineSettings(CALIBRATION_MIN_SAMPLES=5)
        calibration = TasteCalibration.from_profile_arrays(data, config=config)
    assert calibration is not None
    assert calibration.is_calibrated("darkness")
    assert calibration.band("symmetry").n == 30


# ---------------------------------------------------------------------------
# Visual features (recs 1, 3, 5)
# ---------------------------------------------------------------------------


def test_palette_features_dark_vs_bright():
    dark = np.zeros((300, 200, 3), dtype=np.uint8)
    bright = np.full((300, 200, 3), 240, dtype=np.uint8)
    assert palette_features(dark)["darkness"] > 0.95
    assert palette_features(bright)["darkness"] < 0.1


def test_palette_hue_entropy_monochrome_vs_rainbow():
    gray = np.full((300, 200, 3), 128, dtype=np.uint8)
    rainbow = np.zeros((300, 200, 3), dtype=np.uint8)
    rainbow[:100, :, 2] = 255  # red (BGR)
    rainbow[100:200, :, 1] = 255  # green
    rainbow[200:, :, 0] = 255  # blue
    assert palette_features(gray)["hue_entropy"] == 0.0
    assert palette_features(rainbow)["hue_entropy"] > 0.3


def test_composition_negative_space_blank_vs_noise():
    rng = np.random.default_rng(0)
    blank = np.full((480, 320, 3), 255, dtype=np.uint8)
    noisy = rng.integers(0, 255, (480, 320, 3), dtype=np.uint8).astype(np.uint8)
    assert composition_features(blank)["negative_space_frac"] > 0.95
    assert composition_features(noisy)["negative_space_frac"] < 0.1


def test_composition_symmetry_mirrored_image():
    rng = np.random.default_rng(1)
    half = rng.integers(0, 255, (300, 100, 3), dtype=np.uint8).astype(np.uint8)
    mirrored = np.concatenate([half, half[:, ::-1]], axis=1)
    sym = composition_features(mirrored)["symmetry"]
    asym = composition_features(
        rng.integers(0, 255, (300, 200, 3), dtype=np.uint8).astype(np.uint8)
    )["symmetry"]
    assert sym > 0.95
    assert sym > asym


def test_noise_and_blockiness_ordering():
    rng = np.random.default_rng(2)
    clean = np.full((256, 256), 128, dtype=np.uint8)
    noisy = np.clip(clean.astype(np.int16) + rng.normal(0, 20, clean.shape), 0, 255).astype(
        np.uint8
    )
    assert noise_sigma(noisy) > noise_sigma(clean) + 5

    blocky = np.zeros((256, 256), dtype=np.uint8)
    blocky[:, ::16] = 0
    for i in range(0, 256, 8):
        blocky[:, i:] = min(255, i)  # step at every 8-px boundary
    assert blockiness(blocky) > blockiness(clean)


def test_standardize_width_resizes_only_when_needed():
    tall = np.zeros((900, 600, 3), dtype=np.uint8)
    std = standardize_width(tall)
    assert std.shape[1] == 500
    assert std.shape[0] == 750
    already = np.zeros((750, 500, 3), dtype=np.uint8)
    assert standardize_width(already) is already


def test_title_geometry_fractions():
    bbox = ((100.0, 600.0), (400.0, 600.0), (400.0, 660.0), (100.0, 660.0))
    geo = title_geometry(bbox, image_width=500, image_height=750)
    assert geo["title_height_frac"] == pytest.approx(60 / 750)
    assert geo["title_y_center"] == pytest.approx(630 / 750)
    assert geo["title_centeredness"] == pytest.approx(1.0)  # centered box
    missing = title_geometry(None, 500, 750)
    assert np.isnan(missing["title_height_frac"])


def test_face_geometry_counts_and_dominance():
    boxes = [(0.0, 0.0, 100.0, 100.0), (0.0, 0.0, 250.0, 300.0)]
    geo = face_geometry(boxes, image_width=500, image_height=750)
    assert geo["face_count"] == 2.0
    assert geo["largest_face_frac"] == pytest.approx(250 * 300 / (500 * 750))


# ---------------------------------------------------------------------------
# Normalization of the new scorer features
# ---------------------------------------------------------------------------


def test_normalize_omits_uncomputed_optional_features():
    features = _features()  # dino/typicality/quality default to None
    normalized = normalize_features(features)
    assert "dino_knn" not in normalized
    assert "taste_typicality" not in normalized
    assert "quality_artifacts" not in normalized


def test_normalize_dino_uses_profile_range():
    features = _features(dino_knn=0.55)
    normalize_features(features, dino_knn_range=(0.5, 0.6))
    assert features.normalized["dino_knn"] == pytest.approx(0.5)


def test_quality_artifacts_inverted_to_cleanliness():
    config = PipelineSettings(QUALITY_BLOCKINESS_SAT=4.0, QUALITY_NOISE_SAT=10.0)
    raw = quality_artifact_raw(2.0, 5.0, config)  # half-bad on both
    assert raw == pytest.approx(0.5)
    features = _features(quality_artifacts=raw)
    normalize_features(features, config)
    assert features.normalized["quality_artifacts"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Scorer: weight redistribution + bounded residual
# ---------------------------------------------------------------------------


def test_weighted_scorer_redistributes_absent_optional_features():
    features = _features()
    normalize_features(features)  # no dino/typicality/quality keys
    score_without, _ = WeightedScorer().score(features)

    features_full = _features(dino_knn=0.55, taste_typicality=0.8, quality_artifacts=0.1)
    normalize_features(features_full, dino_knn_range=(0.3, 0.8))
    score_with, contributions = WeightedScorer().score(features_full)

    assert 0.0 <= score_without <= 1.0
    assert 0.0 <= score_with <= 1.0
    assert contributions["dino_knn"] > 0
    assert contributions["taste_typicality"] > 0


def _residual(config: PipelineSettings, feature_names: list[str]) -> ResidualArtifact:
    return ResidualArtifact(
        namespace="movies",
        feature_names=feature_names,
        weights=np.ones(len(feature_names)),
        bias=0.0,
        alpha=0.5,
        delta_max=0.75,
        baseline_signature=baseline_signature(config.scorer_weights),
        profile_checksum="a" * 64,
        evidence_revision="b" * 64,
        seed=0,
        evaluation=ResidualEvaluation(0.5, 0.6, 0.1, 5, 20, 0.05, 0.1),
        trained_at="2026-07-22T00:00:00+00:00",
    )


def _residual_context(config: PipelineSettings) -> ResidualRuntimeContext:
    return ResidualRuntimeContext(
        library="movies",
        baseline_signature=baseline_signature(config.scorer_weights),
        profile_checksum="a" * 64,
        profile_generation=0,
    )


def test_residual_refuses_missing_features():
    config = PipelineSettings()
    scorer = ResidualScorer(
        WeightedScorer(config), _residual(config, ["dino_knn"]), _residual_context(config)
    )
    features = _features()
    normalize_features(features)  # dino absent
    with pytest.raises(RuntimeError, match="dino_knn"):
        scorer.score(features)


def test_select_scorer_auto_falls_back_without_artifact(
    tmp_path: Path,
):
    config = PipelineSettings(SCORER="auto")
    scorer = select_scorer(config, artifact_path=tmp_path / "missing.npz")
    assert scorer.name == "weighted"


def test_select_scorer_auto_prefers_valid_residual(tmp_path: Path):
    path = tmp_path / "residual.npz"
    config = PipelineSettings(SCORER="auto")
    _residual(config, ["knn_sim"]).save(path)
    scorer = select_scorer(config, artifact_path=path, context=_residual_context(config))
    assert scorer.name == "residual"


# ---------------------------------------------------------------------------
# DINOv2 activation policy
# ---------------------------------------------------------------------------


def test_dino_auto_activation_by_tier(monkeypatch: pytest.MonkeyPatch):
    from marquee.ml import dino as dino_module
    from marquee.ml.hardware import HardwareProfile

    def fake_profile(tier: str):
        return HardwareProfile(tier=tier, providers=["CPUExecutionProvider"], provider_options=[{}])

    monkeypatch.setattr(dino_module, "detect_hardware", lambda: fake_profile("cuda"))
    monkeypatch.setattr(dino_module.pipeline_settings, "DINO_ENABLED", "auto")
    assert dino_module.dino_active() is True

    monkeypatch.setattr(dino_module, "detect_hardware", lambda: fake_profile("cpu"))
    assert dino_module.dino_active() is False

    monkeypatch.setattr(dino_module.pipeline_settings, "DINO_ENABLED", "on")
    assert dino_module.dino_active() is True

    monkeypatch.setattr(dino_module.pipeline_settings, "DINO_ENABLED", "off")
    monkeypatch.setattr(dino_module, "detect_hardware", lambda: fake_profile("cuda"))
    assert dino_module.dino_active() is False


def test_dino_preprocess_shape():
    from marquee.ml.dino import preprocess_dino

    image = Image.new("RGB", (500, 750), color="navy")
    pixels = preprocess_dino(image)
    assert pixels.shape == (1, 3, 224, 224)
    assert pixels.dtype == np.float32


# ---------------------------------------------------------------------------
# Embedding cache — keyed by the model name and the SHA-256 of the file's bytes
# ---------------------------------------------------------------------------

CACHE_MODEL = "clip-vit-b-32"


class _CountingEncoder:
    """Stands in for CLIP: one distinct unit vector per call, and counts what it encodes."""

    model_name = CACHE_MODEL

    def __init__(self) -> None:
        self.encoded = 0

    def encode_batch(self, pixels: list[np.ndarray]) -> np.ndarray:
        rows = []
        for _ in pixels:
            self.encoded += 1
            vector = np.zeros(512, dtype=np.float32)
            vector[self.encoded % 512] = 1.0
            rows.append(vector)
        return np.stack(rows)


class _FlatAesthetic:
    model_name = CACHE_MODEL

    def score(self, _embedding: np.ndarray) -> float:
        return 5.0


def _candidate(name: str) -> PosterCandidate:
    return PosterCandidate(
        file_path=f"/{name}",
        width=2000,
        height=3000,
        aspect_ratio=2 / 3,
        language="en",
        vote_average=5.0,
        vote_count=10,
    )


def _poster(path: Path, colour: tuple[int, int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 60), colour).save(path, format="JPEG")
    return path


def _extractor(config: PipelineSettings, encoder: _CountingEncoder) -> FeatureExtractor:
    return FeatureExtractor(
        config=config,
        encoder=encoder,
        aesthetic=_FlatAesthetic(),
        face_detector=object(),
        dino_encoder=object(),
        person_detector=object(),
        personalization_mode="collecting",
    )


def _entries(config: PipelineSettings) -> list[Path]:
    return sorted((config.EMBEDDING_CACHE_DIR / CACHE_MODEL).glob("*.npz"))


def test_same_name_with_different_bytes_gets_two_entries(tmp_path: Path) -> None:
    config = PipelineSettings(EMBEDDING_CACHE_DIR=tmp_path / "cache", AI_MODEL=CACHE_MODEL)
    first = _poster(tmp_path / "run1" / "poster.jpg", (200, 10, 10))
    second = _poster(tmp_path / "run2" / "poster.jpg", (10, 10, 200))
    encoder = _CountingEncoder()
    extractor = _extractor(config, encoder)

    extractor.extract_style_batch([(first, _candidate("poster.jpg"))])
    run1_vector = extractor.load_run_embedding("poster.jpg")
    extractor.extract_style_batch([(second, _candidate("poster.jpg"))])
    run2_vector = extractor.load_run_embedding("poster.jpg")

    assert encoder.encoded == 2
    assert len(_entries(config)) == 2
    assert run1_vector is not None and run2_vector is not None
    assert not np.array_equal(run1_vector, run2_vector)
    assert np.array_equal(load_cached_embedding(file_sha256(first), config), run1_vector)
    assert np.array_equal(load_cached_embedding(file_sha256(second), config), run2_vector)


def test_same_bytes_under_two_names_share_one_entry(tmp_path: Path) -> None:
    config = PipelineSettings(EMBEDDING_CACHE_DIR=tmp_path / "cache", AI_MODEL=CACHE_MODEL)
    original = _poster(tmp_path / "run1" / "a.jpg", (30, 120, 30))
    copy = tmp_path / "run1" / "b.jpg"
    shutil.copyfile(original, copy)

    _extractor(config, _CountingEncoder()).extract_style_batch(
        [(original, _candidate("a.jpg")), (copy, _candidate("b.jpg"))]
    )
    assert len(_entries(config)) == 1
    assert _entries(config)[0] == embedding_cache_path(file_sha256(original), config)

    # A later run with the same bytes under a new name is a cache hit: nothing is encoded.
    renamed = _poster(tmp_path / "run2" / "c.jpg", (0, 0, 0))
    shutil.copyfile(original, renamed)
    encoder = _CountingEncoder()
    extractor = _extractor(config, encoder)
    results = extractor.extract_style_batch([(renamed, _candidate("c.jpg"))])
    assert encoder.encoded == 0
    assert not isinstance(results[0], Exception)
    assert extractor.load_run_embedding("c.jpg") is not None
    assert len(_entries(config)) == 1


def test_entry_written_under_another_model_is_not_returned(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    config = PipelineSettings(EMBEDDING_CACHE_DIR=cache, AI_MODEL=CACHE_MODEL)
    poster = _poster(tmp_path / "run" / "poster.jpg", (90, 90, 90))
    _extractor(config, _CountingEncoder()).extract_style_batch([(poster, _candidate("poster.jpg"))])
    digest = file_sha256(poster)
    assert load_cached_embedding(digest, config) is not None

    # The same entry placed where another model would look for it is refused.
    other = PipelineSettings(EMBEDDING_CACHE_DIR=cache, AI_MODEL="clip-vit-b-32-int8")
    other_path = embedding_cache_path(digest, other)
    other_path.parent.mkdir(parents=True)
    shutil.copyfile(embedding_cache_path(digest, config), other_path)
    assert load_cached_embedding(digest, other) is None


def test_entry_recording_other_bytes_is_not_returned(tmp_path: Path) -> None:
    config = PipelineSettings(EMBEDDING_CACHE_DIR=tmp_path / "cache", AI_MODEL=CACHE_MODEL)
    first = _poster(tmp_path / "run" / "one.jpg", (250, 250, 0))
    second = _poster(tmp_path / "run" / "two.jpg", (0, 250, 250))
    _extractor(config, _CountingEncoder()).extract_style_batch([(first, _candidate("one.jpg"))])
    # An entry stored under the second file's key but recording the first file's hash.
    target = embedding_cache_path(file_sha256(second), config)
    shutil.copyfile(embedding_cache_path(file_sha256(first), config), target)
    assert load_cached_embedding(file_sha256(second), config) is None


def test_name_unknown_to_the_current_run_has_no_embedding(tmp_path: Path) -> None:
    config = PipelineSettings(EMBEDDING_CACHE_DIR=tmp_path / "cache", AI_MODEL=CACHE_MODEL)
    extractor = _extractor(config, _CountingEncoder())
    assert extractor.load_run_embedding("never-seen.jpg") is None


def test_memory_cache_is_keyed_by_content_too(tmp_path: Path) -> None:
    config = PipelineSettings(EMBEDDING_CACHE_DIR=tmp_path / "cache", AI_MODEL=CACHE_MODEL)
    first = _poster(tmp_path / "run1" / "poster.jpg", (200, 10, 10))
    second = _poster(tmp_path / "run2" / "poster.jpg", (10, 10, 200))
    encoder = _CountingEncoder()
    extractor = _extractor(config, encoder)
    extractor.embedding_cache_enabled = False

    extractor.extract_style_batch([(first, _candidate("poster.jpg"))])
    extractor.extract_style_batch([(second, _candidate("poster.jpg"))])

    assert encoder.encoded == 2
    assert not (tmp_path / "cache").exists()
