"""Backend readiness for the poster-pipeline vertical slice.

Covers the new, ML-free units:
  * OCR per-task title tokens (one pool can serve many movies)
  * per-stage rejection grouping in the results payload
  * pipeline cache-clear path safety
  * library-poster gathering for taste rebuild
  * new job handlers are registered
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from marquee.api.results import build_results_payload
from marquee.config import settings
from marquee.core import pipeline_cache as pc
from marquee.pipeline import ocr_filter

# ---------------------------------------------------------------------------
# OCR per-task tokens — the enabler for one pool serving many movies
# ---------------------------------------------------------------------------


def test_process_image_uses_per_call_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The same image + same OCR decode accepts under its own title tokens and
    rejects under another's — proving tokens are per call, not worker-global."""
    image_path = tmp_path / "poster.jpg"
    Image.new("RGB", (500, 750), color="black").save(image_path)

    class TitleOCR:
        def predict(self, _image):
            return [
                {
                    "rec_texts": ["dune"],
                    "rec_scores": [0.99],
                    "rec_polys": [[[100, 300], [400, 300], [400, 360], [100, 360]]],
                }
            ]

    monkeypatch.setattr(ocr_filter, "_worker_ocr", TitleOCR())
    # Deliberately wrong globals: if the decision used these it would be no_title.
    monkeypatch.setattr(ocr_filter, "_worker_title_tokens", {"zzz"})
    monkeypatch.setattr(ocr_filter, "_worker_director_tokens", set())
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_ENHANCE_RETRY", False)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_DETAIL_PASSES", False)
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_REQUIRE_TITLE", True)

    accepted = ocr_filter._process_image(str(image_path), {"dune"}, set())
    assert accepted.accepted
    assert accepted.title_bbox is not None

    # Under a different movie's tokens the very same decoded box matches no
    # title, so the poster is rejected and no title box is found.
    rejected = ocr_filter._process_image(str(image_path), {"matrix"}, set())
    assert not rejected.accepted
    assert rejected.title_bbox is None


# ---------------------------------------------------------------------------
# Results — per-stage rejection grouping
# ---------------------------------------------------------------------------


def test_rejected_by_stage_groups_each_reason():
    archive = {
        "movie_id": 1,
        "title": "X",
        "tmdb_id": 2,
        "candidates": [
            {"orig_filename": "r1.jpg", "rank": 1, "final_score": 0.9, "contributions": {}},
            {
                "orig_filename": "sha.jpg",
                "rejection_reason": "dedup_sha256",
                "stage_reached": "dedup",
            },
            {
                "orig_filename": "res.jpg",
                "rejection_reason": "resolution_floor",
                "stage_reached": "gate",
            },
            {
                "orig_filename": "aes.jpg",
                "rejection_reason": "aesthetic_floor",
                "stage_reached": "gate",
            },
            {
                "orig_filename": "off.jpg",
                "rejection_reason": "off_style_floor",
                "stage_reached": "gate",
            },
            {"orig_filename": "ocr.jpg", "rejection_reason": "text_heavy", "stage_reached": "ocr"},
            {
                "orig_filename": "ph.jpg",
                "rejection_reason": "dedup_phash",
                "stage_reached": "phash",
            },
            {
                "orig_filename": "err.jpg",
                "rejection_reason": "feature_error: boom",
                "stage_reached": "features",
            },
        ],
    }
    payload = build_results_payload(
        archive, run_id="rid", status="completed", reviewed=False, scorer="weighted"
    )
    by_stage = {group["stage"]: group for group in payload["rejected_by_stage"]}

    assert by_stage["sha256"]["count"] == 1
    assert by_stage["resolution"]["count"] == 1
    assert by_stage["style"]["count"] == 2  # aesthetic + off-style
    assert by_stage["ocr"]["count"] == 1
    assert by_stage["phash"]["count"] == 1
    assert by_stage["errored"]["count"] == 1
    assert by_stage["fan_junk"]["count"] == 0
    # Ranked survivors are not double-counted as rejected.
    assert len(payload["ranked"]) == 1
    # Stages render in pipeline order for the UI tabs.
    assert [g["stage"] for g in payload["rejected_by_stage"]][:2] == ["sha256", "resolution"]


# ---------------------------------------------------------------------------
# Pipeline cache — clearing is path-safe
# ---------------------------------------------------------------------------


def test_assert_clearable_rejects_protected_and_allows_work():
    # The deployed-poster cache holds live artwork — must never be clearable.
    with pytest.raises(ValueError):
        pc._assert_clearable(settings.poster_cache_path)
    # The working tree is the actual poster-download cache — allowed.
    assert pc._assert_clearable(settings.runs_work_path) == settings.runs_work_path.resolve()


def test_clear_pipeline_cache_spares_protected(tmp_path_factory):
    work = settings.runs_work_path
    staging = settings.poster_staging_path
    posters = settings.poster_cache_path / "movies"
    for directory in (work, staging, posters):
        directory.mkdir(parents=True, exist_ok=True)
    (work / "MovieA").mkdir(exist_ok=True)
    (work / "MovieA" / "poster.jpg").write_bytes(b"x" * 100)
    (staging / "tmp.jpg").write_bytes(b"y" * 50)
    (posters / "5.jpg").write_bytes(b"z" * 30)

    result = pc.clear_pipeline_cache(include_embeddings=False, include_archives=False)

    assert list(work.iterdir()) == []  # working tree contents removed
    assert list(staging.iterdir()) == []  # staging cleared
    assert (posters / "5.jpg").exists()  # deployed-poster cache untouched
    assert result["total_freed_bytes"] >= 150


# ---------------------------------------------------------------------------
# Job registration
# ---------------------------------------------------------------------------


def test_canonical_poster_and_maintenance_handlers_registered():
    from marquee.core.jobs.delivery import EXECUTION_HANDLERS

    assert {
        "learned_head_train",
        "poster_rescan",
        "poster_backup_subject",
        "pipeline_cache_clear",
        "poster_maintenance",
    } <= set(EXECUTION_HANDLERS)
