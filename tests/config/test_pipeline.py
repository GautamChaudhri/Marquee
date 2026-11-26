from __future__ import annotations

import pytest

from marquee.config import pipeline as pipeline_config
from marquee.config.pipeline import PipelineSettings

# The ranking weights the planner fitted on 2026-11-25 from blind evaluation 4's archives
# (held out, the pick is the owner's design about 73 times of 147 against 53 with the old
# ones). Resolution and language match stay unweighted.
FITTED_WEIGHTS = {
    "knn_sim": 0.20,
    "aesthetic": 0.10,
    "title_colorfulness": 0.04,
    "face_area": 0.19,
    "text_residual": 0.14,
    "provenance": 0.04,
    "sharpness": 0.06,
    "resolution": 0.0,
    "lang_match": 0.0,
    "dino_knn": 0.03,
    "taste_typicality": 0.18,
    "quality_artifacts": 0.10,
    "official_family": 0.18,
}


def test_the_default_ranking_weights_are_the_fitted_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PipelineSettings.model_fields:
        if name.startswith("WEIGHT_"):
            monkeypatch.delenv(name, raising=False)
    weights = PipelineSettings(_env_file=None).scorer_weights
    assert weights == FITTED_WEIGHTS
    assert sum(weights.values()) == pytest.approx(1.26)


def test_a_ranking_weight_can_still_be_set_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEIGHT_KNN_SIM", "0.3")
    assert PipelineSettings(_env_file=None).scorer_weights["knn_sim"] == 0.3


def test_retry_on_and_title_piece_rule_off_by_default_both_named_in_the_archive() -> None:
    settings = PipelineSettings(_env_file=None)
    ocr = settings.snapshot()["ocr"]
    assert ocr["small_copy_retry"] is True
    assert ocr["title_piece_rule"] is False


def test_the_second_read_rule_is_on_by_default_and_named_in_the_archive() -> None:
    settings = PipelineSettings(_env_file=None)
    assert settings.OCR_SECOND_READ_RULE is True
    assert settings.snapshot()["ocr"]["second_read_rule"] is True


@pytest.mark.parametrize(
    "name", ["OCR_SMALL_COPY_RETRY", "OCR_TITLE_PIECE_RULE", "OCR_SECOND_READ_RULE"]
)
def test_each_text_gate_rule_can_be_switched_off(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name, "false")
    settings = PipelineSettings(_env_file=None)
    assert getattr(settings, name) is False
    assert settings.snapshot()["ocr"][name.removeprefix("OCR_").lower()] is False


def test_the_title_piece_rule_can_be_switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_TITLE_PIECE_RULE", "true")
    settings = PipelineSettings(_env_file=None)
    assert settings.OCR_TITLE_PIECE_RULE is True
    assert settings.snapshot()["ocr"]["title_piece_rule"] is True


def test_the_run_archive_says_the_reader_has_no_document_preprocessor() -> None:
    assert pipeline_config.OCR_DOCUMENT_PREPROCESSOR is False
    ocr = PipelineSettings(_env_file=None).snapshot()["ocr"]
    assert ocr["document_preprocessor"] is False


def test_the_document_preprocessor_is_not_a_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is a defect switched off, not a preference: nothing reads it from the environment."""
    monkeypatch.setenv("OCR_DOCUMENT_PREPROCESSOR", "true")
    settings = PipelineSettings(_env_file=None)
    assert not hasattr(settings, "OCR_DOCUMENT_PREPROCESSOR")
    assert settings.snapshot()["ocr"]["document_preprocessor"] is False
