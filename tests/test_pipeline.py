"""Focused unit tests for the revised GATE-then-RANK pipeline."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

from marquee.config import Settings
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
from marquee.pipeline.runner import _RUNS_WORK_DATA, _clear_generated_outputs
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
    monkeypatch.setattr(hardware.pipeline_settings, "EXECUTION_PROVIDER", "auto")
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


def test_effective_ocr_workers_honors_config_else_hardware_default(
    monkeypatch: pytest.MonkeyPatch,
):
    """`effective_ocr_workers()` honors an explicit ``OCR_WORKERS`` (bounded to a
    sane 16 ceiling) on every device/tier, and only falls back to the
    hardware-tier default when unset. Deterministic via a fixed ``cpu_count``.

    (Replaces the former ``caps_cuda_unless_gpu_forced`` test, which asserted a
    device-conditional cap that the current policy does not implement and
    compared against the host's live ``os.cpu_count()``.)"""
    from marquee.ml import hardware

    monkeypatch.setattr(
        "marquee.ml.hardware.ort.get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    # Fix the core count so the CUDA auto default is deterministic:
    # _auto_ocr_workers caps CUDA at min(3, cpu_count // 4) -> 3 for 16 cores.
    monkeypatch.setattr("os.cpu_count", lambda: 16)
    hardware.detect_hardware.cache_clear()
    try:
        # An explicit worker count wins on every device and is capped at 16.
        for device in ("cpu", "gpu"):
            monkeypatch.setattr(hardware.pipeline_settings, "OCR_DEVICE", device)
            monkeypatch.setattr(hardware.pipeline_settings, "OCR_WORKERS", 10)
            assert hardware.effective_ocr_workers() == 10
            monkeypatch.setattr(hardware.pipeline_settings, "OCR_WORKERS", 99)
            assert hardware.effective_ocr_workers() == 16  # sane upper bound

        # Unset (0) falls back to the CUDA hardware-tier default (min(3, 16 // 4)).
        monkeypatch.setattr(hardware.pipeline_settings, "OCR_WORKERS", 0)
        hardware.detect_hardware.cache_clear()
        assert hardware.detect_hardware().ocr_workers == 3
        assert hardware.effective_ocr_workers() == 3
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


def test_pipeline_run_root_is_inside_data():
    assert Settings().runs_work_path == _RUNS_WORK_DATA


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
            return [
                {
                    "rec_texts": ["may"],
                    "rec_scores": [0.99],
                    "rec_polys": [[[10, 10], [60, 10], [60, 30], [10, 30]]],
                }
            ]

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


def _ocr_poly(left: float, top: float, right: float, bottom: float) -> list[list[float]]:
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def _ocr_box(
    text: str,
    left: float,
    top: float,
    right: float,
    bottom: float,
    *,
    score: float = 0.95,
) -> tuple[str, float, list[list[float]]]:
    return text, score, _ocr_poly(left, top, right, bottom)


def _letter_spaced_boxes(rows: list[list[str]]) -> list[tuple[str, float, list[list[float]]]]:
    boxes: list[tuple[str, float, list[list[float]]]] = []
    for row_index, row in enumerate(rows):
        y = 500 + row_index * 70
        for col_index, text in enumerate(row):
            x = 50 + col_index * 48
            boxes.append(_ocr_box(text, x, y, x + 28, y + 44))
    return boxes


class _StaticOCR:
    def __init__(self, *responses: list[tuple[str, float, list[list[float]]]]):
        self.responses = list(responses)
        self.calls = 0

    def predict(self, _image):
        if not self.responses:
            return []
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        boxes = self.responses[index]
        if not boxes:
            return []
        return [
            {
                "rec_texts": [text for text, _score, _poly in boxes],
                "rec_scores": [score for _text, score, _poly in boxes],
                "rec_polys": [poly for _text, _score, poly in boxes],
            }
        ]


def _run_fake_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    title: str,
    responses: list[list[tuple[str, float, list[list[float]]]]],
    recovery_enabled: bool = False,
) -> tuple[OCRCandidateResult, _StaticOCR]:
    image_path = tmp_path / "poster.jpg"
    Image.new("RGB", (500, 750), color="black").save(image_path)
    title_text = ocr_filter._normalise(title)
    title_tokens = set(title_text.split())
    ocr_filter._add_digit_words(title_tokens)
    fake_ocr = _StaticOCR(*responses)

    monkeypatch.setattr(ocr_filter, "_worker_ocr", fake_ocr)
    monkeypatch.setattr(ocr_filter, "_worker_title_text", title_text)
    monkeypatch.setattr(ocr_filter, "_worker_title_tokens", title_tokens)
    monkeypatch.setattr(ocr_filter, "_worker_director_tokens", set())
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DETAIL_PASSES", False)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_ENHANCE_RETRY", False)
    monkeypatch.setattr(
        ocr_filter.pipeline_settings, "OCR_TITLE_RECOVERY_ENABLED", recovery_enabled
    )
    monkeypatch.setattr(
        ocr_filter.pipeline_settings,
        "OCR_TITLE_RECOVERY_CONFIDENCE_THRESHOLD",
        0.50,
    )
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_TEXT_MODE", "title_only")
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_REQUIRE_TITLE", True)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_MAX_RESIDUAL_BOXES", 0)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_MAX_RESIDUAL_AREA_FRACTION", 0.0)

    result = ocr_filter._process_image(
        str(image_path),
        title_tokens,
        set(),
        title_text=title_text,
    )
    return result, fake_ocr


