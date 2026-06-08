"""Focused unit tests for the revised GATE-then-RANK pipeline."""

from __future__ import annotations

from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

from marquee.api.routes.test_pipeline import _clear_generated_outputs
from marquee.core.pipeline_config import PipelineSettings
from marquee.core.poster_sources.tmdb import PosterCandidate
from marquee.ml.colorfulness import hasler_susstrunk
from marquee.ml.embedding import choose_execution_providers
from marquee.ml.normalize import normalize_features
from marquee.ml.taste_store import NumpyTasteStore
from marquee.pipeline import ocr_filter
from marquee.pipeline.deduper import PosterDeduper
from marquee.pipeline.features import calculate_text_residual
from marquee.pipeline.gate import PosterGate
from marquee.pipeline.output import place_ranked
from marquee.pipeline.scorer import WeightedScorer
from marquee.pipeline.types import CandidateScore, FeatureVector


def _features(**overrides: float) -> FeatureVector:
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


def test_normalization_orients_every_feature_higher_is_better():
    clean = _features(text_residual=0.0, face_area=0.0)
    cluttered = _features(text_residual=1.0, face_area=1.0)

    normalize_features(clean)
    normalize_features(cluttered)

    assert clean.normalized["text_residual"] == 1.0
    assert cluttered.normalized["text_residual"] == 0.0
    assert clean.normalized["face_area"] == 1.0
    assert cluttered.normalized["face_area"] == 0.0


def test_text_residual_uses_configured_blend():
    config = PipelineSettings(
        RESIDUAL_COUNT_SAT=4,
        RESIDUAL_WEIGHT_COUNT=0.75,
        RESIDUAL_WEIGHT_AREA=0.25,
    )
    assert calculate_text_residual(2, 0.4, config) == pytest.approx(0.475)
    assert calculate_text_residual(20, 2.0, config) == 1.0


def test_weighted_scorer_is_weight_normalized():
    config = PipelineSettings(
        WEIGHT_KNN_SIM=2.0,
        WEIGHT_AESTHETIC=1.0,
        WEIGHT_TITLE_COLORFULNESS=0.0,
        WEIGHT_FACE_AREA=0.0,
        WEIGHT_TEXT_RESIDUAL=0.0,
        WEIGHT_PROVENANCE=0.0,
        WEIGHT_SHARPNESS=0.0,
    )
    features = _features()
    features.normalized = {
        "knn_sim": 1.0,
        "aesthetic": 0.0,
        "title_colorfulness": 0.0,
        "face_area": 0.0,
        "text_residual": 0.0,
        "provenance": 0.0,
        "sharpness": 0.0,
        "resolution": 0.0,
        "lang_match": 0.0,
    }

    score, contributions = WeightedScorer(config).score(features)

    assert score == pytest.approx(2 / 3)
    assert sum(contributions.values()) == pytest.approx(score)


def test_pipeline_config_rejects_negative_weights():
    with pytest.raises(ValueError, match="non-negative"):
        PipelineSettings(WEIGHT_FACE_AREA=-0.1)


@pytest.mark.parametrize(
    ("overrides", "width", "reason"),
    [
        ({}, 499, "resolution_floor"),
        ({"aesthetic": 4.49}, 500, "aesthetic_floor"),
        ({"knn_sim": 0.44}, 500, "off_style_floor"),
    ],
)
def test_gate_reasons(overrides: dict[str, float], width: int, reason: str):
    result = PosterGate().evaluate(_features(**overrides), original_width=width)
    assert not result.passed
    assert result.reason == reason


def test_gate_passes_candidate_at_thresholds():
    result = PosterGate().evaluate(
        _features(aesthetic=4.5, knn_sim=0.45),
        original_width=500,
    )
    assert result.passed


def test_taste_store_queries_top_k_and_checks_model(tmp_path: Path):
    profile = tmp_path / "taste_profile.clip-vit-b-32.npz"
    embeddings = np.eye(3, 512, dtype=np.float32)
    centroid = embeddings.mean(axis=0)
    centroid /= np.linalg.norm(centroid)
    np.savez(
        profile,
        embeddings=embeddings,
        poster_names=np.asarray(["a.jpg", "b.jpg", "c.jpg"], dtype=object),
        centroid_emb=centroid,
        model_name=np.asarray("clip-vit-b-32"),
    )

    store = NumpyTasteStore(profile)
    assert store.query_similar(embeddings[0], k=2) == pytest.approx([1.0, 0.0])

    mismatched = NumpyTasteStore(
        profile,
        expected_model_name="clip-vit-b-16",
    )
    with pytest.raises(RuntimeError, match="model mismatch"):
        _ = mismatched.size


