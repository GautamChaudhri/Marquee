"""The text gate's two added rules, from recorded reader output of real posters.

Each fixture in ``ocr_reads/`` holds the reader's own answers for one poster of the
labelled set (text, confidence, polygon, and the shape of the image each call read),
the movie's gate context as the run job builds it, and what the gate decided from those
answers before the two rules existed ("today"). There is no image: the gate reads a
blank image of the poster's size and the recorded answers come back in call order.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from marquee.config.pipeline import PipelineSettings
from marquee.pipeline import ocr_filter
from marquee.pipeline.types import OCRCandidateResult

FIXTURES = Path(__file__).parent / "ocr_reads"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class _RecordedReader:
    """Gives back one poster's recorded answers in call order."""

    def __init__(self, calls: list[dict]):
        self.recorded = calls
        self.shapes: list[list[int]] = []

    def predict(self, image: np.ndarray):
        index = len(self.shapes)
        assert index < len(self.recorded), f"the gate made a call the recording has not: {index}"
        call = self.recorded[index]
        shape = [int(image.shape[0]), int(image.shape[1])]
        assert shape == call["shape"], f"call {index + 1} read {shape}, recorded {call['shape']}"
        self.shapes.append(shape)
        if not call["texts"]:
            return []
        return [
            {
                "rec_texts": call["texts"],
                "rec_scores": call["scores"],
                "rec_polys": [np.asarray(p, dtype=np.float32) for p in call["polys"]],
            }
        ]


