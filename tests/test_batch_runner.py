"""Per-movie/per-candidate isolation in the cross-movie batch stages."""

from __future__ import annotations

import time
from pathlib import Path

import marquee.pipeline.batch_runner as batch_runner
from marquee.pipeline.batch_runner import _BatchMovie, _detail_batch, _rank
from marquee.pipeline.gate import PosterGate
from marquee.pipeline.runner import FetchOutcome
from marquee.pipeline.types import CandidateScore, FeatureVector, OCRCandidateResult


def _ctx(movie_id: int, title: str) -> _BatchMovie:
    return _BatchMovie(
        run_id=f"run-{movie_id}",
        movie_id=movie_id,
        title=title,
        tmdb_id=None,
        out_dir=Path("/tmp/unused"),
        originals_dir=Path("/tmp/unused"),
        started_at="2026-06-27T00:00:00Z",
        start_perf=time.perf_counter(),
        index=movie_id,
        total=1,
        passed=[CandidateScore(image_path=Path(f"{title}.jpg"), orig_filename=f"{title}.jpg")],
    )


class _StubScorer:
    """Raises for the named movie, ranks trivially for everyone else."""

    def __init__(self, fails_for: str) -> None:
        self.fails_for = fails_for

    def rank(self, passed: list[CandidateScore]) -> list[CandidateScore]:
        if passed and passed[0].orig_filename == f"{self.fails_for}.jpg":
            raise RuntimeError(
                "Learned head expects features ['official_family'] that this run did not compute."
            )
        for candidate in passed:
            candidate.final_score = 1.0
        return passed


def test_rank_failure_is_isolated_to_one_movie() -> None:
    good_ctx = _ctx(1, "good_movie")
    bad_ctx = _ctx(2, "bad_movie")
    scorer = _StubScorer(fails_for="bad_movie")

    _rank(good_ctx, scorer, progress=None)
    _rank(bad_ctx, scorer, progress=None)

    assert good_ctx.status == "completed"
    assert good_ctx.error is None
    assert len(good_ctx.ranked) == 1

    assert bad_ctx.status == "failed"
    assert bad_ctx.error is not None
    assert "official_family" in bad_ctx.error
    assert bad_ctx.ranked == []


def _detail_ctx(movie_id: int, title: str) -> _BatchMovie:
    filename = f"{title}.jpg"
    record = CandidateScore(image_path=Path(filename), orig_filename=filename)
    ctx = _BatchMovie(
        run_id=f"run-{movie_id}",
        movie_id=movie_id,
        title=title,
        tmdb_id=None,
        out_dir=Path("/tmp/unused"),
        originals_dir=Path("/tmp/unused"),
        started_at="2026-06-27T00:00:00Z",
        start_perf=time.perf_counter(),
        index=movie_id,
        total=1,
    )
    ctx.fetch = FetchOutcome(
        candidate_map={},
        records={filename: record},
        resolution_by_name={},
        all_files=[],
        primary_name=None,
        counts={},
    )
    ctx.ocr_survivors = [
        OCRCandidateResult(
            image_path=Path(filename),
            accepted=True,
            detected_text="",
            reason=None,
            title_bbox=None,
        )
    ]
    return ctx


def _feature_vector(*, official_family: float | None) -> FeatureVector:
    return FeatureVector(
        knn_sim=0.5,
        aesthetic=6.0,
        title_colorfulness=30.0,
        text_residual=0.2,
        resolution=3.0,
        sharpness=500.0,
        face_area=0.2,
        provenance=0.7,
        lang_match=1.0,
        official_family=official_family,
    )


class _StubExtractor:
    """Mirrors the real bug: official_family is missing for one movie only."""

    def complete_batch(self, items, *, dino_vectors_out=None):
        return [
            _feature_vector(official_family=None if "bad_movie" in ocr_result.image_path.name else 0.8)
            for _features, ocr_result in items
        ]


class _StubDiagnosticScorer:
    """Mirrors LogisticHead.score(): raises iff a trained feature is absent."""

    name = "stub"

    def score(self, features: FeatureVector) -> tuple[float, dict[str, float]]:
        if features.official_family is None:
            raise RuntimeError(
                "Learned head expects features ['official_family'] that this run did not compute."
            )
        return 0.9, {"aesthetic": 0.9}


def test_detail_batch_score_failure_is_isolated_to_one_candidate(
    monkeypatch: object,
) -> None:
    good_ctx = _detail_ctx(1, "good_movie")
    bad_ctx = _detail_ctx(2, "bad_movie")

    monkeypatch.setattr(batch_runner, "select_scorer", lambda: _StubDiagnosticScorer())

    _detail_batch([good_ctx, bad_ctx], _StubExtractor(), PosterGate(), progress=None)

    good_record = good_ctx.fetch.records["good_movie.jpg"]
    bad_record = bad_ctx.fetch.records["bad_movie.jpg"]

    assert good_record.stage_reached == "gate"
    assert good_record.contributions == {"aesthetic": 0.9}
    assert good_record in good_ctx.passed

    # The failing candidate still proceeds through gating despite the
    # diagnostic-score crash — only its UI explainability data is lost.
    assert bad_record.stage_reached == "gate"
    assert bad_record.contributions == {}
    assert bad_record in bad_ctx.passed