@pytest.mark.parametrize(
    ("title", "rows"),
    [
        ("Alien Romulus", [list("ALIEN"), list("ROMULUS")]),
        ("Dune", [list("DUNE")]),
        ("Moana 2", [list("MOANA2")]),
    ],
)
def test_ocr_letter_spaced_title_fragments_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    title: str,
    rows: list[list[str]],
):
    result, _fake_ocr = _run_fake_ocr(
        tmp_path,
        monkeypatch,
        title=title,
        responses=[_letter_spaced_boxes(rows)],
    )

    assert result.accepted
    assert result.reason is None
    assert result.title_bbox is not None
    assert result.residual_boxes == []
    assert any(box["is_title_fragment"] for box in result.diagnostics["detected_boxes"])


def test_ocr_partial_title_boxes_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    result, _fake_ocr = _run_fake_ocr(
        tmp_path,
        monkeypatch,
        title="A Quiet Place Day One",
        responses=[
            [
                _ocr_box("QUIET", 55, 500, 155, 550),
                _ocr_box("T PLACE", 165, 500, 295, 550),
                _ocr_box("DAY ONE", 305, 500, 455, 550),
            ]
        ],
    )

    assert result.accepted
    assert result.reason is None
    assert result.residual_boxes == []
    trace_by_text = {box["text"]: box for box in result.diagnostics["detected_boxes"]}
    assert trace_by_text["T PLACE"]["is_title"]
    assert trace_by_text["T PLACE"]["title_match_source"] in {"box", "line_window"}


@pytest.mark.parametrize(
    ("title", "residual_text"),
    [
        ("A Quiet Place Day One", "ONLY IN THEATERS"),
        ("A Quiet Place Day One", "MARVEL STUDIOS"),
        ("A Quiet Place Day One", "DIRECTED BY"),
        ("A Quiet Place Day One", "BE QUIET"),
    ],
)
def test_ocr_big_non_title_residuals_still_reject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    title: str,
    residual_text: str,
):
    result, _fake_ocr = _run_fake_ocr(
        tmp_path,
        monkeypatch,
        title=title,
        responses=[
            [
                _ocr_box(title, 45, 500, 455, 560),
                _ocr_box(residual_text, 40, 90, 460, 170),
            ]
        ],
    )

    assert not result.accepted
    assert result.reason == "text_heavy"
    residual_trace = [
        box for box in result.diagnostics["detected_boxes"] if box["text"] == residual_text
    ][0]
    assert not residual_trace["is_title"]
    assert residual_trace["is_residual"]
    assert residual_trace["is_significant"]


