"""The text gate's two added rules, from recorded reader output of real posters.

Each fixture in ``ocr_reads/`` holds the reader's own answers for one poster of the
labelled set (text, confidence, polygon, and the shape of the image each call read),
the movie's gate context as the run job builds it, and what the gate decided from those
answers before the two rules existed ("today"). There is no image: the gate reads a
blank image of the poster's size and the recorded answers come back in call order.

The fixtures in ``ocr_reads/`` were recorded with the reader as it was before session E6
(PaddleOCR's document preprocessor on, so some boxes are in a turned frame); they are
inputs to the rules' logic and still test it. ``ocr_reads_fixed_reader/`` holds answers
recorded with the reader as ``_load_ocr`` builds it now, for the tests of that reader.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from marquee.config.pipeline import PipelineSettings
from marquee.pipeline import ocr_filter
from marquee.pipeline.types import OCRCandidateResult

FIXTURES = Path(__file__).parent / "ocr_reads"
FIXED_FIXTURES = Path(__file__).parent / "ocr_reads_fixed_reader"


def test_load_ocr_reads_the_poster_as_it_is(monkeypatch: pytest.MonkeyPatch):
    """No document preprocessor: nothing turns the poster or bends it before it is read, so
    boxes come back in the poster's own frame. Every other argument is as it was."""
    created: dict[str, object] = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=FakePaddleOCR))
    monkeypatch.setattr(ocr_filter, "_paddle_cuda_probe", lambda **_: (False, "not in tests"))
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DEVICE", "cpu")

    ocr_filter._load_ocr()

    assert created == {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": True,
        "lang": "en",
        "device": "cpu",
        "engine": "paddle_dynamic",
        "text_detection_model_name": "PP-OCRv5_mobile_det",
        "text_det_box_thresh": 0.3,
        "text_det_unclip_ratio": 2.0,
    }


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
    title_piece: bool,
    second_read: bool = False,
) -> tuple[OCRCandidateResult, _RecordedReader]:
    """The second-read rule is off unless a test turns it on: the tests written before it
    compare against what the gate decided and traced without it."""
    _defaults(monkeypatch)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_SMALL_COPY_RETRY", small_copy)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_TITLE_PIECE_RULE", title_piece)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_SECOND_READ_RULE", second_read)
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
MISREAD_PIECES = ["zathura-a-space-adventure-619", "the-place-beyond-the-pines-554"]
OTHER_TEXT = [
    "hokum-467",  # "A FILM BY DAMIAN MCCARTHY" under the title
    "how-to-train-your-dragon-2-469",  # the DREAMWORKS mark above the title
    "black-panther-414",  # the MARVEL STUDIOS mark above the title
    "conclave-430",  # actor names above the title
    "mission-impossible-235",  # TOM CRUISE above the title
    "zathura-a-space-adventure-620",  # COMING SOON
    "sonic-the-hedgehog-2-587",  # "IN THEATRES, ON DIGITAL" under the subtitle
    "no-time-to-die-14",  # the 007 mark beside the title (the owner's poster)
    "no-time-to-die-538",  # the 007 mark beside the title
]
MISSED_AT_FULL_SIZE = ["late-night-with-the-devil-4", "kill-bill-vol-1-87"]


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_both_switches_off_decide_exactly_as_before(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """With both switches off the gate gives today's decision and today's trace."""
    fixture = _fixture(name)
    today = fixture["today"]
    result, reader = _run(fixture, tmp_path, monkeypatch, small_copy=False, title_piece=False)

    assert len(reader.shapes) == today["calls_used"]
    assert result.accepted is today["accepted"]
    assert result.reason == today["reason"]
    assert result.detected_text == today["detected_text"]
    title_bbox = [list(point) for point in result.title_bbox] if result.title_bbox else None
    assert title_bbox == today["title_bbox"]
    assert [box.text for box in result.residual_boxes] == today["residual_texts"]
    assert json.loads(json.dumps(result.diagnostics)) == today["diagnostics"]


@pytest.mark.parametrize("name", MISREAD_PIECES)
def test_title_piece_rule_accepts_a_misread_piece_of_the_title(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(name)
    assert fixture["today"]["reason"] == "text_heavy"
    assert fixture["label"] == "title_only"

    result, _reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=True)

    assert result.accepted
    pieces = [box for box in _boxes(result) if box["is_title_piece"]]
    assert pieces
    assert all(box["is_title"] and box["title_match_source"] == "title_piece" for box in pieces)
    assert result.diagnostics["title_piece_rule"] == {"triggered": True, "pieces": len(pieces)}
    assert result.residual_boxes == [] or all(
        box.text not in {piece["text"] for piece in pieces} for box in result.residual_boxes
    )


@pytest.mark.parametrize("name", OTHER_TEXT)
def test_title_piece_rule_keeps_posters_with_other_text_out(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(name)
    assert fixture["label"] == "other_text"

    result, _reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=True)

    assert not result.accepted


def test_title_piece_rule_alone_changes_nothing_for_the_007(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The owner's No Time to Die: the "007" has no letters, so it is never a piece."""
    fixture = _fixture("no-time-to-die-14")
    result, _reader = _run(fixture, tmp_path, monkeypatch, small_copy=False, title_piece=True)

    assert _outcome(result) == "text_heavy"
    marks = [box for box in _boxes(result) if "007" in box["text"]]
    assert marks
    assert not any(box["is_title"] for box in marks)


@pytest.mark.parametrize("name", MISSED_AT_FULL_SIZE)
def test_small_copy_retry_reads_a_title_missed_at_full_size(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixture(name)
    assert fixture["today"]["reason"] in {"no_text", "no_title"}
    assert fixture["label"] == "title_only"

    result, reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=True)

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


def test_both_rules_together_read_a_spaced_title_and_its_pieces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Signs: at 500 px only "si" is read, no title. The 60% copy reads "siqns"; the two
    pieces read beside it at full size and 40% ("si", "ns") are pieces of the title."""
    fixture = _fixture("signs-583")
    assert fixture["today"]["reason"] == "text_heavy"
    assert fixture["label"] == "title_only"

    with_a_only, _reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=False)
    assert _outcome(with_a_only) == "text_heavy"

    result, _reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=True)

    assert result.accepted
    assert result.diagnostics["small_copy_retry"]["recovered_title"] is True
    assert {box["text"] for box in _boxes(result) if box["is_title_piece"]} == {"si", "ns"}


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

    result, reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=True)

    assert len(reader.shapes) == fixture["today"]["calls_used"]
    assert "small_60" not in result.diagnostics["passes_run"]
    assert result.diagnostics["small_copy_retry"]["triggered"] is False
    assert result.diagnostics["title_piece_rule"]["triggered"] is False
    assert result.accepted


def test_small_copy_retry_keeps_the_reason_when_the_copies_find_no_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A poster with no title on it keeps today's reason and today's reads."""
    fixture = _fixture("f1-1")
    assert fixture["label"] == "no_title"

    result, reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=True)

    assert len(reader.shapes) == fixture["today"]["calls_used"] + 2
    assert result.diagnostics["passes_run"][-2:] == ["small_60", "small_40"]
    assert result.diagnostics["small_copy_retry"]["recovered_title"] is False
    assert _outcome(result) == fixture["today"]["reason"]
    assert result.detected_text == fixture["today"]["detected_text"]
    assert [box["text"] for box in _boxes(result)] == [
        box["text"] for box in fixture["today"]["diagnostics"]["detected_boxes"]
    ]


# The fixed reader (no document preprocessor): every box is in the poster's own frame.


def _fixed_fixture(name: str) -> dict:
    return json.loads((FIXED_FIXTURES / f"{name}.json").read_text())


def _counted(result: OCRCandidateResult) -> list[str]:
    return [box["text"] for box in _boxes(result) if box["counts_toward_rejection"]]


def test_fixed_reader_finds_a_title_at_the_top_in_the_top_third_of_the_poster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Late Night with the Devil: the title is set across the top. The old reader turned the
    poster a quarter round and found only "night", outside the poster (x 660 to 750 of 500),
    and only on a smaller copy; the fixed reader reads the title where it is, in one pass."""
    fixture = _fixed_fixture("late-night-with-the-devil-4")
    width, height = fixture["image_size"]

    result, reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=False)

    assert result.accepted
    assert result.diagnostics["passes_run"] == ["full", "top", "bottom"]
    assert len(reader.shapes) == 3
    titles = {box["text"] for box in _boxes(result) if box["is_title"]}
    assert {"LATE NIGHT", "with", "the", "DEVIL"} <= titles
    assert result.title_bbox is not None
    xs = [x for x, _y in result.title_bbox]
    ys = [y for _x, y in result.title_bbox]
    assert max(ys) < height / 3
    assert min(xs) >= 0 and max(xs) <= width and min(ys) >= 0


@pytest.mark.parametrize("title_piece", [False, True])
def test_fixed_reader_still_rejects_text_that_is_part_of_the_artwork(
    title_piece: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Monsters (the owner's own poster): the fixed reader reads the INFECTED ZONE sign in the
    picture, which the old reader never did. It is not the title, so the poster is rejected,
    with the piece rule or without it."""
    fixture = _fixed_fixture("monsters-seed")
    assert fixture["old_reader"]["outcome"] == "accepted"

    result, _reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=title_piece)

    assert _outcome(result) == "text_heavy"
    assert _counted(result) == ["INFECTEBZONE"]
    assert any(box["is_title"] and box["text"] == "MONSTERS" for box in _boxes(result))


