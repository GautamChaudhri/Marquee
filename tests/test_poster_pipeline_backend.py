"""Backend readiness for the poster-pipeline vertical slice.

Covers the new, ML-free units:
  * OCR per-task title tokens (one pool can serve many movies)
  * the batch runner's per-movie no-text fallback isolation
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
from marquee.pipeline import batch_runner as br
from marquee.pipeline import ocr_filter
from marquee.pipeline.runner import FetchOutcome
from marquee.pipeline.types import CandidateScore, OCRCandidateResult

_BBOX = ((100.0, 300.0), (400.0, 300.0), (400.0, 360.0), (100.0, 360.0))


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
# Batch runner — per-movie no-text fallback isolation
# ---------------------------------------------------------------------------


def _make_batch_movie(tmp_path: Path, movie_id: int, title: str, files: list[str]):
    out = tmp_path / f"movie{movie_id}"
    out.mkdir()
    (out / "0-originals").mkdir()
    records: dict[str, CandidateScore] = {}
    paths: list[Path] = []
    for name in files:
        path = out / name
        Image.new("RGB", (20, 30), color="black").save(path)
        records[name] = CandidateScore(image_path=path, orig_filename=name)
        paths.append(path)
    ctx = br._BatchMovie(
        run_id=f"run{movie_id}",
        movie_id=movie_id,
        title=title,
        tmdb_id=None,
        out_dir=out,
        originals_dir=out / "0-originals",
        started_at="2026-06-21T00:00:00+00:00",
        start_perf=0.0,
        index=movie_id,
        total=2,
    )
    ctx.fetch = FetchOutcome(
        candidate_map={},
        records=records,
        resolution_by_name={},
        all_files=paths,
        primary_name=None,
        counts={},
    )
    ctx.style_survivors = paths
    return ctx


def test_batch_ocr_fallback_is_isolated_per_movie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A movie with a titled survivor must NOT rescue its textless posters,
    while a movie with zero titled survivors must rescue its own — the no-text
    fallback is per movie, never across the shared batch."""
    movie_a = _make_batch_movie(tmp_path, 1, "Movie A", ["a1.jpg", "a2.jpg"])
    movie_b = _make_batch_movie(tmp_path, 2, "Movie B", ["b1.jpg"])

    def fake_run(items, *, num_workers=None, progress=None):
        results = []
        for item in items:
            path = item[0]
            if path.name == "a1.jpg":
                results.append(OCRCandidateResult(path, True, "movie a", None, _BBOX))
            else:
                results.append(OCRCandidateResult(path, False, "", "no_text", None))
        return results

    monkeypatch.setattr(br.PosterTextFilter, "run_ocr_batch", staticmethod(fake_run))
    monkeypatch.setattr(ocr_filter.pipeline_settings, "OCR_ACCEPT_NO_TEXT", True)

    br._ocr_batch([movie_a, movie_b], progress=None)

    # A had a titled survivor → a2 stays rejected (no rescue).
    assert {r.image_path.name for r in movie_a.ocr_survivors} == {"a1.jpg"}
    # B had nothing titled → its lone textless poster is rescued by the fallback.
    assert {r.image_path.name for r in movie_b.ocr_survivors} == {"b1.jpg"}


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
# Library-poster gathering for the initial-training button (source=library)
# ---------------------------------------------------------------------------


def test_copy_library_posters_names_and_dedupes(tmp_path: Path):
    from marquee.core.jobs.builtin_handlers import _copy_library_posters

    source = tmp_path / "deployed.jpg"
    source.write_bytes(b"a" * 10)
    dest = tmp_path / "dest"
    dest.mkdir()

    movies = [
        ("Dune", 2021, None, str(source)),  # copied via poster_path
        ("Dune", 2021, None, str(source)),  # same title+year → skipped
        ("No Source", 2000, None, None),  # no source → skipped
    ]
    count = _copy_library_posters(movies, dest)

    assert count == 1
    assert (dest / "Dune (2021).jpg").exists()
    assert sorted(p.name for p in dest.iterdir()) == ["Dune (2021).jpg"]


# ---------------------------------------------------------------------------
# Job registration
# ---------------------------------------------------------------------------


def test_new_job_handlers_registered():
    import marquee.core.jobs.builtin_handlers  # noqa: F401 — registers handlers
    from marquee.core.jobs.handlers import registered_types

    assert {
        "poster_pipeline_batch",
        "learned_head_train",
        "pipeline_cache_clear",
    } <= registered_types()