def test_ocr_lone_letter_residual_not_significant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A single stray glyph — a spaced-title letter that escaped the title band —
    is typography, not a text block, and must not gate-reject a titled poster.
    (Multi-char garble like "mm" stays significant; see the watermark test.)"""
    result, _fake_ocr = _run_fake_ocr(
        tmp_path,
        monkeypatch,
        title="Alien Romulus",
        responses=[
            [
                _ocr_box("ALIEN ROMULUS", 45, 600, 455, 660),
                _ocr_box("N", 30, 60, 120, 150),  # big lone letter, far from title
            ]
        ],
    )

    assert result.accepted
    assert result.reason is None
    lone = [box for box in result.diagnostics["detected_boxes"] if box["text"] == "N"][0]
    assert lone["is_residual"]
    assert not lone["is_significant"]


def test_ocr_spaced_title_partial_read_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A heavily-misread spaced title where OCR only recovered some glyphs, in
    scrambled reading order, still passes: a wide, tall band of large letters that
    all belong to the title is the title even at partial coverage (the
    high-precision band rescue). The order-preserving matchers cannot match this
    scramble, so only the order-independent band detector saves it."""
    # Only 5 of the 10 distinct "ALIEN ROMULUS" letters survive (coverage 0.50),
    # every one belongs to the title (precision 1.0), and their order is scrambled
    # so it is not an ordered subsequence of the title.
    result, _fake_ocr = _run_fake_ocr(
        tmp_path,
        monkeypatch,
        title="Alien Romulus",
        responses=[_letter_spaced_boxes([["S", "M", "R", "N", "A"]])],
    )

    assert result.accepted
    assert result.reason is None
    assert result.title_bbox is not None
    assert result.residual_boxes == []
    assert any(
        box["title_match_source"] == "letter_band" for box in result.diagnostics["detected_boxes"]
    )


def test_ocr_no_text_recovery_accepts_only_recovered_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    result, fake_ocr = _run_fake_ocr(
        tmp_path,
        monkeypatch,
        title="Dune",
        responses=[
            [],
            _letter_spaced_boxes([list("DUNE")]),
        ],
        recovery_enabled=True,
    )

    assert fake_ocr.calls == 2
    assert result.accepted
    assert result.reason is None
    assert result.diagnostics["title_recovery"]["triggered"] is True
    assert result.diagnostics["title_recovery"]["recovered_title"] is True
    assert {box["pass"] for box in result.diagnostics["detected_boxes"]} == {"title_recovery"}


def test_ocr_no_text_recovery_ignores_non_title_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    result, fake_ocr = _run_fake_ocr(
        tmp_path,
        monkeypatch,
        title="Dune",
        responses=[
            [],
            [_ocr_box("ONLY IN THEATERS", 40, 90, 460, 170)],
        ],
        recovery_enabled=True,
    )

    assert fake_ocr.calls == 2
    assert not result.accepted
    assert result.reason == "no_text"
    assert result.detected_text == ""
    assert result.diagnostics["detected_boxes"] == []
    assert result.diagnostics["title_recovery"]["triggered"] is True
    assert result.diagnostics["title_recovery"]["recovered_title"] is False


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


def test_no_text_fallback_default_blocks_legacy_rescue(monkeypatch: pytest.MonkeyPatch):
    default_settings = PipelineSettings()
    monkeypatch.setattr(
        ocr_filter.pipeline_settings,
        "OCR_ACCEPT_NO_TEXT",
        default_settings.OCR_ACCEPT_NO_TEXT,
    )

    results = ocr_filter.PosterTextFilter._apply_no_text_fallback(
        [_ocr_result("ash-style.jpg", accepted=False, reason="no_title")]
    )

    assert default_settings.OCR_ACCEPT_NO_TEXT is False
    assert not results[0].accepted


