"""Feedback against a real PostgreSQL server and a run folder in tmp_path."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import pipeline_settings, settings
from marquee.db.models import (
    Exemplar,
    Movie,
    PosterRun,
    PreferenceEvent,
    RunCandidate,
    TasteProfile,
)
from marquee.ml.residual import build_residual_pairs, train_residual
from marquee.taste import exemplars, feedback

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
PROFILE = "e" * 64


def _features(n: int) -> dict[str, float]:
    return {"knn_sim": 1.0 - n / 100, "aesthetic": 0.5 + n / 200, "dino_knn": 0.9 - n / 50}


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


async def _movie(session: AsyncSession, radarr_id: int = 1, title: str = "Black Widow") -> int:
    movie = Movie(
        radarr_id=radarr_id,
        title=title,
        sort_title=title.lower(),
        year=2021,
        radarr_path=f"/radarr/{title} (2021)",
        has_file=True,
        last_seen_at=NOW,
    )
    session.add(movie)
    await session.flush()
    return movie.id


async def _run(
    session: AsyncSession,
    movie_id: int,
    *,
    ranked: int = 12,
    rejected: int = 2,
    state: str = "completed",
    profile: str | None = PROFILE,
    folder_name: str | None = None,
) -> dict[str, Any]:
    """A run of ``ranked`` ranked candidates (r1.jpg is the pick) and ``rejected`` that a gate
    removed (downloaded too), plus one never downloaded, with its folder and archive."""
    run = PosterRun(
        movie_id=movie_id,
        state=state,
        run_folder=folder_name or f"0000000{movie_id}-run",
        started_at=NOW,
        profile_sha256=profile,
        scorer_name="weighted",
    )
    session.add(run)
    await session.flush()
    folder = settings.runs_dir / run.run_folder
    folder.mkdir(parents=True, exist_ok=True)
    ledger: list[dict[str, Any]] = []
    rows: dict[str, RunCandidate] = {}
    names = [f"r{n}.jpg" for n in range(1, ranked + 1)] + [f"x{n}.jpg" for n in range(rejected)]
    for name in names:
        data = f"{run.id}:{name}".encode()
        (folder / name).write_bytes(data)
        n = int(name[1:-4]) if name.startswith("r") else None
        ledger.append(
            {
                "orig_filename": name,
                "rank": n,
                "final_score": 1 - n / 100 if n else None,
                "normalized_features": _features(n or 50),
            }
        )
        rows[name] = RunCandidate(
            run_id=run.id,
            source_file=name,
            sha256=hashlib.sha256(data).hexdigest(),
            rank=n,
            final_score=1 - n / 100 if n else None,
            stack_rank=n,
            stack_label="A" if n else None,
            rejection_reason=None if n else "text_heavy",
            is_pick=n == 1,
        )
    rows["never.jpg"] = RunCandidate(run_id=run.id, source_file="never.jpg")
    ledger.append({"orig_filename": "never.jpg"})
    (folder / "pipeline_run.json").write_text(
        json.dumps({"diagnostic_ledger": {"candidates": ledger}})
    )
    session.add_all(rows.values())
    await session.flush()
    return {"run": run.id, "folder": folder, **{name: row.id for name, row in rows.items()}}


async def _profile(session: AsyncSession, movie_ids: list[int], sha: str = PROFILE) -> None:
    session.add(
        TasteProfile(
            sha256=sha,
            revision="b" * 64,
            source="seed",
            positives=len(movie_ids),
            negatives=0,
            positive_movie_ids=movie_ids,
            file_name=f"{sha}.npz",
            is_active=True,
        )
    )
    await session.flush()


async def _seed_positive(session: AsyncSession, movie_id: int, sha: str = "c" * 64) -> Exemplar:
    row = Exemplar(
        movie_id=movie_id,
        polarity="positive",
        weight=1.0,
        source="seed",
        state="active",
        sha256=sha,
        file_name=f"{sha}.png",
        staged_name="Black Widow (2021).png",
        created_at=NOW,
    )
    session.add(row)
    await session.flush()
    return row


async def _exemplars(session: AsyncSession) -> list[Exemplar]:
    return list(await session.scalars(select(Exemplar).order_by(Exemplar.id)))


# ── rank ──────────────────────────────────────────────────────────────────


async def test_rank_keeps_what_was_shown_and_makes_a_positive_and_a_negative(
    db_session: AsyncSession, data_dir: Path
) -> None:
    movie_id = await _movie(db_session)
    await _profile(db_session, [999])  # the run was blind
    ids = await _run(db_session, movie_id)

    outcome = await feedback.record(
        db_session,
        run_id=ids["run"],
        action="rank",
        idempotency_key="k1",
        order=[ids["r3.jpg"], ids["r1.jpg"]],
        hated=[ids["r2.jpg"], ids["r11.jpg"]],
    )

    event = outcome.event
    assert (event.action, event.chosen_source_file, event.pick_source_file) == (
        "rank",
        "r3.jpg",
        "r1.jpg",
    )
    assert (event.blind, event.profile_sha256, event.scorer_name) == (True, PROFILE, "weighted")
    assert event.context == {
        "order": ["r3.jpg", "r1.jpg"],
        "hated": ["r2.jpg", "r11.jpg"],
        "selected_candidate": "r3.jpg",
    }
    assert [row["candidate_id"] for row in event.exposed] == [f"r{n}.jpg" for n in range(1, 13)]
    first = event.exposed[0]
    assert first == {
        "candidate_id": "r1.jpg",
        "sha256": hashlib.sha256(f"{ids['run']}:r1.jpg".encode()).hexdigest(),
        "baseline_rank": 1,
        "baseline_score": 0.99,
        "stack": "1A",
        "normalized_features": _features(1),
    }

    rows = await _exemplars(db_session)
    positive, negative = rows  # r11 is ranked past the hard-negative limit
    assert pipeline_settings.FEEDBACK_HARD_NEGATIVE_RANK_MAX == 10
    assert (positive.polarity, positive.weight, positive.source_file) == ("positive", 1.0, "r3.jpg")
    assert positive.staged_name == "Black Widow (2021).jpg" and positive.event_id == event.id
    assert (negative.polarity, negative.weight, negative.source_file) == ("negative", 1.0, "r2.jpg")
    assert negative.staged_name is None
    for row in rows:
        assert (
            exemplars.read_kept(row.file_name, row.sha256)
            == (ids["folder"] / row.source_file).read_bytes()
        )
    assert outcome.changes.created == rows and outcome.changes.ended == []
    assert outcome.deploy_candidate_id == ids["r3.jpg"]
    assert outcome.revision_changed


async def test_the_same_key_returns_the_same_event_and_changes_nothing(
    db_session: AsyncSession,
) -> None:
    movie_id = await _movie(db_session)
    await _seed_positive(db_session, movie_id)
    ids = await _run(db_session, movie_id)
    first = await feedback.record(
        db_session,
        run_id=ids["run"],
        action="override",
        candidate_id=ids["r2.jpg"],
        idempotency_key="k",
    )
    again = await feedback.record(
        db_session, run_id=ids["run"], action="reject", idempotency_key="k"
    )
    assert again.replayed and not again.revision_changed
    assert again.event is first.event
    assert [r.id for r in again.changes.created] == [r.id for r in first.changes.created]
    assert [r.id for r in again.changes.ended] == [r.id for r in first.changes.ended]
    assert again.deploy_candidate_id == first.deploy_candidate_id == ids["r2.jpg"]
    assert await db_session.scalar(select(func.count()).select_from(PreferenceEvent)) == 1


# ── approval and override ─────────────────────────────────────────────────


async def test_approval_adds_a_weaker_positive_only_without_one(db_session: AsyncSession) -> None:
    blind = await _movie(db_session, 1, "Ash")
    known = await _movie(db_session, 2, "Die Hard")
    await _profile(db_session, [known])
    seed = await _seed_positive(db_session, known)
    blind_run = await _run(db_session, blind)
    known_run = await _run(db_session, known, folder_name="00000002-run")

    approved = await feedback.record(
        db_session, run_id=blind_run["run"], action="approval", idempotency_key="a"
    )
    (positive,) = approved.changes.created
    assert (positive.weight, positive.source_file, positive.staged_name) == (
        0.75,
        "r1.jpg",
        "Ash (2021).jpg",
    )
    assert approved.event.blind is True and approved.deploy_candidate_id == blind_run["r1.jpg"]

    again = await feedback.record(
        db_session, run_id=known_run["run"], action="approval", idempotency_key="b"
    )
    assert again.event.blind is False
    assert again.changes.created == [] and not again.revision_changed
    assert seed.state == "active"


async def test_an_override_of_the_pick_is_an_approval(db_session: AsyncSession) -> None:
    movie_id = await _movie(db_session)
    ids = await _run(db_session, movie_id)
    outcome = await feedback.record(
        db_session,
        run_id=ids["run"],
        action="override",
        candidate_id=ids["r1.jpg"],
        idempotency_key="k",
    )
    assert outcome.event.action == "approval"
    assert outcome.changes.created[0].weight == 0.75
    assert outcome.event.blind is None  # the run's profile is not registered


async def test_an_override_may_choose_a_candidate_a_gate_rejected(db_session: AsyncSession) -> None:
    movie_id = await _movie(db_session)
    seed = await _seed_positive(db_session, movie_id)
    ids = await _run(db_session, movie_id)
    outcome = await feedback.record(
        db_session,
        run_id=ids["run"],
        action="override",
        candidate_id=ids["x0.jpg"],
        idempotency_key="k",
    )
    (positive,) = outcome.changes.created
    assert positive.weight == 1.0 and positive.replaced_exemplar_id == seed.id
    assert outcome.changes.ended == [seed] and seed.state == "superseded"
    assert outcome.event.exposed[-1]["candidate_id"] == "x0.jpg"  # added to what was shown
    assert outcome.event.exposed[-1]["baseline_rank"] is None


# ── reject ────────────────────────────────────────────────────────────────


async def test_reject_with_none_named_hates_every_shown_one(db_session: AsyncSession) -> None:
    movie_id = await _movie(db_session)
    ids = await _run(db_session, movie_id, ranked=12)
    outcome = await feedback.record(
        db_session, run_id=ids["run"], action="reject", idempotency_key="k"
    )
    assert outcome.event.chosen_source_file is None and outcome.deploy_candidate_id is None
    assert outcome.event.context["hated"] == [f"r{n}.jpg" for n in range(1, 13)]
    negatives = [row for row in await _exemplars(db_session) if row.state == "active"]
    # Ten ranked hard negatives, of which the movie keeps the newest eight.
    assert len(outcome.changes.created) == 10
    assert len(negatives) == exemplars.MAX_ACTIVE_NEGATIVES
    assert {row.polarity for row in negatives} == {"negative"}


async def test_hating_the_active_positive_ends_it(db_session: AsyncSession) -> None:
    movie_id = await _movie(db_session)
    ids = await _run(db_session, movie_id)
    sha = hashlib.sha256(f"{ids['run']}:r4.jpg".encode()).hexdigest()
    seed = await _seed_positive(db_session, movie_id, sha)
    outcome = await feedback.record(
        db_session, run_id=ids["run"], action="reject", hated=[ids["r4.jpg"]], idempotency_key="k"
    )
    assert seed.state == "superseded" and outcome.changes.ended == [seed]
    (negative,) = outcome.changes.created
    assert negative.sha256 == sha


# ── refusals ──────────────────────────────────────────────────────────────


async def test_feedback_goes_to_the_latest_completed_run_only(db_session: AsyncSession) -> None:
    movie_id = await _movie(db_session)
    old = await _run(db_session, movie_id, folder_name="00000001-old")
    new = await _run(db_session, movie_id, folder_name="00000002-new")
    await _run(db_session, movie_id, state="failed", folder_name="00000003-failed")
    with pytest.raises(feedback.FeedbackRefusedError, match="not the latest completed run"):
        await feedback.record(db_session, run_id=old["run"], action="approval", idempotency_key="k")
    running = await _run(db_session, movie_id, state="running", folder_name="00000004-running")
    with pytest.raises(feedback.FeedbackRefusedError, match="is running"):
        await feedback.record(
            db_session, run_id=running["run"], action="approval", idempotency_key="k"
        )
    with pytest.raises(feedback.UnknownRunError):
        await feedback.record(db_session, run_id=999, action="approval", idempotency_key="k")
    with pytest.raises(feedback.UnknownCandidateError):
        await feedback.record(
            db_session,
            run_id=new["run"],
            action="override",
            candidate_id=old["r1.jpg"],
            idempotency_key="k",
        )
    assert await db_session.scalar(select(func.count()).select_from(PreferenceEvent)) == 0


@pytest.mark.parametrize(
    ("action", "args", "message"),
    [
        ("override", {}, "an override names a candidate"),
        ("override", {"candidate_id": "never.jpg"}, "has no downloaded file"),
        ("reject", {"hated": ["never.jpg"]}, "has no downloaded file"),
        ("rank", {"order": []}, "an order of at least one"),
        ("rank", {"order": ["r1.jpg", "r1.jpg"]}, "names a candidate twice"),
        ("rank", {"order": ["r1.jpg"], "hated": ["r1.jpg"]}, "both in the order and hated"),
        ("approval", {"hated": ["r2.jpg"]}, "takes no order"),
        ("rank", {"order": ["r3.jpg"], "shown": ["r1.jpg", "r2.jpg"]}, "not among those shown"),
        ("hate", {}, "unknown action"),
    ],
)
async def test_refusals(
    db_session: AsyncSession, action: str, args: dict[str, Any], message: str
) -> None:
    movie_id = await _movie(db_session)
    ids = await _run(db_session, movie_id)

    def resolve(value: Any) -> Any:
        if isinstance(value, list):
            return [ids[v] for v in value]
        return ids[value] if isinstance(value, str) else value

    with pytest.raises(feedback.FeedbackRefusedError, match=message):
        await feedback.record(
            db_session,
            run_id=ids["run"],
            action=action,
            idempotency_key="k",
            **{name: resolve(value) for name, value in args.items()},
        )


async def test_an_event_keeps_at_most_a_hundred_candidates(db_session: AsyncSession) -> None:
    movie_id = await _movie(db_session)
    ids = await _run(db_session, movie_id, ranked=101, rejected=0)
    with pytest.raises(feedback.FeedbackRefusedError, match="at most 100 shown"):
        await feedback.record(db_session, run_id=ids["run"], action="approval", idempotency_key="k")
    shown = [ids[f"r{n}.jpg"] for n in range(1, 101)]
    outcome = await feedback.record(
        db_session, run_id=ids["run"], action="approval", idempotency_key="k", shown=shown
    )
    assert len(outcome.event.exposed) == 100
    assert len(json.dumps(outcome.event.exposed)) < 30_000


@pytest.mark.parametrize("key", ["", "k" * 161])
async def test_an_idempotency_key_is_required(db_session: AsyncSession, key: str) -> None:
    with pytest.raises(feedback.FeedbackRefusedError, match="idempotency key"):
        await feedback.record(db_session, run_id=1, action="approval", idempotency_key=key)


# ── revoke ────────────────────────────────────────────────────────────────


async def test_revoke_undoes_the_exemplars_and_brings_back_the_replaced_one(
    db_session: AsyncSession,
) -> None:
    movie_id = await _movie(db_session)
    seed = await _seed_positive(db_session, movie_id)
    ids = await _run(db_session, movie_id)
    before = await exemplars.revision(db_session)
    ranked = await feedback.record(
        db_session,
        run_id=ids["run"],
        action="rank",
        order=[ids["r2.jpg"]],
        hated=[ids["r1.jpg"]],
        idempotency_key="k",
    )

    undone = await feedback.revoke(db_session, event_id=ranked.event.id)

    assert undone.event.action == "revoke" and undone.event.revokes_event_id == ranked.event.id
    assert undone.event.idempotency_key == f"revoke:{ranked.event.id}"
    assert {row.state for row in ranked.changes.created} == {"revoked"}
    assert seed.state == "active" and undone.changes.reactivated == [seed]
    assert undone.revision_after == before and undone.revision_changed
    assert ranked.event.action == "rank"  # the event itself is never changed

    again = await feedback.revoke(db_session, event_id=ranked.event.id)
    assert again.replayed and again.event is undone.event
    assert [r.id for r in again.changes.ended] == [r.id for r in undone.changes.ended]
    with pytest.raises(feedback.FeedbackRefusedError, match="already revoked by event"):
        await feedback.revoke(db_session, event_id=ranked.event.id, idempotency_key="other")
    with pytest.raises(feedback.FeedbackRefusedError, match="is a revoke"):
        await feedback.revoke(db_session, event_id=undone.event.id)
    with pytest.raises(feedback.UnknownEventError):
        await feedback.revoke(db_session, event_id=999)
    assert [e.id for e in await feedback.events_of_run(db_session, ids["run"])] == [
        ranked.event.id,
        undone.event.id,
    ]


async def test_revokes_out_of_order_leave_the_seed_exemplar_active(
    db_session: AsyncSession,
) -> None:
    """Pick A, pick B, revoke A, revoke B: the seed poster is the movie's positive again."""
    movie_id = await _movie(db_session)
    seed = await _seed_positive(db_session, movie_id)
    ids = await _run(db_session, movie_id)
    a = await feedback.record(
        db_session,
        run_id=ids["run"],
        action="override",
        candidate_id=ids["r2.jpg"],
        idempotency_key="a",
    )
    assert seed.state == "superseded" and seed.ended_by_event_id == a.event.id
    b = await feedback.record(
        db_session,
        run_id=ids["run"],
        action="override",
        candidate_id=ids["r3.jpg"],
        idempotency_key="b",
    )
    (picked_a,) = a.changes.created
    (picked_b,) = b.changes.created
    assert picked_a.ended_by_event_id == b.event.id

    first = await feedback.revoke(db_session, event_id=a.event.id)
    assert picked_b.state == "active" and first.changes.reactivated == []
    # It was already ended, by B; the revoke changes its state, not what ended it.
    assert picked_a.state == "revoked" and picked_a.ended_by_event_id == b.event.id

    second = await feedback.revoke(db_session, event_id=b.event.id)

    assert seed.state == "active" and seed.ended_at is None and seed.ended_by_event_id is None
    assert picked_b.state == "revoked" and picked_b.ended_by_event_id == second.event.id
    assert second.changes.reactivated == [seed]
    assert second.event.context == {"reactivated": [seed.id]}
    assert await exemplars.active_positive(db_session, movie_id) is seed
    assert second.revision_after == await exemplars.revision(db_session)
    again = await feedback.revoke(db_session, event_id=b.event.id)
    assert again.replayed and [r.id for r in again.changes.reactivated] == [seed.id]
    assert [r.id for r in again.changes.ended] == [r.id for r in second.changes.ended]


