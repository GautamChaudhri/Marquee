from __future__ import annotations

import pytest

from marquee.config import pipeline as pipeline_config
from marquee.config.pipeline import PipelineSettings


def test_retry_on_and_title_piece_rule_off_by_default_both_named_in_the_archive() -> None:
    settings = PipelineSettings(_env_file=None)
    ocr = settings.snapshot()["ocr"]
    assert ocr["small_copy_retry"] is True
    assert ocr["title_piece_rule"] is False


@pytest.mark.parametrize("name", ["OCR_SMALL_COPY_RETRY", "OCR_TITLE_PIECE_RULE"])
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