def test_load_ocr_respects_cpu_device_when_paddle_cuda_exists(
    monkeypatch: pytest.MonkeyPatch,
):
    created: dict[str, object] = {}
    probe: dict[str, object] = {}
    cuda_device = object()

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            created.update(kwargs)

    def zeros(shape, dtype=None, *, device=None):
        probe["shape"] = shape
        probe["dtype"] = dtype
        probe["device"] = device

    def synchronize(device):
        probe["synchronized"] = device

    fake_paddle = types.SimpleNamespace(
        device=types.SimpleNamespace(
            is_compiled_with_cuda=lambda: True,
            cuda=types.SimpleNamespace(device_count=lambda: 1),
            synchronize=synchronize,
        ),
        CUDAPlace=lambda index: cuda_device if index == 0 else None,
        zeros=zeros,
    )
    fake_paddleocr = types.SimpleNamespace(PaddleOCR=FakePaddleOCR)
    monkeypatch.setitem(sys.modules, "paddle", fake_paddle)
    monkeypatch.setitem(sys.modules, "paddleocr", fake_paddleocr)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DEVICE", "cpu")

    ocr_filter._load_ocr()

    assert created["device"] == "cpu"
    assert probe == {
        "shape": [1],
        "dtype": "float32",
        "device": cuda_device,
        "synchronized": cuda_device,
    }


def test_load_ocr_auto_uses_gpu_when_paddle_cuda_exists(
    monkeypatch: pytest.MonkeyPatch,
):
    created: dict[str, object] = {}
    probe: dict[str, object] = {}
    cuda_device = object()

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            created.update(kwargs)

    def zeros(shape, dtype=None, *, device=None):
        probe["shape"] = shape
        probe["dtype"] = dtype
        probe["device"] = device

    def synchronize(device):
        probe["synchronized"] = device

    fake_paddle = types.SimpleNamespace(
        device=types.SimpleNamespace(
            is_compiled_with_cuda=lambda: True,
            cuda=types.SimpleNamespace(device_count=lambda: 1),
            synchronize=synchronize,
        ),
        CUDAPlace=lambda index: cuda_device if index == 0 else None,
        zeros=zeros,
    )
    fake_paddleocr = types.SimpleNamespace(PaddleOCR=FakePaddleOCR)
    monkeypatch.setitem(sys.modules, "paddle", fake_paddle)
    monkeypatch.setitem(sys.modules, "paddleocr", fake_paddleocr)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DEVICE", "auto")

    ocr_filter._load_ocr()

    assert created["device"] == "gpu:0"
    assert probe == {
        "shape": [1],
        "dtype": "float32",
        "device": cuda_device,
        "synchronized": cuda_device,
    }


def test_load_ocr_auto_falls_back_to_cpu_when_cuda_wheel_has_zero_devices(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    created: dict[str, object] = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            created.update(kwargs)

    fake_paddle = types.SimpleNamespace(
        device=types.SimpleNamespace(
            is_compiled_with_cuda=lambda: True,
            cuda=types.SimpleNamespace(device_count=lambda: 0),
        )
    )
    monkeypatch.setitem(sys.modules, "paddle", fake_paddle)
    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=FakePaddleOCR))
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DEVICE", "auto")

    ocr_filter._load_ocr()

    assert created["device"] == "cpu"
    assert "zero usable CUDA devices" in caplog.text


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

    with pytest.raises(RuntimeError, match="Paddle CUDA is unusable"):
        ocr_filter._load_ocr()


def test_load_ocr_rejects_forced_gpu_when_cuda_wheel_has_zero_devices(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_paddle = types.SimpleNamespace(
        device=types.SimpleNamespace(
            is_compiled_with_cuda=lambda: True,
            cuda=types.SimpleNamespace(device_count=lambda: 0),
        )
    )
    monkeypatch.setitem(sys.modules, "paddle", fake_paddle)
    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=object))
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DEVICE", "gpu")

    with pytest.raises(RuntimeError, match="zero usable CUDA devices"):
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
    plan = {
        "requested": "auto",
        "gpu_build": False,
        "gpus": [],
        "expected_device": "cpu",
        "error": None,
        "confirmed": False,
    }
    monkeypatch.setattr(system, "ocr_device_plan", lambda: plan)

    status = system._ocr_status()

    assert status == {
        "device": "auto",
        "configured_workers": 0,
        "effective_workers": 3,
        "paddle_cuda_available": False,
        "workers": {"active": [], "stale_reaped": []},
        "plan": plan,
    }