async def test_revoking_a_reject_that_hated_the_positive_brings_it_back(
    db_session: AsyncSession,
) -> None:
    movie_id = await _movie(db_session)
    ids = await _run(db_session, movie_id)
    sha = hashlib.sha256(f"{ids['run']}:r4.jpg".encode()).hexdigest()
    seed = await _seed_positive(db_session, movie_id, sha)
    rejected = await feedback.record(
        db_session, run_id=ids["run"], action="reject", hated=[ids["r4.jpg"]], idempotency_key="k"
    )
    assert seed.state == "superseded" and seed.ended_by_event_id == rejected.event.id

    undone = await feedback.revoke(db_session, event_id=rejected.event.id)

    assert seed.state == "active" and undone.changes.reactivated == [seed]
    assert {row.state for row in rejected.changes.created} == {"revoked"}


async def test_the_limit_of_eight_negatives_holds_after_a_revoke(
    db_session: AsyncSession,
) -> None:
    """R1 hates eight; R2, on a newer run, hates eight more and ends R1's; R3 hates two more
    and ends two of R2's. Revoking R2 brings back R1's newest six: eight are active."""
    movie_id = await _movie(db_session)
    run1 = await _run(db_session, movie_id, folder_name="00000001-a")
    r1 = await feedback.record(
        db_session,
        run_id=run1["run"],
        action="reject",
        hated=[run1[f"r{n}.jpg"] for n in range(1, 9)],
        idempotency_key="r1",
    )
    run2 = await _run(db_session, movie_id, folder_name="00000002-b")
    r2 = await feedback.record(
        db_session,
        run_id=run2["run"],
        action="reject",
        hated=[run2[f"r{n}.jpg"] for n in range(1, 9)],
        idempotency_key="r2",
    )
    assert {row.ended_by_event_id for row in r1.changes.created} == {r2.event.id}
    r3 = await feedback.record(
        db_session,
        run_id=run2["run"],
        action="reject",
        hated=[run2["r9.jpg"], run2["r10.jpg"]],
        idempotency_key="r3",
    )
    assert len(await exemplars.active_negatives(db_session, movie_id)) == 8

    undone = await feedback.revoke(db_session, event_id=r2.event.id)

    active = await exemplars.active_negatives(db_session, movie_id)
    assert len(active) == exemplars.MAX_ACTIVE_NEGATIVES
    newest_six = sorted(r1.changes.created, key=lambda row: row.id)[-6:]
    assert sorted(r.id for r in undone.changes.reactivated) == [r.id for r in newest_six]
    assert {r.id for r in active} == {r.id for r in newest_six} | {r.id for r in r3.changes.created}


