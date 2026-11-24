"""The residual's training, keeping, activation, and binding to a profile, from made-up
events against a real PostgreSQL server."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import pipeline_settings, settings
from marquee.db.models import Movie, PreferenceEvent, ResidualTraining, TasteProfile
from marquee.ml import residual as engine
from marquee.ml.residual import ResidualArtifact, ResidualEvaluation, baseline_signature
from marquee.pipeline.scorer import ResidualRuntimeContext, select_scorer
from marquee.taste import rebuild, residuals

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
CANDIDATES = 8


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


async def _profile(session: AsyncSession, n: int = 1) -> TasteProfile:
    sha = hashlib.sha256(f"profile {n}".encode()).hexdigest()
    row = await rebuild.register(
        session,
        sha256=sha,
        revision="b" * 64,
        source="seed" if n == 1 else "rebuild",
        positives=30,
        negatives=0,
        movie_ids=[],
        file_name=f"{sha}.npz",
    )
    await session.commit()
    return row


async def _events(
    session: AsyncSession, movies: int, *, follows_baseline: bool = False, start: int = 0
) -> None:
    """One ``rank`` per movie over :data:`CANDIDATES` candidates. The owner orders by
    ``aesthetic``; the baseline score runs (slightly) the other way unless
    ``follows_baseline``. The feature values lie between 0.4 and 0.9, as real normalised
    features mostly do."""
    for m in range(start, start + movies):
        movie = Movie(
            radarr_id=1000 + m,
            title=f"Movie {m}",
            sort_title=f"movie {m:03d}",
            radarr_path=f"/m/Movie {m}",
            has_file=True,
            last_seen_at=NOW,
        )
        session.add(movie)
        await session.flush()
        exposed = []
        for i in range(CANDIDATES):
            aesthetic = 0.9 - 0.5 * i / (CANDIDATES - 1)  # c0 is the owner's favourite
            step = 0.002 * i
            baseline = 0.5 - step if follows_baseline else 0.5 + step
            exposed.append(
                {
                    "candidate_id": f"c{i}.jpg",
                    "rank": i + 1,
                    "baseline_rank": i + 1,
                    "baseline_score": round(baseline, 4),
                    "normalized_features": {
                        "aesthetic": aesthetic,
                        "knn_sim": 0.55 + 0.05 * ((m + i) % 5),
                    },
                }
            )
        session.add(
            PreferenceEvent(
                movie_id=movie.id,
                action="rank",
                exposed=exposed,
                context={
                    "order": [f"c{i}.jpg" for i in range(CANDIDATES)],
                    "hated": [],
                    "selected_candidate": "c0.jpg",
                },
                idempotency_key=f"event-{m}",
                created_at=NOW,
            )
        )
    await session.commit()


async def _rows(session: AsyncSession) -> list[ResidualTraining]:
    return list(await session.scalars(select(ResidualTraining).order_by(ResidualTraining.id)))


async def test_too_few_movies_changes_nothing_and_is_recorded(db_session: AsyncSession) -> None:
    profile = await _profile(db_session)
    await _events(db_session, 5)
    trained = await residuals.train(db_session)
    row = trained.row
    assert (row.outcome, row.reason) == ("no_change", "insufficient_evidence")
    assert (row.subjects, row.pairs, row.events) == (5, 5 * 28, 5)
    assert row.profile_id == profile.id and row.artifact_sha256 is None and not row.is_active
    assert not trained.activated
    assert not settings.residuals_dir.exists() or not any(settings.residuals_dir.iterdir())
    state = await residuals.state(db_session)
    assert state["in_use"] is False and "insufficient_evidence" in state["why"]
    assert state["missing"] == {"subjects": 20, "pairs": 60}


async def test_too_few_pairs_changes_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _profile(db_session)
    await _events(db_session, 30)
    monkeypatch.setattr(pipeline_settings, "RESIDUAL_MIN_PAIRS", 10_000)
    row = (await residuals.train(db_session)).row
    assert (row.outcome, row.reason, row.subjects) == ("no_change", "insufficient_evidence", 30)


async def test_no_held_out_improvement_changes_nothing(db_session: AsyncSession) -> None:
    await _profile(db_session)
    await _events(db_session, 30, follows_baseline=True)
    row = (await residuals.train(db_session)).row
    assert (row.outcome, row.reason) == ("no_change", "no_held_out_improvement")
    assert row.report["evaluation"]["improvement"] < 0.02
    assert await residuals.active(db_session) is None


async def test_an_activation_keeps_the_artifact_and_runs_use_it(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = await _profile(db_session)
    await _events(db_session, 30)
    trained = await residuals.train(db_session, job_id=None)
    row = trained.row
    assert row.outcome == "activate" and row.is_active and trained.activated
    path = settings.residuals_dir / f"{row.artifact_sha256}.npz"
    assert row.file_name == path.name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row.artifact_sha256
    assert not list(settings.residuals_dir.glob(".pending-*"))
    evaluation = row.report["evaluation"]
    assert evaluation["candidate_accuracy"] - evaluation["baseline_accuracy"] >= 0.02

    context = ResidualRuntimeContext(
        library="movies",
        baseline_signature=baseline_signature(pipeline_settings.scorer_weights),
        profile_checksum=profile.sha256,
        profile_generation=profile.id,
        artifact_id=row.id,
        artifact_checksum=row.artifact_sha256,
    )
    assert select_scorer(artifact_path=path, context=context).name == "residual"
    # A missing artifact file: the run still ranks, by the weighted scorer.
    assert select_scorer(artifact_path=path.with_name("gone.npz"), context=context).name == (
        "weighted"
    )
    monkeypatch.setattr(pipeline_settings, "SCORER", "weighted")
    assert select_scorer(artifact_path=path, context=context).name == "weighted"
    state = await residuals.state(db_session)
    assert state["in_use"] is False and "SCORER is weighted" in state["why"]
    monkeypatch.setattr(pipeline_settings, "SCORER", "auto")
    state = await residuals.state(db_session)
    assert state["in_use"] is True and state["active"]["id"] == row.id
    assert set(state["active"]["weights"]) == {"aesthetic", "knn_sim"}
    assert state["active"]["weights"]["aesthetic"] > 0


async def test_a_new_profile_makes_the_artifact_dormant_until_trained_again(
    db_session: AsyncSession,
) -> None:
    first = await _profile(db_session, 1)
    await _events(db_session, 30)
    old = (await residuals.train(db_session)).row
    old_path = settings.residuals_dir / old.file_name  # type: ignore[operator]

    second = await _profile(db_session, 2)  # a rebuild published another profile
    await db_session.refresh(old)
    assert not old.is_active and old.deactivated_at is not None
    stale = ResidualRuntimeContext(
        library="movies",
        baseline_signature=baseline_signature(pipeline_settings.scorer_weights),
        profile_checksum=second.sha256,
        profile_generation=second.id,
    )
    # The old artifact against the new profile: the engine falls back, silently but for a log.
    assert select_scorer(artifact_path=old_path, context=stale).name == "weighted"

    new = (await residuals.train(db_session)).row
    assert new.is_active and new.profile_id == second.id and new.id != old.id
    # What the next run hands the GPU child: the new profile and the new artifact.
    from marquee.jobs.handlers.poster_run import identities

    profile, artifact = await identities(db_session)
    assert profile is not None and profile["id"] == second.id
    assert artifact == {
        "id": new.id,
        "sha256": new.artifact_sha256,
        "file_name": new.file_name,
    }
    fresh = ResidualRuntimeContext(
        library="movies",
        baseline_signature=baseline_signature(pipeline_settings.scorer_weights),
        profile_checksum=second.sha256,
        profile_generation=second.id,
        artifact_id=new.id,
        artifact_checksum=new.artifact_sha256,
    )
    new_path = settings.residuals_dir / new.file_name  # type: ignore[operator]
    assert select_scorer(artifact_path=new_path, context=fresh).name == "residual"
    assert first.id != second.id


def _fake_artifact(profile: TasteProfile, seed: int) -> ResidualArtifact:
    import numpy as np

    return ResidualArtifact(
        namespace="movies",
        feature_names=["aesthetic"],
        weights=np.asarray([0.1 * (seed + 1)]),
        bias=0.0,
        alpha=0.5,
        delta_max=0.75,
        baseline_signature=baseline_signature(pipeline_settings.scorer_weights),
        profile_checksum=profile.sha256,
        evidence_revision="c" * 64,
        seed=seed,
        evaluation=ResidualEvaluation(0.5, 0.6, 0.1, 3, 30, 0.01, 0.02),
        trained_at=f"2026-11-24T00:00:{seed:02d}+00:00",
        profile_generation=profile.id,
    )


async def test_a_second_training_is_checked_against_the_active_artifact(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _profile(db_session)
    await _events(db_session, 30)
    first = (await residuals.train(db_session)).row
    seen: dict[str, Any] = {}

    def regression(pairs: list, **kwargs: Any) -> tuple[None, dict[str, Any]]:
        seen.update(kwargs)
        return None, {"outcome": "no_change", "reason": "active_residual_regression"}

    monkeypatch.setattr(engine, "train_residual", regression)
    second = (await residuals.train(db_session)).row
    assert seen["active_residual"] is not None
    assert seen["active_residual"].evidence_revision == first.evidence_digest
    assert (second.outcome, second.reason) == ("no_change", "active_residual_regression")
    await db_session.refresh(first)
    assert first.is_active and (await residuals.active(db_session)).id == first.id  # type: ignore[union-attr]


async def test_a_training_that_raises_is_recorded_and_not_raised(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _profile(db_session)

    def broken(pairs: list, **kwargs: Any) -> Any:
        raise RuntimeError("the engine broke")

    monkeypatch.setattr(engine, "train_residual", broken)
    row = (await residuals.train(db_session)).row
    assert row.outcome == "failed" and row.reason == "error"
    assert "the engine broke" in row.report["error"]


async def test_no_profile_no_training(db_session: AsyncSession) -> None:
    with pytest.raises(residuals.ResidualError, match="no active taste profile"):
        await residuals.train(db_session)
    assert await _rows(db_session) == []


async def test_a_crash_between_the_file_and_the_row_is_finished_by_the_next_training(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = await _profile(db_session)
    record = {
        "job_id": None,
        "profile_id": profile.id,
        "profile_sha256": profile.sha256,
        "subjects": 30,
        "pairs": 840,
        "events": 30,
        "evidence_digest": "c" * 64,
        "report": {"outcome": "activate", "evaluation": {"candidate_accuracy": 0.6}},
        "reason": None,
    }
    sha = residuals._keep(_fake_artifact(profile, 1), record)  # then the worker died
    assert (settings.residuals_dir / f".pending-{sha}.json").is_file()
    assert await _rows(db_session) == []

    def nothing(pairs: list, **kwargs: Any) -> tuple[None, dict[str, Any]]:
        return None, {"outcome": "no_change", "reason": "insufficient_evidence"}

    monkeypatch.setattr(engine, "train_residual", nothing)
    trained = await residuals.train(db_session)
    rows = await _rows(db_session)
    assert [r.artifact_sha256 for r in rows] == [sha, None]
    assert rows[0].is_active and rows[0].outcome == "activate" and rows[0].subjects == 30
    assert trained.finished_pending == [rows[0].id]
    assert not (settings.residuals_dir / f".pending-{sha}.json").exists()
    assert (settings.residuals_dir / f"{sha}.npz").is_file()


async def test_only_the_five_newest_artifacts_and_the_active_one_are_kept(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = await _profile(db_session)
    seeds = iter(range(100))

    def activation(pairs: list, **kwargs: Any) -> tuple[ResidualArtifact, dict[str, Any]]:
        return _fake_artifact(profile, next(seeds)), {"outcome": "activate"}

    monkeypatch.setattr(engine, "train_residual", activation)
    for _ in range(7):
        await residuals.train(db_session)
    rows = await _rows(db_session)
    kept = sorted(p.name for p in settings.residuals_dir.glob("*.npz"))
    assert kept == sorted(r.file_name for r in rows[-5:])  # type: ignore[type-var]
    assert [r.is_active for r in rows] == [False] * 6 + [True]
    assert all(r.file_name for r in rows)  # rows keep their names when files go
