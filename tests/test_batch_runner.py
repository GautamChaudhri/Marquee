"""Per-movie/per-candidate isolation in the cross-movie batch stages."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import marquee.pipeline.batch_runner as batch_runner
from marquee.core.poster_sources.tmdb import PosterCandidate
from marquee.pipeline.batch_runner import _BatchMovie, _detail_batch, _download_phase, _rank
from marquee.pipeline.gate import PosterGate
from marquee.pipeline.runner import FetchOutcome, ProgressEvent
from marquee.pipeline.types import (
    CandidateScore,
    FeatureVector,
    OCRCandidateResult,
    OCRTextBox,
)


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


class _FakeResponse:
    content = b"fake-poster-bytes"

    def raise_for_status(self) -> None:
        return None


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient so downloads never hit the network."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str) -> _FakeResponse:
        return _FakeResponse()


def _poster_candidate(file_path: str) -> PosterCandidate:
    return PosterCandidate(
        file_path=file_path,
        width=2000,
        height=3000,
        aspect_ratio=0.67,
        language="en",
        vote_average=5.0,
        vote_count=10,
    )


def _download_ctx(tmp_path: Path, movie_id: int, title: str, n_candidates: int) -> _BatchMovie:
    originals_dir = tmp_path / title / "0-originals"
    originals_dir.mkdir(parents=True)
    candidates = [_poster_candidate(f"/{title}-{i}.jpg") for i in range(n_candidates)]
    candidate_map = {batch_runner._candidate_filename(c): c for c in candidates}
    records = {
        name: CandidateScore(image_path=originals_dir / name, orig_filename=name)
        for name in candidate_map
    }
    ctx = _BatchMovie(
        run_id=f"run-{movie_id}",
        movie_id=movie_id,
        title=title,
        tmdb_id=movie_id,
        out_dir=tmp_path / title,
        originals_dir=originals_dir,
        started_at="2026-06-28T00:00:00Z",
        start_perf=time.perf_counter(),
        index=movie_id,
        total=1,
    )
    ctx.fetch = FetchOutcome(
        candidate_map=candidate_map,
        records=records,
        resolution_by_name={},
        all_files=[],
        primary_name=None,
        counts={
            "posters_found": n_candidates,
            "downloaded": 0,
            "skipped": 0,
            "errors": 0,
            "metadata_gated": 0,
        },
    )
    ctx._downloadable = candidates
    return ctx


async def test_download_phase_emits_global_progress_ticks_during_downloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(batch_runner.httpx, "AsyncClient", _FakeAsyncClient)

    ctx_a = _download_ctx(tmp_path, 1, "movie_a", 3)
    ctx_b = _download_ctx(tmp_path, 2, "movie_b", 2)
    ctx_empty = _download_ctx(tmp_path, 3, "movie_empty", 0)

    events: list[ProgressEvent] = []
    await _download_phase([ctx_a, ctx_b, ctx_empty], events.append)

    global_events = [e for e in events if e.movie_id is None]
    assert global_events[0].stage == "fetch"
    assert global_events[0].state == "start"
    assert global_events[0].total == 5  # 3 + 2 downloadable candidates; ctx_empty has none

    # A "progress" tick fires per individual download, not just at phase
    # boundaries — this is what keeps job.progress moving while the real
    # download work (potentially long) is happening.
    progress_ticks = [e for e in global_events if e.state == "progress"]
    assert [t.done for t in progress_ticks] == [1, 2, 3, 4, 5]
    assert all(t.total == 5 for t in progress_ticks)

    assert global_events[-1].state == "end"
    assert global_events[-1].survivors == 5

    # Each movie's own "fetch end" fires (not batched to wait on every other
    # movie), and the empty-candidate movie is finalized without ever
    # entering the download pool.
    per_movie_ends = {e.movie_id: e for e in events if e.movie_id is not None and e.state == "end"}
    assert per_movie_ends.keys() == {1, 2, 3}
    assert per_movie_ends[1].survivors == 3
    assert per_movie_ends[2].survivors == 2
    assert per_movie_ends[3].survivors == 0
    assert ctx_empty.status == "failed"  # nothing to download -> no poster files
    assert ctx_a.status != "failed"
    assert ctx_b.status != "failed"


def test_ocr_batch_persists_diagnostics_to_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: the batch engine (the path the UI uses) must persist OCR
    diagnostics onto every record — accepted AND rejected — so the per-run
    archive carries the real detected text / title bbox / residual boxes instead
    of nulls. Null OCR data is what forced the label-capture flow to synthesize
    logs. This keeps batch_runner._ocr_batch in lockstep with the single-movie
    engine's shared _attach_ocr_diagnostics helper.
    """
    out_dir = tmp_path / "alien"
    survivors_dir = out_dir / "1-style-survivors"
    survivors_dir.mkdir(parents=True)
    accepted_path = survivors_dir / "accepted.jpg"
    rejected_path = survivors_dir / "rejected.jpg"
    accepted_path.write_bytes(b"a")  # _copy_with_reason copies the rejected file
    rejected_path.write_bytes(b"b")

    records = {
        "accepted.jpg": CandidateScore(image_path=accepted_path, orig_filename="accepted.jpg"),
        "rejected.jpg": CandidateScore(image_path=rejected_path, orig_filename="rejected.jpg"),
    }
    ctx = _BatchMovie(
        run_id="run-1",
        movie_id=1,
        title="Alien",
        tmdb_id=None,
        out_dir=out_dir,
        originals_dir=out_dir / "0-originals",
        started_at="2026-06-29T00:00:00Z",
        start_perf=time.perf_counter(),
        index=1,
        total=1,
    )
    ctx.fetch = FetchOutcome(
        candidate_map={},
        records=records,
        resolution_by_name={},
        all_files=[],
        primary_name=None,
        counts={},
    )
    ctx.style_survivors = [accepted_path, rejected_path]

    title_bbox = ((0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0))
    residual = OCRTextBox(
        text="tagline noise",
        confidence=0.8,
        bbox=((0.0, 50.0), (40.0, 50.0), (40.0, 60.0), (0.0, 60.0)),
        area=400.0,
        geometry_valid=True,
    )

    def _fake_run_ocr_batch(items, *, num_workers=None, progress=None):
        results = []
        for path, _title_tokens, _director_tokens in items:
            if path.name == "accepted.jpg":
                results.append(
                    OCRCandidateResult(
                        image_path=path,
                        accepted=True,
                        detected_text="ALIEN",
                        reason=None,
                        title_bbox=title_bbox,
                        residual_boxes=[],
                        diagnostics={"decision": {"accepted": True, "reason": None}},
                    )
                )
            else:
                results.append(
                    OCRCandidateResult(
                        image_path=path,
                        accepted=False,
                        detected_text="ALIEN ROMULUS extra noise",
                        reason="text_heavy",
                        title_bbox=title_bbox,
                        residual_boxes=[residual],
                        diagnostics={"decision": {"accepted": False, "reason": "text_heavy"}},
                    )
                )
        return results

    monkeypatch.setattr(
        batch_runner.PosterTextFilter,
        "run_ocr_batch",
        staticmethod(_fake_run_ocr_batch),
    )

    batch_runner._ocr_batch([ctx], progress=None)

    accepted = records["accepted.jpg"]
    assert accepted.stage_reached == "ocr"
    assert accepted.ocr_detected_text == "ALIEN"
    assert accepted.ocr_title_bbox == [[0.0, 0.0], [10.0, 0.0], [10.0, 5.0], [0.0, 5.0]]
    assert accepted.ocr_residual_boxes == []
    # The full structured trace is persisted on DEBUG runs (conftest forces DEBUG).
    assert accepted.ocr_trace == {"decision": {"accepted": True, "reason": None}}

    rejected = records["rejected.jpg"]
    assert rejected.stage_reached == "ocr"
    assert rejected.rejection_reason == "text_heavy"
    assert rejected.ocr_detected_text == "ALIEN ROMULUS extra noise"
    assert rejected.ocr_title_bbox == [[0.0, 0.0], [10.0, 0.0], [10.0, 5.0], [0.0, 5.0]]
    assert rejected.ocr_residual_boxes is not None and len(rejected.ocr_residual_boxes) == 1
    assert rejected.ocr_residual_boxes[0]["text"] == "tagline noise"
    assert rejected.ocr_trace == {"decision": {"accepted": False, "reason": "text_heavy"}}


async def test_download_phase_finalizes_movies_that_failed_metadata_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(batch_runner.httpx, "AsyncClient", _FakeAsyncClient)

    ok_ctx = _download_ctx(tmp_path, 1, "ok_movie", 1)
    failed_ctx = _download_ctx(tmp_path, 2, "failed_movie", 1)
    failed_ctx.fetch = None  # simulates a Phase A metadata-fetch exception
    failed_ctx.status = "failed"

    events: list[ProgressEvent] = []
    await _download_phase([ok_ctx, failed_ctx], events.append)

    per_movie_ends = {e.movie_id: e for e in events if e.movie_id is not None and e.state == "end"}
    assert per_movie_ends.keys() == {1, 2}
    assert per_movie_ends[2].survivors == 0