def test_fixed_reader_misread_title_piece_rejects_unless_the_piece_rule_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Berserk II (the owner's own poster): the full pass reads BERSERK, and the bottom strip,
    read at twice its size, reads the same title again as "DEKSEKK". With the piece rule off
    (the default) that misread piece is other text; with it on it is a piece of the title."""
    fixture = _fixed_fixture("berserk-the-golden-age-arc-ii-the-battle-for-doldrey-seed")
    assert fixture["old_reader"]["outcome"] == "accepted"

    off, _reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=False)
    assert _outcome(off) == "text_heavy"
    assert _counted(off) == ["DEKSEKK"]

    on, _reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=True)
    assert on.accepted
    assert {box["text"] for box in _boxes(on) if box["is_title_piece"]} == {"DEKSEKK"}


# Title matching under any hash seed. Python orders a set of strings by their hashes, which
# change with PYTHONHASHSEED from one process to the next, so each run is a fresh process.

_GATE_IN_A_FRESH_PROCESS = """
import json, sys
import numpy as np
from PIL import Image
from marquee.config.pipeline import PipelineSettings
from marquee.pipeline import ocr_filter

fixture = json.loads(open(sys.argv[1]).read())
for name, field in PipelineSettings.model_fields.items():
    if name.startswith("OCR_"):
        setattr(ocr_filter.pipeline_settings, name, field.default)
ocr_filter.pipeline_settings.OCR_SMALL_COPY_RETRY = True
ocr_filter.pipeline_settings.OCR_TITLE_PIECE_RULE = False

class Reader:
    def __init__(self):
        self.calls = iter(fixture["calls"])
    def predict(self, image):
        call = next(self.calls)
        if not call["texts"]:
            return []
        polys = [np.asarray(p, dtype=np.float32) for p in call["polys"]]
        return [{"rec_texts": call["texts"], "rec_scores": call["scores"], "rec_polys": polys}]

ocr_filter._worker_ocr = Reader()
Image.new("RGB", tuple(fixture["image_size"]), "black").save(sys.argv[2])
extras = dict(fixture["extras"])
extras["studio_tokens"] = set(extras.get("studio_tokens", ()))
result = ocr_filter._process_image(
    sys.argv[2], set(fixture["title_tokens"]), set(fixture["director_tokens"]),
    title_text=fixture["title_text"], extras=extras)
print(json.dumps({"accepted": result.accepted, "reason": result.reason,
                  "trace": result.diagnostics}, sort_keys=True))
"""


def test_title_matching_gives_the_same_answer_under_any_hash_seed(tmp_path: Path):
    """No Time to Die with the 007 mark (labelled poster #341). A smaller copy reads MO, TIAE,
    and 007, and the letter band made of them covers two title words fully, "time" and "to".
    Which of the two the band was measured against used to follow the order Python iterated a
    set of title words: measured against "time" most of its letters are the title's and the
    band is title; measured against "to" it is not, and MO counts as other text. The same
    reader answers were accepted under some hash seeds and rejected under others. The words
    are now taken in sorted order, so "time" comes first under every seed."""
    fixture_path = FIXED_FIXTURES / "no-time-to-die-341.json"
    answers = []
    for seed in ("0", "1", "2", "4", "7"):
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                _GATE_IN_A_FRESH_PROCESS,
                str(fixture_path),
                str(tmp_path / f"poster-{seed}.png"),
            ],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        answers.append(completed.stdout.strip().splitlines()[-1])

    assert len(set(answers)) == 1, "the decision or the trace changed with the hash seed"
    answer = json.loads(answers[0])
    assert answer["accepted"]
    titles = {box["text"] for box in answer["trace"]["detected_boxes"] if box["is_title"]}
    assert titles == {"MO", "TIAE", "007", "00"}


# Rule C, second reads: a leftover box that is a second read of title glyphs, read by another
# pass, is set aside and stops counting as other text.


def _slanted(cx: float, cy: float, length: float, height: float, degrees: float):
    """A reader's polygon for a line of text centred on (cx, cy), turned by *degrees*
    (negative rises to the right, as on a slanted title), corners in reading order."""
    a = math.radians(degrees)
    ux, uy = math.cos(a), math.sin(a)
    vx, vy = -uy, ux
    corners = [
        (-length / 2, -height / 2),
        (length / 2, -height / 2),
        (length / 2, height / 2),
        (-length / 2, height / 2),
    ]
    return tuple((cx + s * ux + t * vx, cy + s * uy + t * vy) for s, t in corners)


def _rectangle(polygon) -> tuple:
    xs = [x for x, _y in polygon]
    ys = [y for _x, y in polygon]
    return ((min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys)))


SLANTED_TITLE = _slanted(250, 330, 400, 70, -20)
# A credit line set level above the title's left end: inside the title's bounding rectangle,
# outside its polygon.
CREDIT_ABOVE = ((70, 262), (200, 262), (200, 282), (70, 282))
# The title's right half read again, turned with it.
MISREAD_ON_TITLE = _slanted(340, 297, 180, 60, -20)
# A box centred on the title's right end, turned with it: half on the title, half off it.
_END = math.radians(-20)
HALF_OFF_THE_END = _slanted(250 + 200 * math.cos(_END), 330 + 200 * math.sin(_END), 100, 70, -20)


def test_second_read_overlap_is_measured_on_polygons_not_rectangles():
    """Rectangles lie: the bounding rectangle of a slanted title covers the credit above it."""
    assert ocr_filter._polygon_share_inside(CREDIT_ABOVE, _rectangle(SLANTED_TITLE)) == 1.0
    assert ocr_filter._polygon_share_inside(CREDIT_ABOVE, SLANTED_TITLE) == 0.0
    assert ocr_filter._polygon_share_inside(MISREAD_ON_TITLE, SLANTED_TITLE) == pytest.approx(1.0)
    # Half of a box hanging over the title's end is half inside, whichever way the corners run.
    assert ocr_filter._polygon_share_inside(HALF_OFF_THE_END, SLANTED_TITLE) == pytest.approx(0.5)
    assert ocr_filter._polygon_share_inside(
        HALF_OFF_THE_END[::-1], SLANTED_TITLE[::-1]
    ) == pytest.approx(0.5)
    assert ocr_filter._glyph_height(SLANTED_TITLE) == pytest.approx(70)


def _second_read(
    box_bbox, *, box_pass="bottom", anchor_pass="full", category="other", valid=True
) -> bool:
    anchor = ocr_filter._DetectedBox("KILL BILL", 0.95, SLANTED_TITLE)
    box = ocr_filter._DetectedBox("NILL", 0.8, box_bbox, geometry_valid=valid)
    return ocr_filter._is_second_read(
        box, [anchor], pass_by_id={id(anchor): anchor_pass, id(box): box_pass}, category=category
    )


def test_a_second_read_is_another_pass_on_the_title_with_glyphs_of_its_size():
    assert _second_read(MISREAD_ON_TITLE)
    assert _second_read(MISREAD_ON_TITLE, box_pass="small_60")
    # The same pass reading two boxes there is two pieces of text, not one read twice.
    assert not _second_read(MISREAD_ON_TITLE, box_pass="full")
    # The credit above the slanted title, read by the top strip, is not on the title, and a box
    # half off the title's end is not on it enough.
    assert not _second_read(CREDIT_ABOVE, box_pass="top")
    assert not _second_read(HALF_OFF_THE_END)
    # Small type inside the title box (a mark in its slack): a tenth of the title's height.
    assert not _second_read(_slanted(340, 297, 120, 7, -20))
    # The gate's classifier keeps studio marks and director's credits, wherever they lie.
    assert not _second_read(MISREAD_ON_TITLE, category="studio")
    assert not _second_read(MISREAD_ON_TITLE, category="director")
    assert _second_read(MISREAD_ON_TITLE, category="billing")
    # A box whose polygon is in a turned frame takes no part.
    assert not _second_read(MISREAD_ON_TITLE, valid=False)


# Rule C on recorded reader output of real posters (the fixed reader, the smaller-copy retry
# on, the title-piece rule off, as on this machine). The eval3 fixtures are candidates the third
# blind evaluation rejected as text_heavy with a title found, labelled by looking in session E7.

SECOND_READS = {
    # NATT: the bottom strip, read at twice its size, on the RIVER of WIND RIVER.
    "wind-river-eval3-1": {"NATT"},
    # "si" and "ns": the bottom strip on "signs", read whole by the full pass.
    "signs-eval3-1": {"si", "ns"},
    # DEKSEKK: the bottom strip on BERSERK (the owner's poster).
    "berserk-the-golden-age-arc-ii-the-battle-for-doldrey-seed": {"DEKSEKK"},
    # EINTHSNOS: the full pass on the SUNSHINE the 40% copy read.
    "sunshine-eval3-1": {"EINTHSNOS"},
}


@pytest.mark.parametrize("name", sorted(SECOND_READS))
def test_second_read_rule_sets_aside_a_second_read_of_the_title(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixed_fixture(name)
    expected = SECOND_READS[name]
    off, _reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=False)
    assert _outcome(off) == "text_heavy"
    assert set(_counted(off)) == expected

    on, _reader = _run(
        fixture, tmp_path, monkeypatch, small_copy=True, title_piece=False, second_read=True
    )

    assert on.accepted
    set_aside = [box for box in _boxes(on) if box["is_second_read"]]
    assert {box["text"] for box in set_aside} == expected
    assert on.diagnostics["second_read_rule"] == {
        "triggered": True,
        "second_reads": len(set_aside),
    }
    assert not any(box["counts_toward_rejection"] or box["is_title"] for box in set_aside)
    # Set aside, not made title: the title evidence and the title box are what they were, and
    # the box stays a leftover box (the ranking's text_residual feature still counts it).
    assert [box["is_title"] for box in _boxes(on)] == [box["is_title"] for box in _boxes(off)]
    assert on.title_bbox == off.title_bbox
    assert [box.text for box in on.residual_boxes] == [box.text for box in off.residual_boxes]


def _counted_on(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> dict:
    """Runs a poster with other text with the rule on; it stays rejected and *text* counts."""
    fixture = _fixed_fixture(name)
    assert fixture["label"] == "other_text"
    off, _reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=False)
    on, _reader = _run(
        fixture, tmp_path, monkeypatch, small_copy=True, title_piece=False, second_read=True
    )
    assert _outcome(off) == _outcome(on) == "text_heavy"
    assert on.diagnostics["second_read_rule"]["triggered"] is True
    boxes = [box for box in _boxes(on) if box["text"] == text]
    assert boxes
    assert all(box["counts_toward_rejection"] and not box["is_second_read"] for box in boxes)
    return {"on": on, "boxes": boxes}


def _title_box(result: OCRCandidateResult, text: str) -> dict:
    return next(box for box in _boxes(result) if box["is_title"] and box["text"] == text)


def test_second_read_rule_keeps_a_studio_mark_inside_the_title_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The Incredibles: the top strip reads the Disney Pixar mark, which lies almost whole
    inside the full pass's THE INCREDIBLES box and is set large enough for the height bound.
    The gate's classifier calls it a studio mark, and a studio mark is never set aside."""
    run = _counted_on("the-incredibles-eval3-1", tmp_path, monkeypatch, "DisNEp·PIXAR")
    (mark,) = run["boxes"]
    title = _title_box(run["on"], "THE INCREDIBLES")
    assert (mark["pass"], mark["category"], title["pass"]) == ("top", "studio", "full")
    assert ocr_filter._polygon_share_inside(_polygon(mark), _polygon(title)) > 0.9


def _polygon(box: dict) -> tuple:
    return tuple(tuple(point) for point in box["bbox"])


def test_second_read_rule_keeps_a_credit_line_over_a_slanted_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Death Proof: QUENTIN TARANTINO'S over the slanted DEATH. The title's bounding rectangle
    takes in most of the credit; its polygon takes in a third."""
    run = _counted_on("death-proof-eval3-1", tmp_path, monkeypatch, "QUENTIN TARA")
    (credit,) = run["boxes"]
    title = _title_box(run["on"], "DEATH")
    inside = ocr_filter._polygon_share_inside(_polygon(credit), _polygon(title))
    rectangle = ocr_filter._polygon_share_inside(_polygon(credit), _rectangle(_polygon(title)))
    assert rectangle > 0.75 > 0.4 > inside


def test_second_read_rule_keeps_actor_names_under_the_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The Girl with the Dragon Tattoo: DANIEL CRAIG ROONEY MARA, read by the same pass as the
    title and partly inside its box: two pieces of text, not one read twice."""
    run = _counted_on(
        "the-girl-with-the-dragon-tattoo-eval3-1", tmp_path, monkeypatch, "DANIEL CRAIG ROONEY MARA"
    )
    (names,) = run["boxes"]
    assert names["pass"] == _title_box(run["on"], "DRAGON TATTOO")["pass"] == "full"


def test_second_read_rule_keeps_a_tagline_inside_a_large_title_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Abigail: the title is read only on the smaller copies, and its box there takes in the
    tagline DADDY'S LITTLE GIRL IS GETTING HUNGRY, read by the full pass and the top strip.
    The tagline is set at a twentieth of the title's height: the height bound keeps it."""
    run = _counted_on(
        "abigail-eval3-1", tmp_path, monkeypatch, "DADDY'S LITTLE GIRL IS GETTING HUNGRY"
    )
    (tagline,) = run["boxes"]
    title = _title_box(run["on"], "ABIGAL")
    assert (tagline["pass"], title["pass"]) == ("full", "small_60")
    assert ocr_filter._polygon_share_inside(_polygon(tagline), _polygon(title)) > 0.75
    assert ocr_filter._glyph_height(_polygon(tagline)) < 0.1 * ocr_filter._glyph_height(
        _polygon(title)
    )


def test_second_read_rule_keeps_artwork_text_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Monsters (the owner's poster): the INFECTED ZONE sign in the picture is not on the title."""
    fixture = _fixed_fixture("monsters-seed")
    on, _reader = _run(
        fixture, tmp_path, monkeypatch, small_copy=True, title_piece=False, second_read=True
    )
    assert _outcome(on) == "text_heavy"
    assert _counted(on) == ["INFECTEBZONE"]
    assert on.diagnostics["second_read_rule"] == {"triggered": True, "second_reads": 0}


ACCEPTED_WITHOUT_THE_RULE = ["late-night-with-the-devil-4", "no-time-to-die-341"]


@pytest.mark.parametrize("name", ACCEPTED_WITHOUT_THE_RULE)
def test_second_read_rule_never_looks_at_a_poster_accepted_without_it(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = _fixed_fixture(name)
    off, _reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=False)
    on, reader = _run(
        fixture, tmp_path, monkeypatch, small_copy=True, title_piece=False, second_read=True
    )

    assert off.accepted and on.accepted
    assert on.diagnostics["second_read_rule"] == {"triggered": False, "second_reads": 0}
    assert not any(box["is_second_read"] for box in _boxes(on))
    trace = json.loads(json.dumps(on.diagnostics))
    del trace["second_read_rule"]
    for box in trace["detected_boxes"]:
        del box["is_second_read"]
    assert trace == json.loads(json.dumps(off.diagnostics))
    assert len(reader.shapes) == len(fixture["calls"])


@pytest.mark.parametrize("name", sorted(SECOND_READS))
def test_second_read_switch_off_adds_nothing_to_the_trace(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """With the switch off the poster is rejected as before and the trace has no block or mark
    of the rule's (the earlier tests compare such traces with the stored ones exactly)."""
    fixture = _fixed_fixture(name)
    off, _reader = _run(fixture, tmp_path, monkeypatch, small_copy=True, title_piece=False)
    assert _outcome(off) == "text_heavy"
    assert "second_read_rule" not in off.diagnostics
    assert not any("is_second_read" in box for box in _boxes(off))