def test_ocr_device_plan_reports_gpu_when_wheel_and_card_present(
    monkeypatch: pytest.MonkeyPatch,
):
    """auto mode predicts the GPU from the wheel + NVML, and flags it unconfirmed."""
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DEVICE", "auto")
    monkeypatch.setattr(ocr_filter, "paddle_gpu_build", lambda: True)
    monkeypatch.setattr(
        "marquee.core.system_metrics.gpu_inventory",
        lambda: [{"index": 0, "name": "NVIDIA GeForce RTX 3070", "vram_total": 1, "vram_free": 1}],
    )

    plan = ocr_filter.ocr_device_plan()

    assert plan["expected_device"] == "gpu:0"
    assert plan["confirmed"] is False
    assert plan["error"] is None
    assert plan["gpus"][0]["name"] == "NVIDIA GeForce RTX 3070"


def test_ocr_device_plan_surfaces_forced_gpu_without_a_card(monkeypatch: pytest.MonkeyPatch):
    """OCR_DEVICE=gpu with nothing usable must report the error, not guess a device."""
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DEVICE", "gpu")
    monkeypatch.setattr(ocr_filter, "paddle_gpu_build", lambda: False)
    monkeypatch.setattr("marquee.core.system_metrics.gpu_inventory", lambda: [])

    plan = ocr_filter.ocr_device_plan()

    assert plan["expected_device"] is None
    assert plan["error"] is not None
    assert "OCR_DEVICE=gpu" in plan["error"]


def test_normalize_official_family_ramp():
    features = _features()
    features.official_family = 0.95
    normalized = normalize_features(features, PipelineSettings())
    assert normalized["official_family"] == pytest.approx(1.0)

    features.official_family = 0.60
    assert normalize_features(features, PipelineSettings())["official_family"] == pytest.approx(0.0)

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
            return [
                {
                    "rec_texts": ["avengers", "mm"],
                    "rec_scores": [0.95, 0.92],
                    "rec_polys": [
                        [[50, 20], [450, 20], [450, 70], [50, 70]],  # title
                        [[40, 300], [460, 300], [460, 380], [40, 380]],  # watermark
                    ],
                }
            ]

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
        vectors = np.tile(np.arange(1, 513, dtype=np.float32), (batch, 1))
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

    monkeypatch.setattr("marquee.ml.embedding.effective_clip_batch_size", lambda: 2)
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


class _LifecycleQueue:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.puts = []
        self.close_calls = 0
        self.join_thread_calls = 0

    def put(self, item):
        self.puts.append(item)

    def get(self, *, timeout=None):
        del timeout
        return self.messages.pop(0)

    def close(self):
        self.close_calls += 1

    def join_thread(self):
        self.join_thread_calls += 1


class _StartingWorker(_FakeWorker):
    def __init__(
        self,
        name: str,
        pid: int | None,
        *,
        start_error: BaseException | None = None,
    ):
        super().__init__(name, pid, alive=False, exitcode=None)
        self.start_error = start_error
        self.started = False

    def start(self):
        if self.start_error is not None:
            raise self.start_error
        self.started = True
        self._alive = True


class _StartContext:
    def __init__(self, workers):
        self.workers = list(workers)
        self.queues = []

    def Queue(self):  # noqa: N802 - mirrors multiprocessing context API
        worker_queue = _LifecycleQueue()
        self.queues.append(worker_queue)
        return worker_queue

    def Process(self, *, target, args, name):  # noqa: N802 - multiprocessing API
        del target, args
        worker = self.workers.pop(0)
        assert worker.name == name
        return worker


def test_filter_batch_warm_pool_matches_inline(monkeypatch, tmp_path):
    expected = [_ocr_result("poster.jpg", accepted=True, reason=None)]
    calls = []

    def run_inline(items, *, num_workers=None, progress=None):
        calls.append(("inline", list(items), num_workers, progress))
        return expected

    def run_warm(pool, items, *, progress=None):
        calls.append(("warm", pool, list(items), progress))
        return expected

    monkeypatch.setattr(
        ocr_filter.PosterTextFilter,
        "run_ocr_batch",
        staticmethod(run_inline),
    )
    monkeypatch.setattr(
        ocr_filter.PosterTextFilter,
        "run_ocr_tasks",
        staticmethod(run_warm),
    )

    text_filter = ocr_filter.PosterTextFilter("Example")
    image = tmp_path / "poster.jpg"
    pool = ocr_filter.OcrPool([], _LifecycleQueue(), _LifecycleQueue())

    inline = text_filter.filter_batch([image])
    warm = text_filter.filter_batch([image], pool=pool)

    assert warm == inline == expected
    assert [call[0] for call in calls] == ["inline", "warm"]