# ── The residual's shape ──────────────────────────────────────────────────


async def test_recorded_events_are_in_the_shape_the_residual_reads(
    db_session: AsyncSession,
) -> None:
    ranked_movie = await _movie(db_session, 1, "One")
    override_movie = await _movie(db_session, 2, "Two")
    approved_movie = await _movie(db_session, 3, "Three")
    revoked_movie = await _movie(db_session, 4, "Four")
    a = await _run(db_session, ranked_movie, folder_name="00000001-one")
    b = await _run(db_session, override_movie, folder_name="00000002-two")
    c = await _run(db_session, approved_movie, folder_name="00000003-three")
    d = await _run(db_session, revoked_movie, folder_name="00000004-four")
    await feedback.record(
        db_session,
        run_id=a["run"],
        action="rank",
        order=[a["r3.jpg"], a["r1.jpg"], a["r2.jpg"]],
        hated=[a["r4.jpg"]],
        idempotency_key="a",
    )
    await feedback.record(
        db_session,
        run_id=b["run"],
        action="override",
        candidate_id=b["r5.jpg"],
        idempotency_key="b",
    )
    await feedback.record(db_session, run_id=c["run"], action="approval", idempotency_key="c")
    revoked = await feedback.record(
        db_session,
        run_id=d["run"],
        action="override",
        candidate_id=d["r2.jpg"],
        idempotency_key="d",
    )
    await feedback.revoke(db_session, event_id=revoked.event.id)

    events = await feedback.residual_events(db_session)
    assert [e.action for e in events] == ["rank", "override", "approval", "override", "revoke"]
    assert all(isinstance(e.id, str) and e.subject_kind == "movie" for e in events)
    assert events[3].revoked_event_id == events[4].id
    by_event = {e.action + e.subject_reference: build_residual_pairs([e]) for e in events}

    rank_pairs = by_event[f"rank{ranked_movie}"]
    # 3 > 1 > 2 gives three ordered pairs, and each of the three beats the hated one.
    assert len(rank_pairs) == 6
    assert {p.confidence for p in rank_pairs} == {"strong", "explicit"}
    assert all(p.subject == f"movie:{ranked_movie}" for p in rank_pairs)
    (override_pair,) = by_event[f"override{override_movie}"]
    assert override_pair.confidence == "strong" and override_pair.winner == _features(5)
    approval_pairs = by_event[f"approval{approved_movie}"]
    assert len(approval_pairs) == 3 and {p.weight for p in approval_pairs} == {1 / 3}
    assert by_event[f"override{revoked_movie}"] == []  # revoked: no pairs
    assert by_event[f"revoke{revoked_movie}"] == []

    pairs = build_residual_pairs(events)
    assert len(pairs) == 10
    artifact, report = train_residual(
        pairs,
        namespace="movies",
        baseline="weighted",
        profile_checksum=PROFILE,
        evidence_revision="r",
    )
    assert artifact is None and report["reason"] == "insufficient_evidence"
