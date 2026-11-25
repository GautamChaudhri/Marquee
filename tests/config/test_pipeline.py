from __future__ import annotations

import pytest

from marquee.config.pipeline import PipelineSettings


def test_the_text_gate_rules_are_on_by_default_and_named_in_the_run_archive() -> None:
    settings = PipelineSettings(_env_file=None)
    ocr = settings.snapshot()["ocr"]
    assert ocr["small_copy_retry"] is True
    assert ocr["title_piece_rule"] is True


@pytest.mark.parametrize("name", ["OCR_SMALL_COPY_RETRY", "OCR_TITLE_PIECE_RULE"])
def test_each_text_gate_rule_can_be_switched_off(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name, "false")
    settings = PipelineSettings(_env_file=None)
    assert getattr(settings, name) is False
    assert settings.snapshot()["ocr"][name.removeprefix("OCR_").lower()] is False
