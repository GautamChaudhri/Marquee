"""Run archives must never contain NaN/Infinity — Starlette renders responses
with ``allow_nan=False``, so a single archived NaN would 500 every endpoint
that replays the archive (/api/pipeline/runs/{id}, /rescore, feedback)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from marquee.pipeline.runner import _attach_ocr_diagnostics, write_run_json
from marquee.pipeline.types import CandidateScore, OCRCandidateResult


def _strict_loads(text: str) -> dict:
    def _reject(constant: str) -> None:
        raise AssertionError(f"archive contains non-finite literal {constant!r}")

    return json.loads(text, parse_constant=_reject)


def test_write_run_json_strips_nonfinite(tmp_path):
    payload = {
        "plain_nan": float("nan"),
        "plain_inf": float("inf"),
        "np_nan": np.float64("nan"),
        "np_value": np.float32(0.5),
        "array": np.array([1.0, np.nan, -np.inf]),
        "nested": {"deep": [{"x": float("nan")}, 2.0]},
        "fine": 1.25,
        "text": "NaN",  # strings must survive untouched
    }
    path = tmp_path / "run.json"
    write_run_json(path, payload)

    data = _strict_loads(path.read_text())
    assert data["plain_nan"] is None
    assert data["plain_inf"] is None
    assert data["np_nan"] is None
    assert data["np_value"] == pytest.approx(0.5)
    assert data["array"] == [1.0, None, None]
    assert data["nested"]["deep"][0]["x"] is None
    assert data["nested"]["deep"][1] == 2.0
    assert data["fine"] == 1.25
    assert data["text"] == "NaN"


def _ocr_result(*, accepted: bool, diagnostics: dict) -> OCRCandidateResult:
    return OCRCandidateResult(
        image_path=Path("poster.jpg"),
        accepted=accepted,
        detected_text="tomahawk",
        reason=None if accepted else "text_heavy",
        title_bbox=None,
        diagnostics=diagnostics,
    )


def _region(text: str, **extra) -> dict:
    return {
        "text": text,
        "confidence": 0.9,
        "category": "title",
        "is_title": True,
        "is_title_fragment": True,
        "is_significant": False,
        "counts_toward_rejection": False,
        "pass": "bottom",
        "title_match_source": "box",
        **extra,
    }


def test_archive_regions_without_the_added_text_rules_are_unchanged():
    """With both switches off the trace has no block of theirs: an accepted candidate keeps
    no regions and a rejected one keeps the same keys as before the two rules."""
    trace = {"detected_boxes": [_region("TOMA")]}
    accepted = CandidateScore(image_path=Path("a.jpg"), orig_filename="a.jpg")
    _attach_ocr_diagnostics(accepted, _ocr_result(accepted=True, diagnostics=trace))
    assert accepted.ocr_display_regions is None

    rejected = CandidateScore(image_path=Path("r.jpg"), orig_filename="r.jpg")
    _attach_ocr_diagnostics(rejected, _ocr_result(accepted=False, diagnostics=trace))
    assert rejected.ocr_display_regions is not None
    assert set(rejected.ocr_display_regions[0]) == {
        "text",
        "confidence",
        "category",
        "semantic_source",
        "semantic_span_text",
        "is_title",
        "is_title_fragment",
        "is_significant",
        "counts_toward_rejection",
    }


@pytest.mark.parametrize("block", ["small_copy_retry", "title_piece_rule", "second_read_rule"])
def test_archive_keeps_every_region_with_its_pass_when_an_added_rule_is_on(block: str):
    """A later reader can tell why a poster passed: every candidate keeps its regions, each
    with the pass that read it and whether it counted as a piece of the title."""
    trace = {
        block: {"triggered": True},
        "detected_boxes": [
            _region("TOMA"),
            _region("RAHAWN", title_match_source="title_piece", is_title_piece=True),
            _region("BONE", **{"pass": "small_60"}),
        ],
    }
    accepted = CandidateScore(image_path=Path("a.jpg"), orig_filename="a.jpg")
    _attach_ocr_diagnostics(accepted, _ocr_result(accepted=True, diagnostics=trace))
    regions = {region["text"]: region for region in accepted.ocr_display_regions or []}
    assert regions["TOMA"]["pass"] == "bottom"
    assert regions["TOMA"]["is_title_piece"] is False
    assert regions["RAHAWN"]["is_title_piece"] is True
    assert regions["BONE"]["pass"] == "small_60"


def test_archive_marks_what_the_second_read_rule_set_aside():
    """With the rule on, every region says whether it was set aside as a second read of the
    title; a region the rule set aside no longer counts toward rejection."""
    trace = {
        "second_read_rule": {"triggered": True, "second_reads": 1},
        "detected_boxes": [
            _region("WIND RIVER", **{"pass": "full"}, is_second_read=False),
            _region(
                "NATT",
                category="other",
                is_title=False,
                is_title_fragment=False,
                is_significant=True,
                is_second_read=True,
            ),
        ],
    }
    accepted = CandidateScore(image_path=Path("a.jpg"), orig_filename="a.jpg")
    _attach_ocr_diagnostics(accepted, _ocr_result(accepted=True, diagnostics=trace))
    regions = {region["text"]: region for region in accepted.ocr_display_regions or []}
    assert regions["WIND RIVER"]["is_second_read"] is False
    assert regions["NATT"]["is_second_read"] is True
    assert regions["NATT"]["counts_toward_rejection"] is False
    assert regions["NATT"]["pass"] == "bottom"


def test_archive_regions_carry_no_second_read_mark_without_the_rule():
    """With the second-read rule off its block is not in the trace, and no region gets the
    mark: the archive is what it was with the other two rules alone."""
    trace = {"small_copy_retry": {"triggered": False}, "detected_boxes": [_region("TOMA")]}
    rejected = CandidateScore(image_path=Path("r.jpg"), orig_filename="r.jpg")
    _attach_ocr_diagnostics(rejected, _ocr_result(accepted=False, diagnostics=trace))
    assert rejected.ocr_display_regions is not None
    assert "is_second_read" not in rejected.ocr_display_regions[0]
