"""Focused unit tests for the revised GATE-then-RANK pipeline."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

from marquee.api.routes.test_pipeline import _EXPERIMENTS_DATA, _clear_generated_outputs
from marquee.core.pipeline_config import PipelineSettings
from marquee.core.poster_sources.tmdb import PosterCandidate
from marquee.ml.artifact_codec import unicode_array, unicode_scalar
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
from marquee.pipeline.types import CandidateScore, FeatureVector, OCRCandidateResult


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


def test_pipeline_config_rejects_invalid_ocr_device():
    with pytest.raises(ValueError, match="OCR_DEVICE"):
        PipelineSettings(OCR_DEVICE="cuda")


@pytest.mark.parametrize(
    ("overrides", "width", "reason"),
    [
        ({}, 499, "resolution_floor"),
        # aesthetic below rescue floor — gated even with high knn_sim
        ({"aesthetic": 1.9, "knn_sim": 0.65}, 500, "aesthetic_floor"),
        # aesthetic in 2–4.5 range but knn_sim too low to trigger rescue
        ({"aesthetic": 3.5, "knn_sim": 0.40}, 500, "aesthetic_floor"),
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


def test_gate_aesthetic_rescue_by_knn_sim():
    """High knn_sim rescues a poster with aesthetic below 4.5 but above the rescued floor."""
    result = PosterGate().evaluate(
        _features(aesthetic=3.0, knn_sim=0.65),
        original_width=500,
    )
    assert result.passed


def test_gate_aesthetic_rescue_requires_min_rescued_floor():
    """knn_sim rescue doesn't apply if aesthetic is below the rescued floor (2.0)."""
    result = PosterGate().evaluate(
        _features(aesthetic=1.9, knn_sim=0.65),
        original_width=500,
    )
    assert not result.passed
    assert result.reason == "aesthetic_floor"