def test_inline_ocr_teardown_preserves_processing_failure(monkeypatch):
    pool = ocr_filter.OcrPool([], _LifecycleQueue(), _LifecycleQueue())
    monkeypatch.setattr(
        ocr_filter.PosterTextFilter,
        "start_ocr_pool",
        staticmethod(lambda _workers=None: pool),
    )
    monkeypatch.setattr(
        ocr_filter.PosterTextFilter,
        "run_ocr_tasks",
        staticmethod(
            lambda _pool, _items, progress=None, on_item=None: (_ for _ in ()).throw(
                ValueError("ocr failed")
            )
        ),
    )
    monkeypatch.setattr(
        ocr_filter.PosterTextFilter,
        "stop_ocr_pool",
        staticmethod(lambda _pool: (_ for _ in ()).throw(RuntimeError("teardown failed"))),
    )

    with pytest.raises(ValueError, match="ocr failed"):
        ocr_filter.PosterTextFilter.run_ocr_batch([(Path("poster.jpg"), {"poster"}, set())])


def test_ocr_pool_startup_cleanup_is_atomic_on_base_exception(monkeypatch):
    first = _StartingWorker("poster-ocr-1", 987651)
    second = _StartingWorker(
        "poster-ocr-2",
        None,
        start_error=KeyboardInterrupt("start interrupted"),
    )
    context = _StartContext([first, second])
    monkeypatch.setattr(ocr_filter.multiprocessing, "get_context", lambda _mode: context)
    monkeypatch.setattr(ocr_filter, "effective_ocr_omp_threads", lambda _workers: 1)

    with pytest.raises(KeyboardInterrupt, match="start interrupted"):
        ocr_filter.PosterTextFilter.start_ocr_pool(2)

    assert first.started
    assert first.terminated
    assert 987651 not in ocr_filter._active_worker_pids
    assert len(context.queues) == 2
    assert all(worker_queue.close_calls == 1 for worker_queue in context.queues)
    assert all(worker_queue.join_thread_calls == 1 for worker_queue in context.queues)


def test_ocr_pool_startup_cleanup_failure_is_not_downgraded(monkeypatch):
    worker = _StartingWorker(
        "poster-ocr-1",
        None,
        start_error=RuntimeError("worker start failed"),
    )
    context = _StartContext([worker])
    monkeypatch.setattr(ocr_filter.multiprocessing, "get_context", lambda _mode: context)
    monkeypatch.setattr(ocr_filter, "effective_ocr_omp_threads", lambda _workers: 1)
    monkeypatch.setattr(
        ocr_filter.PosterTextFilter,
        "stop_ocr_pool",
        staticmethod(
            lambda _pool: (_ for _ in ()).throw(
                ocr_filter.OcrPoolTeardownError("worker survived cleanup")
            )
        ),
    )

    with pytest.raises(ocr_filter.OcrPoolTeardownError, match="could not be certified"):
        ocr_filter.PosterTextFilter.start_ocr_pool(1)


def test_ocr_pool_readiness_failure_cleans_every_started_worker(monkeypatch):
    workers = [
        _StartingWorker("poster-ocr-1", 987652),
        _StartingWorker("poster-ocr-2", 987653),
    ]
    context = _StartContext(workers)
    monkeypatch.setattr(ocr_filter.multiprocessing, "get_context", lambda _mode: context)
    monkeypatch.setattr(ocr_filter, "effective_ocr_omp_threads", lambda _workers: 1)
    monkeypatch.setattr(
        ocr_filter.PosterTextFilter,
        "_wait_for_workers_ready",
        staticmethod(lambda _queue, _workers: (_ for _ in ()).throw(RuntimeError("not ready"))),
    )

    with pytest.raises(RuntimeError, match="not ready"):
        ocr_filter.PosterTextFilter.start_ocr_pool(2)

    assert all(worker.started and worker.terminated for worker in workers)
    assert not ({987652, 987653} & ocr_filter._active_worker_pids.keys())
    assert all(worker_queue.close_calls == 1 for worker_queue in context.queues)