def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every OCR knob at its default, as when the readings were recorded."""
    for name, field in PipelineSettings.model_fields.items():
        if name.startswith("OCR_"):
            monkeypatch.setattr(ocr_filter.pipeline_settings, name, field.default)


def _run(
    fixture: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    small_copy: bool,
) -> tuple[OCRCandidateResult, _RecordedReader]:
    _defaults(monkeypatch)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_SMALL_COPY_RETRY", small_copy)
    reader = _RecordedReader(fixture["calls"])
    monkeypatch.setattr(ocr_filter, "_worker_ocr", reader)
    path = tmp_path / "poster.png"
    Image.new("RGB", tuple(fixture["image_size"]), "black").save(path)
    extras = dict(fixture["extras"])
    if "studio_tokens" in extras:
        extras["studio_tokens"] = set(extras["studio_tokens"])
    result = ocr_filter._process_image(
        str(path),
        set(fixture["title_tokens"]),
        set(fixture["director_tokens"]),
        title_text=fixture["title_text"],
        extras=extras,
    )
    return result, reader


def _outcome(result: OCRCandidateResult) -> str:
    return "accepted" if result.accepted else str(result.reason)


def _boxes(result: OCRCandidateResult) -> list[dict]:
    assert result.diagnostics is not None
    return result.diagnostics["detected_boxes"]


ALL_FIXTURES = sorted(path.stem for path in FIXTURES.glob("*.json"))
MISSED_AT_FULL_SIZE = ["late-night-with-the-devil-4", "kill-bill-vol-1-87"]


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_switched_off_the_gate_decides_exactly_as_before(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """With the smaller-copy retry off the gate gives today's decision and today's trace."""
    fixture = _fixture(name)
    today = fixture["today"]
    result, reader = _run(fixture, tmp_path, monkeypatch, small_copy=False)

    assert len(reader.shapes) == today["calls_used"]
    assert result.accepted is today["accepted"]
    assert result.reason == today["reason"]
    assert result.detected_text == today["detected_text"]
    title_bbox = [list(point) for point in result.title_bbox] if result.title_bbox else None
    assert title_bbox == today["title_bbox"]
    assert [box.text for box in result.residual_boxes] == today["residual_texts"]
    assert json.loads(json.dumps(result.diagnostics)) == today["diagnostics"]


@pytest.mark.parametrize("name", MISSED_AT_FULL_SIZE)
def test_small_copy_retry_reads_a_title_missed_at_full_size(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(name)
    assert fixture["today"]["reason"] in {"no_text", "no_title"}
    assert fixture["label"] == "title_only"

    result, reader = _run(fixture, tmp_path, monkeypatch, small_copy=True)

    assert result.accepted
    assert len(reader.shapes) == fixture["today"]["calls_used"] + 2
    width, height = fixture["image_size"]
    assert reader.shapes[-2:] == [
        [round(height * 0.6), round(width * 0.6)],
        [round(height * 0.4), round(width * 0.4)],
    ]
    assert result.diagnostics["passes_run"][-2:] == ["small_60", "small_40"]
    assert result.diagnostics["small_copy_retry"]["triggered"] is True
    assert result.diagnostics["small_copy_retry"]["recovered_title"] is True
    title_boxes = [box for box in _boxes(result) if box["is_title"]]
    assert any(box["pass"] in {"small_60", "small_40"} for box in title_boxes)
    # Every box is in the poster's own coordinates, not the copy's.
    for box in _boxes(result):
        if box["geometry_valid"]:
            xs = [x for x, _y in box["bbox"]]
            ys = [y for _x, y in box["bbox"]]
            assert min(xs) >= -3 and max(xs) <= width + 3
            assert min(ys) >= -3 and max(ys) <= height + 3


def test_small_copy_boxes_come_back_in_the_posters_coordinates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A known box on the 60% copy maps back to the poster: (60, 300)-(240, 360) on a
    300x450 copy is (100, 500)-(400, 600) on the 500x750 poster."""
    _defaults(monkeypatch)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DETAIL_PASSES", False)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_ENHANCE_RETRY", False)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_TITLE_RECOVERY_ENABLED", False)

    class SmallCopyReader:
        def __init__(self):
            self.shapes: list[tuple[int, int]] = []

        def predict(self, image: np.ndarray):
            self.shapes.append((image.shape[0], image.shape[1]))
            if image.shape[:2] != (450, 300):
                return []
            poly = [[60, 300], [240, 300], [240, 360], [60, 360]]
            return [{"rec_texts": ["DUNE"], "rec_scores": [0.95], "rec_polys": [poly]}]

    reader = SmallCopyReader()
    monkeypatch.setattr(ocr_filter, "_worker_ocr", reader)
    path = tmp_path / "poster.png"
    Image.new("RGB", (500, 750), "black").save(path)

    result = ocr_filter._process_image(str(path), {"dune"}, set(), title_text="dune")

    assert reader.shapes == [(750, 500), (450, 300), (300, 200)]
    assert result.accepted
    assert result.title_bbox is not None
    corners = [coordinate for point in result.title_bbox for coordinate in point]
    assert corners == pytest.approx([100, 500, 400, 500, 400, 600, 100, 600], abs=1e-3)
    assert [box["pass"] for box in _boxes(result)] == ["small_60"]


def test_small_copy_retry_never_runs_when_a_title_was_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture("black-panther-282")
    assert fixture["today"]["accepted"]

    result, reader = _run(fixture, tmp_path, monkeypatch, small_copy=True)

    assert len(reader.shapes) == fixture["today"]["calls_used"]
    assert "small_60" not in result.diagnostics["passes_run"]
    assert result.diagnostics["small_copy_retry"]["triggered"] is False
    assert result.accepted


def test_small_copy_retry_keeps_the_reason_when_the_copies_find_no_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A poster with no title on it keeps today's reason and today's reads."""
    fixture = _fixture("f1-1")
    assert fixture["label"] == "no_title"

    result, reader = _run(fixture, tmp_path, monkeypatch, small_copy=True)

    assert len(reader.shapes) == fixture["today"]["calls_used"] + 2
    assert result.diagnostics["passes_run"][-2:] == ["small_60", "small_40"]
    assert result.diagnostics["small_copy_retry"]["recovered_title"] is False
    assert _outcome(result) == fixture["today"]["reason"]
    assert result.detected_text == fixture["today"]["detected_text"]
    assert [box["text"] for box in _boxes(result)] == [
        box["text"] for box in fixture["today"]["diagnostics"]["detected_boxes"]
    ]
