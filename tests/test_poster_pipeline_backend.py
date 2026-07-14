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
from sqlalchemy import select

from marquee.api.results import build_results_payload
from marquee.config import settings
from marquee.core import pipeline_cache as pc
from marquee.models import Movie, PipelineRun
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


@pytest.mark.asyncio
async def test_run_batch_finalizes_rows_when_exception_escapes(db, tmp_path: Path, monkeypatch):
    async def fake_fetch_candidates(_tmdb, _movie):
        return [], None

    async def boom(_contexts, _progress):
        raise RuntimeError("download exploded")

    monkeypatch.setattr(br, "fetch_candidates", fake_fetch_candidates)
    monkeypatch.setattr(br, "_download_phase", boom)

    movie = Movie(
        title="Boom",
        year=2024,
        folder_path=str(tmp_path / "boom"),
        movie_file_path="boom.mkv",
        tmdb_id=700,
    )
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    with pytest.raises(RuntimeError, match="download exploded"):
        await br.run_batch(
            job_id="batch-boom",
            movies=[(movie.id, movie.title, movie.tmdb_id)],
            tmdb=object(),
            extractor=object(),
            progress=None,
            should_cancel=lambda: False,
        )

    run = (
        await db.execute(select(PipelineRun).where(PipelineRun.movie_id == movie.id))
    ).scalar_one()
    await db.refresh(run)
    assert run.status == "failed"
    assert run.completed_at is not None
    assert run.error == "download exploded"
    assert run.media_type == "movie"

    import json

    archive = json.loads(Path(run.archive_path).read_text())
    assert archive["media_type"] == "movie"
    assert "subject" not in archive


@pytest.mark.asyncio
async def test_run_batch_assets_creates_tv_pipeline_runs_with_subject_fks(
    db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """One series asset + one season asset: distinct work dirs, PipelineRun
    rows carry the right subject FKs (never movie_id), and the archive gains
    the additive media_type/subject keys (design 04 §9.2/§9.6)."""
    import json

    from marquee.core.poster_subjects import PosterSubject
    from marquee.ml.namespaces import get_namespace
    from marquee.models import Season, Series

    series_folder = tmp_path / "Breaking Bad"
    series_folder.mkdir(parents=True)
    series = Series(
        title="Breaking Bad",
        year=2008,
        series_path=str(series_folder),
        tmdb_id=1396,
        sonarr_id=10,
    )
    db.add(series)
    await db.flush()
    season = Season(series_id=series.id, season_number=1, episode_count=7, episode_file_count=7)
    db.add(season)
    await db.commit()
    await db.refresh(series)
    await db.refresh(season)

    class _FakeTMDB:
        async def get_tv_images(self, _tmdb_id):
            return []

        async def get_tv_primary_poster(self, _tmdb_id):
            return None

        async def get_season_images(self, _tmdb_id, _season_number):
            return []

        async def get_season_primary_poster(self, _tmdb_id, _season_number):
            return None

    async def boom(_contexts, _progress):
        raise RuntimeError("download exploded")

    monkeypatch.setattr(br, "_download_phase", boom)

    series_subject = PosterSubject.from_series(series)
    season_subject = PosterSubject.from_season(season, series)
    assets = [
        br.AssetSpec(
            media_type="series",
            subject_id=series.id,
            title=series_subject.title,
            tmdb_id=series.tmdb_id,
            series_id=series.id,
            ocr_title=series.title,
        ),
        br.AssetSpec(
            media_type="season",
            subject_id=season.id,
            title=season_subject.title,
            tmdb_id=series.tmdb_id,
            series_id=series.id,
            season_id=season.id,
            season_number=season.season_number,
            ocr_title=series.title,
        ),
    ]

    with pytest.raises(RuntimeError, match="download exploded"):
        await br.run_batch_assets(
            job_id="batch-tv",
            assets=assets,
            tmdb=_FakeTMDB(),
            extractor=object(),
            taste_namespace=get_namespace("tv"),
            progress=None,
            should_cancel=lambda: False,
        )

    series_run = (
        await db.execute(select(PipelineRun).where(PipelineRun.series_id == series.id))
    ).scalars().all()
    season_run = (
        await db.execute(select(PipelineRun).where(PipelineRun.season_id == season.id))
    ).scalar_one()
    show_run = next(r for r in series_run if r.season_id is None)
    await db.refresh(show_run)
    await db.refresh(season_run)

    assert show_run.media_type == "series"
    assert show_run.movie_id is None
    assert show_run.series_id == series.id
    assert season_run.media_type == "season"
    assert season_run.movie_id is None
    assert season_run.series_id == series.id
    assert season_run.season_id == season.id

    # Distinct work directories (season title carries the " - Season NN" suffix).
    assert show_run.output_dir != season_run.output_dir

    show_archive = json.loads(Path(show_run.archive_path).read_text())
    season_archive = json.loads(Path(season_run.archive_path).read_text())
    assert show_archive["media_type"] == "series"
    assert show_archive["subject"]["series_id"] == series.id
    assert season_archive["media_type"] == "season"
    assert season_archive["subject"]["season_number"] == 1
    assert season_archive["subject"]["series_id"] == series.id


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
        "pipeline_cache_clear",
        "poster_backup_all",
        "poster_maintenance",
    } <= registered_types()
    from marquee.core.jobs.delivery import EXECUTION_HANDLERS

    assert {"learned_head_train", "poster_rescan"} <= set(EXECUTION_HANDLERS)