def test_completed_ocr_pool_can_be_torn_down_twice():
    result = _ocr_result("poster.jpg", accepted=True, reason=None)
    worker = _FakeWorker("poster-ocr-1", 987654, alive=False, exitcode=0)
    task_queue = _LifecycleQueue()
    result_queue = _LifecycleQueue([(ocr_filter._WORKER_RESULT, 0, result)])
    pool = ocr_filter.OcrPool([worker], task_queue, result_queue)
    ocr_filter._register_worker(worker)

    actual = ocr_filter.PosterTextFilter.run_ocr_tasks(
        pool,
        [(Path("poster.jpg"), {"poster"}, set())],
    )
    ocr_filter.PosterTextFilter.stop_ocr_pool(pool)
    ocr_filter.PosterTextFilter.stop_ocr_pool(pool)

    assert actual == [result]
    assert 987654 not in ocr_filter._active_worker_pids
    assert task_queue.close_calls == 2
    assert result_queue.close_calls == 2


@pytest.mark.asyncio
async def test_preload_failure_supplies_none_to_inline_ocr_path(monkeypatch, tmp_path):
    from marquee.pipeline import orchestrator
    from marquee.pipeline.runner import FetchOutcome, SyncOutcome

    start_calls = []

    def fail_preload():
        start_calls.append(True)
        raise RuntimeError("warmup failed")

    class FakeTMDBClient:
        def __init__(self, *, read_access_token):
            assert read_access_token == "token"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

    image = tmp_path / "poster.jpg"
    candidate = PosterCandidate(
        file_path="/poster.jpg",
        width=500,
        height=750,
        aspect_ratio=2 / 3,
        language=None,
        vote_average=0.0,
        vote_count=0,
    )
    record = CandidateScore(image_path=image, orig_filename=image.name)
    fetch = FetchOutcome(
        candidate_map={image.name: candidate},
        records={image.name: record},
        resolution_by_name={image.name: (500, 750)},
        all_files=[image],
        primary_name=None,
        counts={
            "posters_found": 1,
            "downloaded": 1,
            "skipped": 0,
            "errors": 0,
            "metadata_gated": 0,
        },
    )
    seen_pools = []

    async def fake_fetch(**_kwargs):
        return fetch

    def fake_sync(**kwargs):
        seen_pools.append(kwargs["ocr_pool"])
        return SyncOutcome()

    monkeypatch.setattr(orchestrator.settings, "TMDB_READ_ACCESS_TOKEN", "token")
    monkeypatch.setattr(
        orchestrator.PosterTextFilter,
        "start_ocr_pool",
        staticmethod(fail_preload),
    )
    monkeypatch.setattr(orchestrator, "TMDBClient", FakeTMDBClient)
    monkeypatch.setattr(orchestrator, "fetch_and_download", fake_fetch)
    monkeypatch.setattr(orchestrator, "run_sync_stages", fake_sync)

    output = await orchestrator.run_poster_pipeline(
        subject=orchestrator.PosterSubjectInput(
            title="Example",
            movie_id=1,
            tmdb_id=2,
        ),
        source=orchestrator.PosterSourceInput(mode="tmdb"),
        out_dir=tmp_path,
        feature_extractor=object(),
    )

    assert output.status == "completed"
    assert start_calls == [True]
    assert seen_pools == [None]


@pytest.mark.asyncio
async def test_preload_does_not_fallback_after_uncertified_cleanup(monkeypatch):
    from marquee.pipeline import orchestrator

    monkeypatch.setattr(
        orchestrator.PosterTextFilter,
        "start_ocr_pool",
        staticmethod(
            lambda: (_ for _ in ()).throw(
                ocr_filter.OcrPoolTeardownError("worker survived cleanup")
            )
        ),
    )

    with pytest.raises(ocr_filter.OcrPoolTeardownError, match="worker survived cleanup"):
        async with orchestrator._preloaded_ocr_pool(enabled=True) as ready:
            await ready()