def test_taste_store_queries_top_k_and_checks_model(tmp_path: Path):
    profile = tmp_path / "taste_profile.clip-vit-b-32.npz"
    embeddings = np.eye(3, 512, dtype=np.float32)
    centroid = embeddings.mean(axis=0)
    centroid /= np.linalg.norm(centroid)
    np.savez(
        profile,
        embeddings=embeddings,
        poster_names=unicode_array(["a.jpg", "b.jpg", "c.jpg"]),
        centroid_emb=centroid,
        model_name=unicode_scalar("clip-vit-b-32"),
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
    from marquee.ml import hardware

    monkeypatch.setattr(
        "marquee.ml.hardware.ort.get_available_providers",
        lambda: ["CoreMLExecutionProvider", "CPUExecutionProvider"],
    )
    hardware.detect_hardware.cache_clear()
    try:
        providers = choose_execution_providers()
        assert providers == ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    finally:
        hardware.detect_hardware.cache_clear()


def test_hardware_profile_resolves_tiers(monkeypatch: pytest.MonkeyPatch):
    from marquee.ml import hardware

    monkeypatch.setattr(
        "marquee.ml.hardware.ort.get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    hardware.detect_hardware.cache_clear()
    try:
        profile = hardware.detect_hardware()
        assert profile.tier == "cuda"
        assert profile.providers[0] == "CUDAExecutionProvider"
        assert profile.clip_batch_size == 32
        assert profile.ocr_workers >= 1
    finally:
        hardware.detect_hardware.cache_clear()


def test_effective_ocr_workers_caps_cuda_unless_gpu_forced(
    monkeypatch: pytest.MonkeyPatch,
):
    from marquee.ml import hardware

    monkeypatch.setattr(
        "marquee.ml.hardware.ort.get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr(hardware.pipeline_settings, "OCR_WORKERS", 10)
    monkeypatch.setattr(hardware.pipeline_settings, "OCR_DEVICE", "cpu")
    hardware.detect_hardware.cache_clear()
    try:
        assert hardware.effective_ocr_workers() == hardware.detect_hardware().ocr_workers
        monkeypatch.setattr(hardware.pipeline_settings, "OCR_DEVICE", "gpu")
        assert hardware.effective_ocr_workers() == 10
    finally:
        hardware.detect_hardware.cache_clear()


def test_hardware_openvino_alias_forces_cpu_device(monkeypatch: pytest.MonkeyPatch):
    from marquee.ml import hardware

    monkeypatch.setattr(
        "marquee.ml.hardware.ort.get_available_providers",
        lambda: ["OpenVINOExecutionProvider", "CPUExecutionProvider"],
    )
    hardware.detect_hardware.cache_clear()
    try:
        profile = hardware.detect_hardware("openvino-cpu")
        assert profile.tier == "openvino-cpu"
        assert profile.provider_options[0] == {"device_type": "CPU"}
    finally:
        hardware.detect_hardware.cache_clear()


def test_hardware_explicit_unavailable_provider_raises(monkeypatch: pytest.MonkeyPatch):
    from marquee.ml import hardware

    monkeypatch.setattr(
        "marquee.ml.hardware.ort.get_available_providers",
        lambda: ["CPUExecutionProvider"],
    )
    hardware.detect_hardware.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="unavailable"):
            hardware.detect_hardware("cuda")
    finally:
        hardware.detect_hardware.cache_clear()


def test_repeat_run_cleanup_retains_flat_downloads(tmp_path: Path):
    originals = tmp_path / "0-originals"
    originals.mkdir()
    cached = originals / "poster.jpg"
    cached.write_bytes(b"cached")
    (tmp_path / "pipeline.log").write_text("old")
    (tmp_path / "pipeline_run.json").write_text("{}")
    generated = tmp_path / "ranked"
    generated.mkdir()
    (generated / "old.jpg").write_bytes(b"stale")

    _clear_generated_outputs(tmp_path)

    assert cached.read_bytes() == b"cached"
    assert not (tmp_path / "ranked").exists()
    assert not (tmp_path / "pipeline.log").exists()
    assert not (tmp_path / "pipeline_run.json").exists()


def test_pipeline_run_root_is_inside_marquee_experiments():
    expected = (
        Path(__file__).resolve().parents[1]
        / "marquee"
        / "experiments"
        / "runs"
    )
    assert expected == _EXPERIMENTS_DATA


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


def test_ocr_rejects_no_text_per_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Textless posters are always rejected per image — OCR_ACCEPT_NO_TEXT is
    a batch-level fallback, never a per-image accept."""
    image_path = tmp_path / "blank.jpg"
    Image.new("RGB", (500, 750), color="black").save(image_path)

    class EmptyOCR:
        def predict(self, _image):
            return []

    monkeypatch.setattr(ocr_filter, "_worker_ocr", EmptyOCR())
    monkeypatch.setattr(ocr_filter, "_worker_title_tokens", {"blank"})
    monkeypatch.setattr(ocr_filter, "_worker_director_tokens", set())
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_ACCEPT_NO_TEXT", True)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_ENHANCE_RETRY", False)

    result = ocr_filter._process_image(str(image_path))

    assert not result.accepted
    assert result.reason == "no_text"
    assert result.title_bbox is None


def test_ocr_rejects_text_without_title_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A poster whose detected text never matches the title is not a titled
    poster (OCR_REQUIRE_TITLE) even when the residual is insignificant."""
    image_path = tmp_path / "poster.jpg"
    Image.new("RGB", (500, 750), color="black").save(image_path)

    class FragmentOCR:
        def predict(self, _image):
            # One short, insignificant fragment (< 4 chars) — previously
            # accepted with title_bbox=None despite showing no title.
            return [{
                "rec_texts": ["may"],
                "rec_scores": [0.99],
                "rec_polys": [[[10, 10], [60, 10], [60, 30], [10, 30]]],
            }]

    monkeypatch.setattr(ocr_filter, "_worker_ocr", FragmentOCR())
    monkeypatch.setattr(ocr_filter, "_worker_title_tokens", {"avengers"})
    monkeypatch.setattr(ocr_filter, "_worker_director_tokens", set())
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_ENHANCE_RETRY", False)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DETAIL_PASSES", False)

    result = ocr_filter._process_image(str(image_path))
    assert not result.accepted
    assert result.reason == "no_title"

    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_REQUIRE_TITLE", False)
    result = ocr_filter._process_image(str(image_path))
    assert result.accepted


def _ocr_result(name: str, *, accepted: bool, reason: str | None) -> OCRCandidateResult:
    return OCRCandidateResult(
        image_path=Path(name),
        accepted=accepted,
        detected_text="",
        reason=reason,
        title_bbox=None,
    )


def test_no_text_fallback_rescues_only_when_nothing_titled(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_ACCEPT_NO_TEXT", True)

    all_textless = [
        _ocr_result("a.jpg", accepted=False, reason="no_text"),
        _ocr_result("b.jpg", accepted=False, reason="no_title"),
        _ocr_result("c.jpg", accepted=False, reason="text_heavy"),
    ]
    rescued = ocr_filter.PosterTextFilter._apply_no_text_fallback(all_textless)
    assert [r.accepted for r in rescued] == [True, True, False]
    assert rescued[0].reason == "no_text_fallback"
    assert rescued[1].reason == "no_title_fallback"
    assert rescued[2].reason == "text_heavy"


def test_no_text_fallback_skipped_when_titled_survivor_exists(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_ACCEPT_NO_TEXT", True)

    mixed = [
        _ocr_result("titled.jpg", accepted=True, reason=None),
        _ocr_result("textless.jpg", accepted=False, reason="no_text"),
    ]
    results = ocr_filter.PosterTextFilter._apply_no_text_fallback(mixed)
    assert [r.accepted for r in results] == [True, False]


def test_no_text_fallback_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_ACCEPT_NO_TEXT", False)

    results = ocr_filter.PosterTextFilter._apply_no_text_fallback(
        [_ocr_result("a.jpg", accepted=False, reason="no_text")]
    )
    assert not results[0].accepted


def test_load_ocr_respects_cpu_device_when_paddle_cuda_exists(
    monkeypatch: pytest.MonkeyPatch,
):
    created: dict[str, object] = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            created.update(kwargs)

    fake_paddle = types.SimpleNamespace(
        device=types.SimpleNamespace(is_compiled_with_cuda=lambda: True)
    )
    fake_paddleocr = types.SimpleNamespace(PaddleOCR=FakePaddleOCR)
    monkeypatch.setitem(sys.modules, "paddle", fake_paddle)
    monkeypatch.setitem(sys.modules, "paddleocr", fake_paddleocr)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DEVICE", "cpu")

    ocr_filter._load_ocr()

    assert created["device"] == "cpu"


def test_load_ocr_auto_uses_gpu_when_paddle_cuda_exists(
    monkeypatch: pytest.MonkeyPatch,
):
    created: dict[str, object] = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            created.update(kwargs)

    fake_paddle = types.SimpleNamespace(
        device=types.SimpleNamespace(is_compiled_with_cuda=lambda: True)
    )
    fake_paddleocr = types.SimpleNamespace(PaddleOCR=FakePaddleOCR)
    monkeypatch.setitem(sys.modules, "paddle", fake_paddle)
    monkeypatch.setitem(sys.modules, "paddleocr", fake_paddleocr)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DEVICE", "auto")

    ocr_filter._load_ocr()

    assert created["device"] == "gpu"


def test_load_ocr_rejects_forced_gpu_without_paddle_cuda(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakePaddleOCR:
        def __init__(self, **_kwargs):
            raise AssertionError("PaddleOCR should not be constructed")

    fake_paddle = types.SimpleNamespace(
        device=types.SimpleNamespace(is_compiled_with_cuda=lambda: False)
    )
    fake_paddleocr = types.SimpleNamespace(PaddleOCR=FakePaddleOCR)
    monkeypatch.setitem(sys.modules, "paddle", fake_paddle)
    monkeypatch.setitem(sys.modules, "paddleocr", fake_paddleocr)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DEVICE", "gpu")

    with pytest.raises(RuntimeError, match="Paddle CUDA is unavailable"):
        ocr_filter._load_ocr()


class _FakeWorker:
    def __init__(self, name: str, pid: int, *, alive: bool, exitcode: int | None = 0):
        self.name = name
        self.pid = pid
        self.exitcode = exitcode
        self._alive = alive
        self.terminated = False

    def is_alive(self):
        return self._alive

    def terminate(self):
        self.terminated = True
        self._alive = False
        self.exitcode = -15

    def join(self, _timeout: float | None = None):
        return None


def test_join_workers_surfaces_forced_shutdown():
    worker = _FakeWorker("poster-ocr-1", 999999, alive=True, exitcode=None)

    with pytest.raises(RuntimeError, match="forced shutdown"):
        ocr_filter.PosterTextFilter._join_workers([worker])

    assert worker.terminated


def test_system_ocr_status_shape(monkeypatch: pytest.MonkeyPatch):
    from marquee.api.routes import system

    monkeypatch.setattr(system.pipeline_settings, "OCR_DEVICE", "auto")
    monkeypatch.setattr(system.pipeline_settings, "OCR_WORKERS", 0)
    monkeypatch.setattr(system, "effective_ocr_workers", lambda: 3)
    monkeypatch.setattr(system, "paddle_cuda_available", lambda: False)
    monkeypatch.setattr(
        system,
        "active_worker_status",
        lambda: {"active": [], "stale_reaped": []},
    )

    status = system._ocr_status()

    assert status == {
        "device": "auto",
        "configured_workers": 0,
        "effective_workers": 3,
        "paddle_cuda_available": False,
        "workers": {"active": [], "stale_reaped": []},
    }


def test_normalize_official_family_ramp():
    features = _features()
    features.official_family = 0.95
    normalized = normalize_features(features, PipelineSettings())
    assert normalized["official_family"] == pytest.approx(1.0)

    features.official_family = 0.60
    assert normalize_features(features, PipelineSettings())[
        "official_family"
    ] == pytest.approx(0.0)

    features.official_family = None
    assert "official_family" not in normalize_features(features, PipelineSettings())


def test_ocr_big_residual_box_is_significant_despite_garbled_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A huge detected box whose text was garbled to 'mm' (the 70MM watermark
    case) must reject the poster — geometry significance, not word length."""
    image_path = tmp_path / "poster.jpg"
    Image.new("RGB", (500, 750), color="black").save(image_path)

    class WatermarkOCR:
        def predict(self, _image):
            return [{
                "rec_texts": ["avengers", "mm"],
                "rec_scores": [0.95, 0.92],
                "rec_polys": [
                    [[50, 20], [450, 20], [450, 70], [50, 70]],      # title
                    [[40, 300], [460, 300], [460, 380], [40, 380]],  # watermark
                ],
            }]

    monkeypatch.setattr(ocr_filter, "_worker_ocr", WatermarkOCR())
    monkeypatch.setattr(ocr_filter, "_worker_title_tokens", {"avengers"})
    monkeypatch.setattr(ocr_filter, "_worker_director_tokens", set())
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_ENHANCE_RETRY", False)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DETAIL_PASSES", False)

    result = ocr_filter._process_image(str(image_path))
    assert not result.accepted
    assert result.reason == "text_heavy"


def test_dedup_preference_beats_resolution(tmp_path: Path):
    """A titled, on-taste near-dupe survives over a larger textless one."""
    textless_large = tmp_path / "textless.png"
    titled_small = tmp_path / "titled.png"
    image = Image.new("RGB", (500, 750), color="navy")
    image.save(textless_large)
    image.save(titled_small)

    result = PosterDeduper(
        sha256_only=True,
        min_width=0,
        resolution_by_name={
            textless_large.name: (2000, 3000),
            titled_small.name: (1000, 1500),
        },
        preference_by_name={
            textless_large.name: (0, 0, 0.86),
            titled_small.name: (1, 0, 0.81),
        },
    ).deduplicate([textless_large, titled_small])

    assert result.survivors == [titled_small]
    assert result.removals[0].removed == textless_large


def test_ocr_worker_flushes_results_before_native_teardown(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[object] = []

    class FakeQueue:
        def close(self) -> None:
            calls.append("close")

        def join_thread(self) -> None:
            calls.append("join_thread")

    def fake_exit(code: int) -> None:
        calls.append(("exit", code))
        raise RuntimeError("worker exited")

    monkeypatch.setattr(ocr_filter.os, "_exit", fake_exit)

    with pytest.raises(RuntimeError, match="worker exited"):
        ocr_filter._exit_worker(FakeQueue(), 0)

    assert calls == ["close", "join_thread", ("exit", 0)]


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
    )

    assert score.image_path.read_bytes() == b"w500"
    assert score.original_download is False
    assert result.original_download_status == {"source.jpg": False}


# ---------------------------------------------------------------------------
# Taste scoring: weighted k-NN and negative exemplars
# ---------------------------------------------------------------------------


def test_weighted_topk_mean_softmax_favors_nearest():
    from marquee.ml.taste_store import weighted_topk_mean

    sims = np.asarray([0.9, 0.5, 0.5, 0.5], dtype=np.float32)
    plain = weighted_topk_mean(sims, k=4, weighting="mean")
    weighted = weighted_topk_mean(sims, k=4, weighting="softmax", temperature=0.1)
    assert plain == pytest.approx(0.6)
    assert weighted > plain  # the close neighbour dominates
    assert weighted < 0.9  # but it is not a hard max


def _write_profile(
    path: Path,
    embeddings: np.ndarray,
    *,
    neg_embeddings: np.ndarray | None = None,
) -> None:
    centroid = embeddings.mean(axis=0)
    centroid /= np.linalg.norm(centroid)
    payload = {
        "embeddings": embeddings,
        "poster_names": unicode_array([f"{i}.jpg" for i in range(len(embeddings))]),
        "centroid_emb": centroid,
        "model_name": unicode_scalar("clip-vit-b-32"),
    }
    if neg_embeddings is not None:
        payload["neg_embeddings"] = neg_embeddings
        payload["neg_poster_names"] = unicode_array(
            [f"neg{i}.jpg" for i in range(len(neg_embeddings))]
        )
    np.savez(path, **payload)


def test_style_score_without_negatives_matches_positive_knn(tmp_path: Path):
    profile = tmp_path / "taste_profile.clip-vit-b-32.npz"
    embeddings = np.eye(3, 512, dtype=np.float32)
    _write_profile(profile, embeddings)

    store = NumpyTasteStore(profile)
    query = embeddings[0]
    assert store.negative_size == 0
    assert store.style_score(query, k=1) == pytest.approx(1.0)


def test_style_score_penalizes_junk_lookalikes(tmp_path: Path):
    profile = tmp_path / "taste_profile.clip-vit-b-32.npz"
    positives = np.eye(2, 512, dtype=np.float32)
    # One negative exemplar along a third axis: a query matching it exactly
    # is closer to the disliked set than the liked set.
    negative = np.zeros((1, 512), dtype=np.float32)
    negative[0, 2] = 1.0
    _write_profile(profile, positives, neg_embeddings=negative)

    store = NumpyTasteStore(profile)
    junk_query = negative[0]
    liked_query = positives[0]

    assert store.negative_size == 1
    # Liked query: closer to positives, no penalty applies.
    assert store.style_score(liked_query, k=1) == pytest.approx(1.0)
    # Junk query: pos knn = 0, neg knn = 1 -> penalized below the raw pos score.
    assert store.style_score(junk_query, k=1) < 0.0


# ---------------------------------------------------------------------------
# OCR text-heavy gate thresholds
# ---------------------------------------------------------------------------


def _fake_ocr_with_boxes(boxes: list[tuple[str, float, list[list[float]]]]):
    class FakeOCR:
        def predict(self, _image):
            return [
                {
                    "rec_texts": [text for text, _, _ in boxes],
                    "rec_scores": [score for _, score, _ in boxes],
                    "rec_polys": [np.asarray(poly, dtype=np.float32) for _, _, poly in boxes],
                }
            ]

    return FakeOCR()


def _poly(x: float, y: float, w: float, h: float) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _run_ocr_with_boxes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boxes: list[tuple[str, float, list[list[float]]]],
):
    image_path = tmp_path / "poster.jpg"
    Image.new("RGB", (500, 750), color="navy").save(image_path)
    monkeypatch.setattr(ocr_filter, "_worker_ocr", _fake_ocr_with_boxes(boxes))
    monkeypatch.setattr(ocr_filter, "_worker_title_tokens", {"inception"})
    monkeypatch.setattr(ocr_filter, "_worker_director_tokens", set())
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DETAIL_PASSES", False)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_ENHANCE_RETRY", False)
    return ocr_filter._process_image(str(image_path))


def test_ocr_default_is_strict_title_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Project target: ONLY the movie title as text. One tagline box gates."""
    result = _run_ocr_with_boxes(
        tmp_path,
        monkeypatch,
        [
            ("INCEPTION", 0.95, _poly(100, 600, 300, 50)),
            ("your mind is the scene of the crime", 0.9, _poly(100, 100, 250, 20)),
        ],
    )
    assert not result.accepted
    assert result.reason == "text_heavy"
    assert result.title_bbox is not None


def test_ocr_title_only_poster_passes_strict_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    result = _run_ocr_with_boxes(
        tmp_path,
        monkeypatch,
        [("INCEPTION", 0.95, _poly(100, 600, 300, 50))],
    )
    assert result.accepted
    assert result.title_bbox is not None
    assert not result.residual_boxes


def test_ocr_residual_threshold_knob_tolerates_taglines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Raising OCR_MAX_RESIDUAL_BOXES demotes a tagline to a rank penalty."""
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_MAX_RESIDUAL_BOXES", 2)
    result = _run_ocr_with_boxes(
        tmp_path,
        monkeypatch,
        [
            ("INCEPTION", 0.95, _poly(100, 600, 300, 50)),
            ("your mind is the scene of the crime", 0.9, _poly(100, 100, 250, 20)),
        ],
    )
    assert result.accepted
    assert len(result.residual_boxes) == 1


def test_ocr_many_residual_boxes_still_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    boxes = [("INCEPTION", 0.95, _poly(100, 600, 300, 50))]
    boxes += [
        (f"completely unrelated promotional words {i}", 0.9, _poly(50, 50 + i * 60, 300, 25))
        for i in range(4)
    ]
    result = _run_ocr_with_boxes(tmp_path, monkeypatch, boxes)
    assert not result.accepted
    assert result.reason == "text_heavy"


def test_ocr_large_residual_area_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A single huge text block (credits wall) exceeds the area threshold."""
    result = _run_ocr_with_boxes(
        tmp_path,
        monkeypatch,
        [
            ("INCEPTION", 0.95, _poly(100, 600, 300, 50)),
            # 400x100 = 40,000 px on a 500x750 image -> ~10.7% > 4% default
            ("warner bros pictures presents legendary pictures", 0.9, _poly(50, 100, 400, 100)),
        ],
    )
    assert not result.accepted
    assert result.reason == "text_heavy"


# ---------------------------------------------------------------------------
# Normalization: neutral title colorfulness when no title box was found
# ---------------------------------------------------------------------------


def test_no_title_box_normalizes_to_neutral_colorfulness():
    found = _features(title_colorfulness=0.0)
    missing = _features(title_colorfulness=0.0)
    missing.title_found = False

    normalize_features(found)
    normalize_features(missing)

    assert found.normalized["title_colorfulness"] == 0.0
    assert missing.normalized["title_colorfulness"] == pytest.approx(0.5)


def test_title_found_is_not_a_scorer_feature():
    features = _features()
    assert "title_found" not in features.raw_values()


# ---------------------------------------------------------------------------
# Aesthetic head: numpy sidecar (no torch at runtime)
# ---------------------------------------------------------------------------


def test_aesthetic_head_loads_npz_without_torch(tmp_path: Path):
    from marquee.ml.aesthetic import AestheticPredictor

    weight = np.zeros(512, dtype=np.float32)
    weight[0] = 5.0
    np.savez(tmp_path / "head.npz", weight=weight, bias=np.float32(2.0))

    predictor = AestheticPredictor(tmp_path / "head.npz")
    embedding = np.zeros(512, dtype=np.float32)
    embedding[0] = 1.0
    assert predictor.score(embedding) == pytest.approx(7.0)
    batch = predictor.score_batch(np.stack([embedding, -embedding]))
    assert batch == pytest.approx([7.0, -3.0])


# ---------------------------------------------------------------------------
# CLIP encoder: batching with fixed-batch fallback
# ---------------------------------------------------------------------------


class _FakeOnnxSession:
    def __init__(self, batch_dim: object):
        self._batch_dim = batch_dim
        self.run_calls: list[int] = []

    def get_inputs(self):
        class _Input:
            name = "pixel_values"
            shape = [self._batch_dim, 3, 224, 224]

        _Input.shape = [self._batch_dim, 3, 224, 224]
        return [_Input()]

    def run(self, _outputs, feed):
        batch = feed["pixel_values"].shape[0]
        if isinstance(self._batch_dim, int):
            assert batch <= self._batch_dim
        self.run_calls.append(batch)
        vectors = np.tile(
            np.arange(1, 513, dtype=np.float32), (batch, 1)
        )
        return [vectors]


def test_encode_batch_falls_back_to_loop_on_fixed_batch_model():
    from marquee.ml.embedding import CLIPImageEncoder

    session = _FakeOnnxSession(batch_dim=1)
    encoder = CLIPImageEncoder(session=session)
    pixels = [np.zeros((1, 3, 224, 224), dtype=np.float32) for _ in range(3)]

    result = encoder.encode_batch(pixels)

    assert result.shape == (3, 512)
    assert session.run_calls == [1, 1, 1]
    assert np.linalg.norm(result, axis=1) == pytest.approx([1.0, 1.0, 1.0])


def test_encode_batch_uses_dynamic_batching(monkeypatch: pytest.MonkeyPatch):
    from marquee.ml.embedding import CLIPImageEncoder

    monkeypatch.setattr(
        "marquee.ml.embedding.effective_clip_batch_size", lambda: 2
    )
    session = _FakeOnnxSession(batch_dim="batch")
    encoder = CLIPImageEncoder(session=session)
    pixels = [np.zeros((1, 3, 224, 224), dtype=np.float32) for _ in range(5)]

    result = encoder.encode_batch(pixels)

    assert result.shape == (5, 512)
    assert session.run_calls == [2, 2, 1]


# ---------------------------------------------------------------------------
# Gate split: stage-appropriate evaluation
# ---------------------------------------------------------------------------


def test_gate_metadata_only_needs_width():
    gate = PosterGate()
    assert not gate.evaluate_metadata(original_width=499).passed
    assert gate.evaluate_metadata(original_width=500).passed


def test_gate_style_does_not_consider_resolution():
    """Style gating runs before detail features; width is gated separately."""
    result = PosterGate().evaluate_style(_features(aesthetic=6.0, knn_sim=0.65))
    assert result.passed


def test_gate_detail_fan_junk_combo():
    config = PipelineSettings(GATE_FAN_JUNK_ENABLED=True)
    gate = PosterGate(config)
    junk = _features(aesthetic=4.6, provenance=0.3, resolution=0.5)
    good = _features(aesthetic=6.5, provenance=0.3, resolution=0.5)
    assert not gate.evaluate_detail(junk).passed
    assert gate.evaluate_detail(junk).reason == "fan_junk_combo"
    assert gate.evaluate_detail(good).passed