def test_colorfulness_distinguishes_gray_from_color():
    gray = np.full((20, 20, 3), 128, dtype=np.uint8)
    color = np.zeros((20, 20, 3), dtype=np.uint8)
    color[:, :10, 0] = 255
    color[:, 10:, 1] = 255

    assert hasler_susstrunk(gray) == pytest.approx(0.0)
    assert hasler_susstrunk(color) > 0


def test_provider_selection_skips_unavailable_openvino(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "marquee.ml.embedding.ort.get_available_providers",
        lambda: ["CoreMLExecutionProvider", "CPUExecutionProvider"],
    )
    providers = choose_execution_providers()
    assert providers == ["CoreMLExecutionProvider", "CPUExecutionProvider"]


def test_repeat_run_cleanup_retains_flat_downloads(tmp_path: Path):
    cached = tmp_path / "poster.jpg"
    cached.write_bytes(b"cached")
    (tmp_path / "pipeline.log").write_text("old")
    (tmp_path / "pipeline_run.json").write_text("{}")
    generated = tmp_path / "sha256" / "phash" / "ocr" / "ranked"
    generated.mkdir(parents=True)
    (generated / "old.jpg").write_bytes(b"stale")

    _clear_generated_outputs(tmp_path)

    assert cached.read_bytes() == b"cached"
    assert not (tmp_path / "sha256").exists()
    assert not (tmp_path / "pipeline.log").exists()
    assert not (tmp_path / "pipeline_run.json").exists()


def test_dedup_tiebreak_uses_original_tmdb_resolution(tmp_path: Path):
    lower = tmp_path / "lower.png"
    higher = tmp_path / "higher.png"
    image = Image.new("RGB", (500, 750), color="navy")
    image.save(lower)
    image.save(higher)

    result = PosterDeduper(
        sha256_only=True,
        min_width=0,
        resolution_by_name={
            lower.name: (1000, 1500),
            higher.name: (2000, 3000),
        },
    ).deduplicate([lower, higher])

    assert result.survivors == [higher]
    assert result.removals[0].removed == lower


def test_ocr_rejects_no_text_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    image_path = tmp_path / "blank.jpg"
    Image.new("RGB", (500, 750), color="black").save(image_path)

    class EmptyOCR:
        def predict(self, _image):
            return []

    monkeypatch.setattr(ocr_filter, "_worker_ocr", EmptyOCR())
    monkeypatch.setattr(ocr_filter, "_worker_title_tokens", {"blank"})
    monkeypatch.setattr(ocr_filter, "_worker_director_tokens", set())

    result = ocr_filter._process_image(str(image_path))

    assert not result.accepted
    assert result.reason == "no_text"
    assert result.title_bbox is None


def test_pre_feature_record_serializes_null_feature_blocks(tmp_path: Path):
    record = CandidateScore(
        image_path=tmp_path / "bad.jpg",
        orig_filename="bad.jpg",
        stage_reached="ocr",
        rejection_reason="ocr_error: decode failed",
    )

    payload = record.to_dict()

    assert payload["raw_features"] is None
    assert payload["normalized_features"] is None
    assert payload["stage_reached"] == "ocr"
    assert payload["rejection_reason"] == "ocr_error: decode failed"


@pytest.mark.asyncio
async def test_original_download_failure_keeps_w500(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"w500")
    score = CandidateScore(
        image_path=source,
        orig_filename="source.jpg",
        final_score=0.75,
        rank=1,
        stage_reached="ranked",
    )
    candidate = PosterCandidate(
        file_path="/source.jpg",
        width=1000,
        height=1500,
        aspect_ratio=2 / 3,
        language="en",
        vote_average=7.0,
        vote_count=10,
    )

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url: str):
            raise httpx.ConnectError("CDN unavailable")

    monkeypatch.setattr(
        "marquee.pipeline.output.httpx.AsyncClient",
        lambda **_kwargs: FailingClient(),
    )

    result = await place_ranked(
        [score],
        candidate_map={"source.jpg": candidate},
        ranked_dir=tmp_path / "ranked",
        lower_dir=tmp_path / "lower",
    )

    assert score.image_path.read_bytes() == b"w500"
    assert score.original_download is False
    assert result.original_download_status == {"source.jpg": False}