@pytest.mark.asyncio
async def test_missing_tmdb_token_fails_before_preload(monkeypatch, tmp_path):
    from marquee.pipeline import orchestrator

    starts = []
    monkeypatch.setattr(orchestrator.settings, "TMDB_READ_ACCESS_TOKEN", None)
    monkeypatch.setattr(
        orchestrator.PosterTextFilter,
        "start_ocr_pool",
        staticmethod(lambda: starts.append(True)),
    )

    with pytest.raises(RuntimeError, match="TMDB_READ_ACCESS_TOKEN"):
        await orchestrator.run_poster_pipeline(
            subject=orchestrator.PosterSubjectInput(title="Example", movie_id=1),
            source=orchestrator.PosterSourceInput(mode="tmdb"),
            out_dir=tmp_path,
            feature_extractor=object(),
        )

    assert starts == []


@pytest.mark.asyncio
async def test_fixture_run_does_not_preload_ocr(monkeypatch, tmp_path):
    from marquee.pipeline import orchestrator

    starts = []
    monkeypatch.setattr(
        orchestrator.PosterTextFilter,
        "start_ocr_pool",
        staticmethod(lambda: starts.append(True)),
    )

    output = await orchestrator.run_poster_pipeline(
        subject=orchestrator.PosterSubjectInput(title="Example", movie_id=1),
        source=orchestrator.PosterSourceInput(mode="fixture"),
        out_dir=tmp_path,
        feature_extractor=object(),
    )

    assert output.status == "flagged_manual"
    assert starts == []


@pytest.mark.asyncio
async def test_preload_cancellation_waits_for_pool_teardown(monkeypatch):
    import asyncio
    import threading

    from marquee.pipeline import orchestrator

    started = threading.Event()
    release = threading.Event()
    worker = _FakeWorker("poster-ocr-cancel", 987655, alive=True, exitcode=None)
    task_queue = _LifecycleQueue()
    result_queue = _LifecycleQueue()
    pool = ocr_filter.OcrPool([worker], task_queue, result_queue)

    def delayed_start():
        ocr_filter._register_worker(worker)
        started.set()
        if not release.wait(timeout=2):
            raise RuntimeError("test failed to release preload")
        return pool

    monkeypatch.setattr(
        orchestrator.PosterTextFilter,
        "start_ocr_pool",
        staticmethod(delayed_start),
    )

    async def fetch_window():
        async with orchestrator._preloaded_ocr_pool(enabled=True):
            while not started.is_set():
                await asyncio.sleep(0)
            await asyncio.Event().wait()

    task = asyncio.create_task(fetch_window())
    while not started.is_set():
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert 987655 not in ocr_filter._active_worker_pids
    assert worker.terminated
    assert task_queue.close_calls == 1
    assert result_queue.close_calls == 1


@pytest.mark.asyncio
async def test_preload_teardown_failure_surfaces_only_without_prior_failure(monkeypatch):
    from marquee.pipeline import orchestrator

    pool = ocr_filter.OcrPool([], _LifecycleQueue(), _LifecycleQueue())
    monkeypatch.setattr(
        orchestrator.PosterTextFilter,
        "start_ocr_pool",
        staticmethod(lambda: pool),
    )
    monkeypatch.setattr(
        orchestrator.PosterTextFilter,
        "stop_ocr_pool",
        staticmethod(lambda _pool: (_ for _ in ()).throw(RuntimeError("teardown failed"))),
    )

    with pytest.raises(RuntimeError, match="teardown failed"):
        async with orchestrator._preloaded_ocr_pool(enabled=True) as ready:
            assert await ready() is pool

    with pytest.raises(ValueError, match="pipeline failed"):
        async with orchestrator._preloaded_ocr_pool(enabled=True) as ready:
            assert await ready() is pool
            raise ValueError("pipeline failed")
