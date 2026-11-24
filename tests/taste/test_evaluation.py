"""Blind evaluation: the match, the outcome, the summary, and recording a result."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import pipeline_settings, settings
from marquee.db.models import (
    Evaluation,
    EvaluationResult,
    Exemplar,
    Job,
    Movie,
    PosterRun,
    RunCandidate,
)
from marquee.ml.artifact_codec import unicode_array, unicode_scalar
from marquee.taste import evaluation
from marquee.taste.evaluation import Candidate

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
THRESHOLD = 0.95


def _vec(*values: float) -> np.ndarray:
    out = np.zeros(512, dtype=np.float32)
    out[: len(values)] = values
    return out


def _at_cosine(cosine: float) -> np.ndarray:
    """A unit vector at exactly this cosine to ``_vec(1)``."""
    return _vec(cosine, float(np.sqrt(max(0.0, 1 - cosine**2))))


def _c(name: str, rank: int | None = None, stack: int | None = None, **values: object) -> Candidate:
    return Candidate(
        source_file=name,
        sha256=hashlib.sha256(name.encode()).hexdigest(),
        rank=rank,
        stack_rank=stack,
        **values,  # type: ignore[arg-type]
    )


def _emb(*pairs: tuple[Candidate, np.ndarray]) -> dict[str, np.ndarray]:
    return {c.sha256: vector for c, vector in pairs if c.sha256}


# ── The match ─────────────────────────────────────────────────────────────


def test_the_closest_candidate_at_the_threshold_is_the_match() -> None:
    exact, near, far = _c("exact.jpg", 2, 2), _c("near.jpg", 1, 1), _c("far.jpg", 3, 3)
    embeddings = _emb((exact, _at_cosine(0.95)), (near, _at_cosine(0.9)), (far, _vec(0, 1)))
    # In float32 the cosine is 0.94999999...; the threshold is met at that value exactly.
    edge = float(evaluation._unit(_vec(1)) @ evaluation._unit(_at_cosine(0.95)))
    found = evaluation.match(_vec(1), [exact, near, far], embeddings, threshold=edge)
    assert found.candidate == exact and found.matched_by == "cosine"
    assert found.cosine == edge
    above = evaluation.match(_vec(1), [exact, near, far], embeddings, threshold=edge + 1e-9)
    assert above.candidate is None


def test_just_below_the_threshold_is_not_offered_and_keeps_the_closest() -> None:
    close = _c("close.jpg", 1, 1)
    found = evaluation.match(
        _vec(1), [close], _emb((close, _at_cosine(0.9499))), threshold=THRESHOLD
    )
    assert found.candidate is None and found.matched_by is None
    assert found.best_file == "close.jpg" and found.best_cosine == pytest.approx(0.9499, abs=1e-6)


def test_a_candidate_with_no_cached_embedding_is_counted_not_guessed() -> None:
    gated_early, ranked = _c("early.jpg", stage_reached="fetch"), _c("r.jpg", 1, 1)
    found = evaluation.match(
        _vec(1), [gated_early, ranked], _emb((ranked, _vec(0, 1))), threshold=THRESHOLD
    )
    assert found.unembedded == 1 and found.candidate is None and found.best_file == "r.jpg"


def test_a_tmdb_file_name_matches_first() -> None:
    named, closer = _c("named.jpg", 3, 3), _c("closer.jpg", 1, 1)
    embeddings = _emb((named, _at_cosine(0.5)), (closer, _vec(1)))
    found = evaluation.match(
        _vec(1), [named, closer], embeddings, source_file="named.jpg", threshold=THRESHOLD
    )
    assert found.candidate == named and found.matched_by == "file"
    assert found.cosine == pytest.approx(0.5, abs=1e-6) and found.best_file == "closer.jpg"


def test_a_file_name_the_run_does_not_have_falls_back_to_cosine() -> None:
    other = _c("other.jpg", 1, 1)
    found = evaluation.match(
        _vec(1), [other], _emb((other, _vec(1))), source_file="gone.jpg", threshold=THRESHOLD
    )
    assert found.candidate == other and found.matched_by == "cosine"


def test_a_file_name_match_on_a_duplicate_follows_it_to_the_one_kept() -> None:
    duplicate, kept = _c("dup.jpg", stage_reached="dedup"), _c("kept.jpg", 2, 1)
    found = evaluation.match(
        None,
        [duplicate, kept],
        {},
        source_file="dup.jpg",
        duplicates={"dup.jpg": "kept.jpg"},
        threshold=THRESHOLD,
    )
    assert found.candidate == kept


def test_a_tie_goes_to_the_better_ranked_candidate() -> None:
    worse, better = _c("a.jpg", 5, 3), _c("b.jpg", 2, 2)
    embeddings = _emb((worse, _vec(1)), (better, _vec(1)))
    found = evaluation.match(_vec(1), [worse, better], embeddings, threshold=THRESHOLD)
    assert found.candidate == better


# ── The outcome ───────────────────────────────────────────────────────────


def test_cards_are_design_stacks_in_rank_order_of_their_best_variant() -> None:
    rows = [_c("1a", 1, 2), _c("2", 2, 1), _c("1b", 3, 2), _c("lone", 4, None), _c("x")]
    cards = evaluation.cards_of(rows)
    assert [[c.source_file for c in card] for card in cards] == [["1a", "1b"], ["2"], ["lone"]]


def test_a_ranked_match_another_variant_of_the_picks_stack_is_the_same_design() -> None:
    pick, variant, other = _c("p", 1, 1, is_pick=True), _c("v", 2, 1), _c("o", 3, 2)
    found = evaluation.match(
        _vec(1),
        [pick, variant, other],
        _emb((variant, _vec(1)), (pick, _vec(0, 1))),
        threshold=THRESHOLD,
    )
    result = evaluation.outcome_of(found, [pick, variant, other])
    assert result.outcome == "ranked"
    assert (result.rank, result.ranked_count, result.card, result.cards) == (2, 3, 1, 2)
    assert result.pick_is_match is False and result.same_design is True
    assert result.pick_file == "p"


def test_the_pick_itself_is_exact_and_the_same_design() -> None:
    pick = _c("p", 1, 1, is_pick=True)
    found = evaluation.match(_vec(1), [pick], _emb((pick, _vec(1))), threshold=THRESHOLD)
    result = evaluation.outcome_of(found, [pick])
    assert (result.pick_is_match, result.same_design, result.card) == (True, True, 1)


def test_a_match_on_a_later_card() -> None:
    pick, second, third = _c("p", 1, 1, is_pick=True), _c("s", 2, 2), _c("t", 3, 3)
    found = evaluation.match(
        _vec(1), [pick, second, third], _emb((third, _vec(1))), threshold=THRESHOLD
    )
    result = evaluation.outcome_of(found, [pick, second, third])
    assert (result.card, result.cards, result.same_design) == (3, 3, False)


def test_a_gated_match_says_which_stage_and_why() -> None:
    gated = _c("g", stage_reached="ocr", rejection_reason="text_heavy")
    pick = _c("p", 1, 1, is_pick=True)
    found = evaluation.match(_vec(1), [gated, pick], _emb((gated, _vec(1))), threshold=THRESHOLD)
    result = evaluation.outcome_of(found, [gated, pick])
    assert (result.outcome, result.stage, result.reason) == ("gated", "ocr", "text_heavy")
    assert result.rank is None and result.match_file == "g"


def test_nothing_close_enough_is_not_offered() -> None:
    pick = _c("p", 1, 1, is_pick=True)
    found = evaluation.match(_vec(1), [pick], _emb((pick, _vec(0, 1))), threshold=THRESHOLD)
    result = evaluation.outcome_of(found, [pick])
    assert result.outcome == "not_offered" and result.best_file == "p"
    assert result.match_file is None


# ── The summary ───────────────────────────────────────────────────────────


def _row(outcome: str, source: str = "seed", **values: object) -> EvaluationResult:
    return EvaluationResult(outcome=outcome, exemplar_source=source, **values)


def test_the_summary_counts_seeds_and_feedback_apart() -> None:
    rows = [
        _row("ranked", card=1, pick_is_match=True, same_design=True),
        _row("ranked", card=2, pick_is_match=False, same_design=False),
        _row("ranked", card=6, pick_is_match=False, same_design=False),
        _row("gated", stage="ocr", reason="text_heavy"),
        _row("gated", stage="ocr", reason="text_heavy"),
        _row("not_offered"),
        _row("failed"),
        _row("ranked", "feedback", card=1, pick_is_match=False, same_design=True),
    ]
    summary = evaluation.summarize(rows)
    seed = summary["seed"]
    assert (seed["evaluated"], seed["ranked"], seed["gated"]) == (7, 3, 2)
    assert (seed["not_offered"], seed["failed"]) == (1, 1)
    assert seed["gated_by"] == {"ocr: text_heavy": 2}
    assert (seed["exact"], seed["same_design"], seed["top3"], seed["top5"]) == (1, 1, 2, 2)
    assert seed["median_card"] == 2
    assert seed["mean_reciprocal_card"] == pytest.approx((1 + 1 / 2 + 1 / 6) / 3, abs=1e-4)
    assert summary["feedback"]["evaluated"] == 1 and summary["feedback"]["same_design"] == 1
    assert summary["all"]["evaluated"] == 8


def test_an_empty_summary_has_no_median() -> None:
    assert evaluation.summarize([])["seed"]["median_card"] is None


# ── Recording a result ────────────────────────────────────────────────────

PROFILE_SHA = "7" * 64


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(pipeline_settings, "EMBEDDING_CACHE_DIR", tmp_path / "data" / "cache" / "e")
    return tmp_path / "data"


def _kept_profile(names: list[str], vectors: list[np.ndarray]) -> None:
    settings.profiles_dir.mkdir(parents=True, exist_ok=True)
    np.savez(
        settings.profiles_dir / f"{PROFILE_SHA}.npz",
        embeddings=np.stack(vectors).astype(np.float32),
        poster_names=unicode_array(names),
        model_name=unicode_scalar(pipeline_settings.AI_MODEL),
    )


def _cache(sha256: str, vector: np.ndarray, *, model: str | None = None) -> None:
    folder = Path(pipeline_settings.EMBEDDING_CACHE_DIR) / pipeline_settings.AI_MODEL
    folder.mkdir(parents=True, exist_ok=True)
    np.savez(
        folder / f"{sha256}.npz",
        embedding=vector,
        model_name=np.asarray(model or pipeline_settings.AI_MODEL),
        sha256=np.asarray(sha256),
    )


async def _setup(
    session: AsyncSession, *, state: str = "completed", source_file: str | None = None
) -> dict[str, int]:
    movie = Movie(
        radarr_id=1,
        title="Heat",
        sort_title="heat",
        year=1995,
        radarr_path="/m/Heat (1995)",
        has_file=True,
        tmdb_id=949,
        tmdb_enriched_at=NOW,
        last_seen_at=NOW,
    )
    session.add(movie)
    await session.flush()
    exemplar = Exemplar(
        movie_id=movie.id,
        polarity="positive",
        weight=1.0,
        source="feedback" if source_file else "seed",
        state="active",
        sha256="1" * 64,
        file_name=f"{'1' * 64}.png",
        staged_name="Heat (1995).png",
        source_file=source_file,
        event_id=None,
    )
    if source_file:
        from marquee.db.models import PreferenceEvent

        event = PreferenceEvent(
            movie_id=movie.id,
            action="override",
            exposed=[],
            context={},
            idempotency_key="k",
            created_at=NOW,
        )
        session.add(event)
        await session.flush()
        exemplar.event_id = event.id
    session.add(exemplar)
    job = Job(kind="batch", job_class="batch", state="running")
    session.add(job)
    await session.flush()
    row = Evaluation(
        job_id=job.id, profile_sha256=PROFILE_SHA, scorer="weighted", movies=1, queued=1
    )
    run = PosterRun(
        movie_id=movie.id,
        state=state,
        run_folder="00000009-heat",
        started_at=NOW,
        profile_sha256=PROFILE_SHA,
        masked=True,
        error=None if state == "completed" else "TMDB: timed out",
    )
    session.add_all([row, run])
    await session.flush()
    names = {
        "pick.jpg": (1, 1, True),
        "variant.jpg": (2, 1, False),
        "gated.jpg": (None, None, False),
    }
    for name, (rank, stack, is_pick) in names.items():
        session.add(
            RunCandidate(
                run_id=run.id,
                source_file=name,
                sha256=hashlib.sha256(name.encode()).hexdigest(),
                rank=rank,
                stack_rank=stack,
                stack_label="A" if rank == 1 else "B" if rank else None,
                is_pick=is_pick,
                stage_reached="ranked" if rank else "ocr",
                rejection_reason=None if rank else "text_heavy",
            )
        )
    folder = settings.runs_dir / run.run_folder
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pipeline_run.json").write_text(json.dumps({"diagnostic_ledger": {"candidates": []}}))
    await session.commit()
    return {"movie": movie.id, "evaluation": row.id, "run": run.id, "exemplar": exemplar.id}


def _sha(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


@pytest.mark.db
async def test_a_result_is_recorded_from_the_profile_and_the_cache(
    db_session: AsyncSession, data_dir: Path
) -> None:
    ids = await _setup(db_session)
    _kept_profile(["Heat (1995).png", "Ran (1985).png"], [_vec(1), _vec(0, 0, 1)])
    _cache(_sha("pick.jpg"), _vec(0, 1))
    _cache(_sha("variant.jpg"), _at_cosine(0.97))
    _cache(_sha("gated.jpg"), _at_cosine(0.5), model="another-model")  # not this model's

    row = await evaluation.record_run(db_session, ids["evaluation"], ids["movie"], ids["run"])

    assert row.outcome == "ranked" and row.matched_by == "cosine"
    assert (row.match_file, row.rank, row.card, row.cards) == ("variant.jpg", 2, 1, 1)
    assert row.match_cosine == pytest.approx(0.97, abs=1e-5)
    assert (row.pick_is_match, row.same_design, row.pick_file) == (False, True, "pick.jpg")
    assert (row.exemplar_id, row.exemplar_source) == (ids["exemplar"], "seed")
    assert row.unembedded == 1

    # Recorded again (a retried job): the same row, replaced.
    _cache(_sha("variant.jpg"), _vec(0, 1))
    again = await evaluation.record_run(db_session, ids["evaluation"], ids["movie"], ids["run"])
    assert again.id == row.id and again.outcome == "not_offered"
    assert len(list(await db_session.scalars(select(EvaluationResult)))) == 1


@pytest.mark.db
async def test_a_feedback_exemplar_is_matched_by_its_file(
    db_session: AsyncSession, data_dir: Path
) -> None:
    ids = await _setup(db_session, source_file="gated.jpg")
    _kept_profile(["Heat (1995).png"], [_vec(1)])
    row = await evaluation.record_run(db_session, ids["evaluation"], ids["movie"], ids["run"])
    assert (row.outcome, row.matched_by, row.match_file) == ("gated", "file", "gated.jpg")
    assert (row.stage, row.reason, row.exemplar_source) == ("ocr", "text_heavy", "feedback")


@pytest.mark.db
async def test_a_run_that_did_not_complete_is_a_failure(
    db_session: AsyncSession, data_dir: Path
) -> None:
    ids = await _setup(db_session, state="failed")
    row = await evaluation.record_run(db_session, ids["evaluation"], ids["movie"], ids["run"])
    assert row.outcome == "failed" and row.reason == "TMDB: timed out"
    other = await evaluation.record_failure(
        db_session, ids["evaluation"], ids["movie"], run_id=None, reason="the GPU process died"
    )
    assert other.id == row.id and other.run_id is None and other.reason == "the GPU process died"


@pytest.mark.db
async def test_an_exemplar_missing_from_the_profile_is_a_failure_not_a_guess(
    db_session: AsyncSession, data_dir: Path
) -> None:
    ids = await _setup(db_session)
    _kept_profile(["Ran (1985).png"], [_vec(1)])
    _cache(_sha("pick.jpg"), _vec(1))
    row = await evaluation.record_run(db_session, ids["evaluation"], ids["movie"], ids["run"])
    assert row.outcome == "failed" and "not in the profile" in (row.reason or "")


@pytest.mark.db
async def test_a_run_against_another_profile_is_a_failure(
    db_session: AsyncSession, data_dir: Path
) -> None:
    # A review answered during the evaluation queued a rebuild, and this masked run started
    # after it published: it measured another profile than the evaluation names.
    ids = await _setup(db_session)
    _kept_profile(["Heat (1995).png"], [_vec(1)])
    _cache(_sha("pick.jpg"), _vec(1))
    run = await db_session.get(PosterRun, ids["run"])
    assert run is not None
    run.profile_sha256 = "f" * 64
    await db_session.commit()
    row = await evaluation.record_run(db_session, ids["evaluation"], ids["movie"], ids["run"])
    assert row.outcome == "failed"
    assert row.reason == "the taste profile changed while the evaluation ran"
    assert row.run_id == ids["run"] and row.rank is None


@pytest.mark.db
async def test_only_masked_runs_are_recorded(db_session: AsyncSession, data_dir: Path) -> None:
    ids = await _setup(db_session)
    run = await db_session.get(PosterRun, ids["run"])
    assert run is not None
    run.masked = False
    await db_session.commit()
    with pytest.raises(evaluation.EvaluationError, match="not a masked run"):
        await evaluation.record_run(db_session, ids["evaluation"], ids["movie"], ids["run"])


@pytest.mark.db
async def test_the_view_counts_what_has_not_run(db_session: AsyncSession, data_dir: Path) -> None:
    ids = await _setup(db_session)
    row = await db_session.get(Evaluation, ids["evaluation"])
    assert row is not None
    assert (await evaluation.running(db_session)).id == row.id  # type: ignore[union-attr]
    assert await evaluation.latest_succeeded(db_session) is None
    job = await db_session.get(Job, row.job_id)
    assert job is not None
    job.state = "cancelled"
    await db_session.commit()
    view = await evaluation.view(db_session, row)
    assert (view.state, view.results, view.not_run) == ("cancelled", 0, 1)
    assert await evaluation.running(db_session) is None
    assert await evaluation.latest_succeeded(db_session) is None  # cancelled: never a headline
    job.state = "succeeded"
    await db_session.commit()
    assert (await evaluation.latest_succeeded(db_session)).id == row.id  # type: ignore[union-attr]
    assert await evaluation.latest_succeeded(db_session, before=row.id) is None


@pytest.mark.db
async def test_evaluable_movies_have_an_active_positive_and_can_run(
    db_session: AsyncSession, data_dir: Path
) -> None:
    ids = await _setup(db_session)
    bare = Movie(
        radarr_id=2,
        title="Alien",
        sort_title="alien",
        radarr_path="/m/Alien",
        has_file=True,
        tmdb_id=348,
        tmdb_enriched_at=NOW,
        last_seen_at=NOW,
    )
    db_session.add(bare)
    await db_session.commit()
    assert await evaluation.evaluable_movie_ids(db_session) == [ids["movie"]]
    assert await evaluation.evaluable_movie_ids(db_session, [bare.id]) == []
