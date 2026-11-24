"""The review, feedback, exemplar, and taste routes against the test database."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.db.models import Artwork, Movie, PosterRun, RunCandidate

NOW = datetime(2026, 11, 24, tzinfo=UTC)

ROUTES = [
    ("GET", "/api/review"),
    ("POST", "/api/runs/1/feedback"),
    ("GET", "/api/runs/1/feedback"),
    ("POST", "/api/feedback/1/revoke"),
    ("GET", "/api/feedback"),
    ("GET", "/api/taste"),
    ("POST", "/api/taste/rebuild"),
    ("GET", "/api/taste/exemplars"),
    ("GET", "/api/taste/exemplars/1/image"),
]


@pytest.fixture
def headers(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key}


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


async def _run(session: AsyncSession, data_dir: Path, title: str = "Alpha", n: int = 1) -> dict:
    movie = Movie(
        radarr_id=n,
        title=title,
        sort_title=title.lower(),
        year=2001,
        radarr_path=f"/radarr/movies/{title}",
        has_file=True,
        is_present=True,
        last_seen_at=NOW,
    )
    session.add(movie)
    await session.flush()
    run = PosterRun(
        movie_id=movie.id, state="completed", run_folder=f"0000000{n}-run", started_at=NOW
    )
    session.add(run)
    await session.flush()
    folder = data_dir / "runs" / run.run_folder
    folder.mkdir(parents=True)
    ids = {"movie": movie.id, "run": run.id}
    ledger = []
    for rank in (1, 2, 3):
        data = f"{title} {rank}".encode()
        (folder / f"r{rank}.jpg").write_bytes(data)
        row = RunCandidate(
            run_id=run.id,
            source_file=f"r{rank}.jpg",
            sha256=hashlib.sha256(data).hexdigest(),
            rank=rank,
            final_score=1 - rank / 10,
            is_pick=rank == 1,
        )
        session.add(row)
        await session.flush()
        ids[f"r{rank}"] = row.id
        ledger.append({"orig_filename": f"r{rank}.jpg", "normalized_features": {"knn_sim": rank}})
    (folder / "pipeline_run.json").write_text(
        json.dumps({"diagnostic_ledger": {"candidates": ledger}})
    )
    await session.commit()
    return ids


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_every_review_route_needs_the_key(
    offline_client: AsyncClient, api_key: str, method: str, path: str
) -> None:
    assert (await offline_client.request(method, path)).status_code == 401
    wrong = await offline_client.request(method, path, headers={"X-Api-Key": "wrong"})
    assert wrong.status_code == 401
    query = await offline_client.request(method, path, params={"api_key": api_key})
    assert query.status_code == 401


async def test_feedback_is_recorded_once_per_key(
    client: AsyncClient, db_session: AsyncSession, headers: dict, data_dir: Path
) -> None:
    ids = await _run(db_session, data_dir)
    body = {
        "action": "rank",
        "order": [ids["r3"], ids["r1"]],
        "hated": [ids["r2"]],
        "idempotency_key": "ui-1",
    }
    first = await client.post(f"/api/runs/{ids['run']}/feedback", headers=headers, json=body)
    again = await client.post(f"/api/runs/{ids['run']}/feedback", headers=headers, json=body)

    assert first.status_code == 201, first.text
    assert again.status_code == 200
    assert again.json() == first.json()
    result = first.json()
    assert result["event"]["action"] == "rank"
    assert result["event"]["context"] == {
        "order": ["r3.jpg", "r1.jpg"],
        "hated": ["r2.jpg"],
        "selected_candidate": "r3.jpg",
    }
    assert [row["candidate_id"] for row in result["event"]["exposed"]] == [
        "r1.jpg",
        "r2.jpg",
        "r3.jpg",
    ]
    created = result["exemplars"]["created"]
    assert [(e["polarity"], e["source_file"]) for e in created] == [
        ("positive", "r3.jpg"),
        ("negative", "r2.jpg"),
    ]
    assert result["jobs"]["deploy"] is not None and result["jobs"]["rebuild"] is not None
    assert str(data_dir) not in first.text and "/radarr" not in first.text

    listed = (await client.get(f"/api/runs/{ids['run']}/feedback", headers=headers)).json()
    assert [e["id"] for e in listed["items"]] == [result["event"]["id"]]
    detail = (await client.get(f"/api/runs/{ids['run']}", headers=headers)).json()
    assert (detail["blind"], detail["reviewed"]) == (None, True)
    assert (await client.get("/api/review", headers=headers)).json() == {"items": []}

    image = await client.get(created[0]["image_url"], headers=headers)
    assert image.status_code == 200 and image.content == b"Alpha 3"
    etag = image.headers["etag"]
    assert etag.strip('"') == created[0]["sha256"]
    cached = await client.get(created[0]["image_url"], headers={**headers, "If-None-Match": etag})
    assert cached.status_code == 304

    exemplars = (
        await client.get("/api/taste/exemplars", headers=headers, params={"polarity": "negative"})
    ).json()
    assert exemplars["total"] == 1 and exemplars["items"][0]["source_file"] == "r2.jpg"
    (data_dir / "ml" / "exemplars" / f"{created[0]['sha256']}.jpg").write_bytes(b"changed")
    assert (await client.get(created[0]["image_url"], headers=headers)).status_code == 404
    assert (await client.get("/api/taste/exemplars/999/image", headers=headers)).status_code == 404


async def test_feedback_refusals(
    client: AsyncClient, db_session: AsyncSession, headers: dict, data_dir: Path
) -> None:
    ids = await _run(db_session, data_dir)
    url = f"/api/runs/{ids['run']}/feedback"

    async def post(path: str, body: dict) -> int:
        return (await client.post(path, headers=headers, json=body)).status_code

    assert (
        await post("/api/runs/999/feedback", {"action": "approval", "idempotency_key": "a"}) == 404
    )
    assert (
        await post(url, {"action": "override", "candidate_id": 999, "idempotency_key": "b"}) == 404
    )
    for malformed in (
        {"action": "approval"},
        {"action": "approval", "idempotency_key": ""},
        {"action": "hate", "idempotency_key": "c"},
        {"action": "override", "idempotency_key": "c"},
        {"action": "rank", "idempotency_key": "c"},
        {"action": "approval", "hated": [ids["r2"]], "idempotency_key": "c"},
        {"action": "approval", "idempotency_key": "c", "path": "/etc"},
    ):
        assert await post(url, malformed) == 422, malformed

    newer = PosterRun(
        movie_id=ids["movie"], state="completed", run_folder="00000009-newer", started_at=NOW
    )
    db_session.add(newer)
    await db_session.commit()
    refused = await client.post(
        url, headers=headers, json={"action": "approval", "idempotency_key": "d"}
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        f"run {ids['run']} is not the latest completed run of Alpha; feedback goes to run "
        f"{newer.id}"
    )


async def test_revoke(
    client: AsyncClient, db_session: AsyncSession, headers: dict, data_dir: Path
) -> None:
    ids = await _run(db_session, data_dir)
    made = await client.post(
        f"/api/runs/{ids['run']}/feedback",
        headers=headers,
        json={"action": "approval", "deploy": False, "idempotency_key": "a"},
    )
    event_id = made.json()["event"]["id"]
    assert made.json()["jobs"]["deploy"] is None

    first = await client.post(f"/api/feedback/{event_id}/revoke", headers=headers)
    again = await client.post(f"/api/feedback/{event_id}/revoke", headers=headers)

    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["event"]["revokes_event_id"] == event_id
    assert [e["state"] for e in first.json()["exemplars"]["ended"]] == ["revoked"]
    assert first.json()["jobs"]["rebuild"] is not None
    revoke_id = first.json()["event"]["id"]
    listed = (await client.get(f"/api/runs/{ids['run']}/feedback", headers=headers)).json()
    assert [(e["id"], e["revoked_by"]) for e in listed["items"]] == [
        (event_id, revoke_id),
        (revoke_id, None),
    ]
    other = await client.post(
        f"/api/feedback/{event_id}/revoke", headers=headers, json={"idempotency_key": "x"}
    )
    assert other.status_code == 409 and "already revoked" in other.json()["detail"]
    assert (
        await client.post(f"/api/feedback/{revoke_id}/revoke", headers=headers)
    ).status_code == 409
    assert (await client.post("/api/feedback/999/revoke", headers=headers)).status_code == 404
    assert (await client.get("/api/runs/999/feedback", headers=headers)).status_code == 404
    pending = (await client.get("/api/review", headers=headers)).json()["items"]
    assert [p["run_id"] for p in pending] == [ids["run"]]


async def test_taste_status_and_rebuild(
    client: AsyncClient, db_session: AsyncSession, headers: dict, data_dir: Path
) -> None:
    status = await client.get("/api/taste", headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["profile"] is None and body["rebuild_due"] is False
    assert body["agreement"]["blind"] == {"reviewed": 0, "agreed": 0, "top3": 0, "percent": None}
    assert body["residual"] == {
        "subjects": 0,
        "pairs": 0,
        "min_subjects": 25,
        "min_pairs": 200,
        "trained": False,
        "scorer": "auto",
        "in_use": False,
        "why": "not trained yet",
        "active": None,
        "last_training": None,
        "missing": {"subjects": 25, "pairs": 200},
    }
    assert body["evaluation"] == {"latest": None, "previous": None, "running": None}
    first = await client.post("/api/taste/rebuild", headers=headers, json={"force": True})
    again = await client.post("/api/taste/rebuild", headers=headers)
    assert first.status_code == 202 and again.status_code == 200
    assert first.json()["kind"] == "taste_rebuild" and again.json()["id"] == first.json()["id"]
    assert first.json()["payload"]["force"] is True


async def test_an_exemplar_says_whose_it_is_and_what_ended_it(
    client: AsyncClient, db_session: AsyncSession, headers: dict, data_dir: Path
) -> None:
    ids = await _run(db_session, data_dir, title="Alpha")
    first = await client.post(
        f"/api/runs/{ids['run']}/feedback",
        headers=headers,
        json={
            "action": "override",
            "candidate_id": ids["r2"],
            "deploy": False,
            "idempotency_key": "a",
        },
    )
    second = await client.post(
        f"/api/runs/{ids['run']}/feedback",
        headers=headers,
        json={
            "action": "override",
            "candidate_id": ids["r3"],
            "deploy": False,
            "idempotency_key": "b",
        },
    )
    first_event, second_event = first.json()["event"]["id"], second.json()["event"]["id"]
    ended = second.json()["exemplars"]["ended"]
    assert [(e["source_file"], e["ended_by_event_id"]) for e in ended] == [("r2.jpg", second_event)]
    created = second.json()["exemplars"]["created"][0]
    assert (created["title"], created["year"], created["ended_by_event_id"]) == (
        "Alpha",
        2001,
        None,
    )

    items = (await client.get("/api/taste/exemplars", headers=headers)).json()["items"]
    by_file = {item["source_file"]: item for item in items}
    assert by_file["r2.jpg"]["state"] == "superseded"
    assert by_file["r2.jpg"]["ended_by_event_id"] == second_event
    assert by_file["r2.jpg"]["event_id"] == first_event
    assert by_file["r3.jpg"]["ended_by_event_id"] is None
    for item in items:
        assert (item["title"], item["year"]) == ("Alpha", 2001)
        assert datetime.fromisoformat(item["updated_at"])
    # Ended by the second event, in a later transaction than the one that made it.
    assert datetime.fromisoformat(by_file["r2.jpg"]["updated_at"]) > datetime.fromisoformat(
        by_file["r2.jpg"]["created_at"]
    )


async def test_the_review_queue_carries_its_pictures(
    client: AsyncClient, db_session: AsyncSession, headers: dict, data_dir: Path
) -> None:
    with_poster = await _run(db_session, data_dir, title="Alpha", n=1)
    without = await _run(db_session, data_dir, title="Beta", n=2)
    sha = "a" * 64
    db_session.add(
        Artwork(
            movie_id=with_poster["movie"],
            artwork_kind="poster",
            file_name="poster.jpg",
            sha256=sha,
            size_bytes=1234,
            file_modified_at=NOW,
            origin="library",
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
    )
    # Beta's pick was never downloaded: it has no picture to show.
    pick = await db_session.get(RunCandidate, without["r1"])
    assert pick is not None
    pick.sha256 = None
    await db_session.commit()

    items = (await client.get("/api/review", headers=headers)).json()["items"]
    by_title = {item["title"]: item for item in items}
    assert by_title["Alpha"]["poster"] == {
        "file_name": "poster.jpg",
        "sha256": sha,
        "size_bytes": 1234,
        "origin": "library",
        "url": f"/api/movies/{with_poster['movie']}/poster",
        "version": sha[:12],
    }
    assert by_title["Alpha"]["pick_image_url"] == (
        f"/api/runs/{with_poster['run']}/candidates/{with_poster['r1']}/image"
    )
    assert by_title["Beta"]["poster"] is None
    assert by_title["Beta"]["pick_image_url"] is None
    picture = await client.get(by_title["Alpha"]["pick_image_url"], headers=headers)
    assert picture.status_code == 200 and picture.content == b"Alpha 1"


async def test_feedback_is_listed_newest_first_with_its_movie(
    client: AsyncClient, db_session: AsyncSession, headers: dict, data_dir: Path
) -> None:
    alpha = await _run(db_session, data_dir, title="Alpha", n=1)
    beta = await _run(db_session, data_dir, title="Beta", n=2)

    async def post(ids: dict, body: dict) -> int:
        answer = await client.post(
            f"/api/runs/{ids['run']}/feedback", headers=headers, json={"deploy": False, **body}
        )
        assert answer.status_code == 201
        return answer.json()["event"]["id"]

    approved = await post(alpha, {"action": "approval", "idempotency_key": "a"})
    ranked = await post(
        beta,
        {
            "action": "rank",
            "order": [beta["r2"], beta["r1"]],
            "hated": [beta["r3"]],
            "idempotency_key": "b",
        },
    )
    revoke = (await client.post(f"/api/feedback/{approved}/revoke", headers=headers)).json()
    revoke_id = revoke["event"]["id"]

    body = (await client.get("/api/feedback", headers=headers)).json()
    assert body["total"] == 3
    assert [(e["id"], e["action"], e["title"], e["revoked_by"]) for e in body["items"]] == [
        (revoke_id, "revoke", "Alpha", None),
        (ranked, "rank", "Beta", None),
        (approved, "approval", "Alpha", revoke_id),
    ]
    rank = body["items"][1]
    assert (rank["year"], rank["run_id"], rank["shown"]) == (2001, beta["run"], 3)
    assert rank["context"]["order"] == ["r2.jpg", "r1.jpg"]
    assert rank["context"]["hated"] == ["r3.jpg"]
    assert "exposed" not in rank
    assert body["items"][0]["revokes_event_id"] == approved

    only_beta = (
        await client.get("/api/feedback", headers=headers, params={"movie_id": beta["movie"]})
    ).json()
    assert [e["id"] for e in only_beta["items"]] == [ranked]
    second_page = (
        await client.get("/api/feedback", headers=headers, params={"page": 2, "page_size": 2})
    ).json()
    assert (second_page["total"], [e["id"] for e in second_page["items"]]) == (3, [approved])


# ── Blind evaluations ─────────────────────────────────────────────────────


async def _evaluable(session: AsyncSession, n: int = 1, *, profile: bool = True) -> int:
    """A movie a run accepts, with an active seed positive, and (once) an active profile
    built from the active exemplars."""
    from marquee.db.models import Exemplar, TasteProfile
    from marquee.taste import exemplars

    now = datetime(2026, 11, 24, tzinfo=UTC)
    movie = Movie(
        radarr_id=100 + n,
        title=f"Movie {n}",
        sort_title=f"movie {n}",
        year=2000 + n,
        radarr_path=f"/radarr/Movie {n}",
        has_file=True,
        tmdb_id=500 + n,
        tmdb_enriched_at=now,
        last_seen_at=now,
    )
    session.add(movie)
    await session.flush()
    sha = hashlib.sha256(f"poster {n}".encode()).hexdigest()
    session.add(
        Exemplar(
            movie_id=movie.id,
            polarity="positive",
            weight=1.0,
            source="seed",
            state="active",
            sha256=sha,
            file_name=f"{sha}.png",
            staged_name=f"Movie {n} ({2000 + n}).png",
        )
    )
    await session.flush()
    if profile:
        from sqlalchemy import update

        await session.execute(update(TasteProfile).values(is_active=False))
        session.add(
            TasteProfile(
                sha256=hashlib.sha256(f"profile {n}".encode()).hexdigest(),
                revision=await exemplars.revision(session),
                source="seed",
                positives=n,
                negatives=0,
                positive_movie_ids=[movie.id],
                file_name=f"{hashlib.sha256(f'profile {n}'.encode()).hexdigest()}.npz",
                is_active=True,
            )
        )
    await session.commit()
    return movie.id


async def test_an_evaluation_is_queued_once_and_refused_with_reasons(
    client: AsyncClient, db_session: AsyncSession, headers: dict, data_dir: Path
) -> None:
    none = await client.post("/api/taste/evaluations", headers=headers)
    assert none.status_code == 409 and "no active taste profile" in none.json()["detail"]
    first = await _evaluable(db_session, 1)
    second = await _evaluable(db_session, 2, profile=False)  # the profile lacks its exemplar
    due = await client.post("/api/taste/evaluations", headers=headers)
    assert due.status_code == 409 and "rebuild it first" in due.json()["detail"]
    await _evaluable(db_session, 3)  # a profile of every active exemplar now

    unknown = await client.post(
        "/api/taste/evaluations", headers=headers, json={"movie_ids": [first, 999]}
    )
    assert unknown.status_code == 422 and "999" in unknown.json()["detail"]
    queued = await client.post("/api/taste/evaluations", headers=headers, json={"limit": 2})
    assert queued.status_code == 202
    body = queued.json()
    assert body["batch"]["kind"] == "batch"
    assert body["batch"]["payload"] == {"evaluation_id": body["evaluation"]["id"], "movies": 2}
    item = body["evaluation"]
    assert (item["movies"], item["queued"], item["state"], item["scorer"]) == (
        2,
        2,
        "running",
        "weighted",
    )
    assert item["progress"] == {"queued": 2}
    jobs = (
        await client.get("/api/jobs", headers=headers, params={"parent_id": body["batch"]["id"]})
    ).json()["items"]
    assert {job["kind"] for job in jobs} == {"poster_run_masked"}
    assert sorted(job["payload"]["movie_id"] for job in jobs) == [first, second]
    assert {job["payload"]["evaluation_id"] for job in jobs} == {item["id"]}

    busy = await client.post("/api/taste/evaluations", headers=headers)
    assert busy.status_code == 409 and "still running" in busy.json()["detail"]
    status = (await client.get("/api/taste", headers=headers)).json()["evaluation"]
    assert status["running"]["id"] == item["id"] and status["latest"] is None


async def test_an_evaluations_results_carry_the_images_to_show(
    client: AsyncClient, db_session: AsyncSession, headers: dict, data_dir: Path
) -> None:
    from marquee.db.models import Evaluation, EvaluationResult, Exemplar, Job

    movie_id = await _evaluable(db_session, 1)
    other_id = await _evaluable(db_session, 2)
    from sqlalchemy import select

    exemplar = await db_session.scalar(select(Exemplar).where(Exemplar.movie_id == movie_id))
    assert exemplar is not None
    job = Job(kind="batch", job_class="batch", state="succeeded", finished_at=datetime.now(UTC))
    db_session.add(job)
    await db_session.flush()
    row = Evaluation(job_id=job.id, profile_sha256="a" * 64, scorer="weighted", movies=3, queued=3)
    run = PosterRun(
        movie_id=movie_id,
        state="completed",
        run_folder="r",
        started_at=job.created_at or datetime.now(UTC),
        masked=True,
    )
    db_session.add_all([row, run])
    await db_session.flush()
    pick = RunCandidate(run_id=run.id, source_file="p.jpg", sha256="1" * 64, rank=1, is_pick=True)
    match = RunCandidate(run_id=run.id, source_file="m.jpg", sha256="2" * 64, rank=4)
    db_session.add_all([pick, match])
    db_session.add_all(
        [
            EvaluationResult(
                evaluation_id=row.id,
                movie_id=movie_id,
                exemplar_id=exemplar.id,
                exemplar_source="seed",
                run_id=run.id,
                outcome="ranked",
                matched_by="cosine",
                match_file="m.jpg",
                match_cosine=0.97,
                rank=4,
                ranked_count=16,
                card=3,
                cards=9,
                pick_file="p.jpg",
                pick_is_match=False,
                same_design=False,
            ),
            EvaluationResult(
                evaluation_id=row.id,
                movie_id=other_id,
                exemplar_source="seed",
                outcome="gated",
                match_file="g.jpg",
                stage="ocr",
                reason="text_heavy",
            ),
        ]
    )
    await db_session.commit()

    one = (await client.get(f"/api/taste/evaluations/{row.id}", headers=headers)).json()
    assert (one["state"], one["results"], one["not_run"]) == ("succeeded", 2, 1)
    seed = one["summary"]["seed"]
    assert (seed["evaluated"], seed["ranked"], seed["gated"], seed["top3"]) == (2, 1, 1, 1)
    assert seed["gated_by"] == {"ocr: text_heavy": 1}

    results = (await client.get(f"/api/taste/evaluations/{row.id}/results", headers=headers)).json()
    assert results["total"] == 2
    ranked = results["items"][0]
    assert (ranked["title"], ranked["outcome"], ranked["card"]) == ("Movie 1", "ranked", 3)
    assert ranked["exemplar_image_url"] == f"/api/taste/exemplars/{exemplar.id}/image"
    assert ranked["match_image_url"] == f"/api/runs/{run.id}/candidates/{match.id}/image"
    assert ranked["pick_image_url"] == f"/api/runs/{run.id}/candidates/{pick.id}/image"
    gated = results["items"][1]
    assert gated["run_id"] is None and gated["match_image_url"] is None
    only = (
        await client.get(
            f"/api/taste/evaluations/{row.id}/results",
            headers=headers,
            params={"outcome": "gated", "source": "seed"},
        )
    ).json()
    assert [r["movie_id"] for r in only["items"]] == [other_id]
    listing = (await client.get("/api/taste/evaluations", headers=headers)).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == row.id
    headline = (await client.get("/api/taste", headers=headers)).json()["evaluation"]
    assert headline["latest"]["id"] == row.id and headline["running"] is None
    assert (await client.get("/api/taste/evaluations/999", headers=headers)).status_code == 404
    assert (
        await client.get("/api/taste/evaluations/999/results", headers=headers)
    ).status_code == 404
    assert str(data_dir) not in json.dumps(results)
