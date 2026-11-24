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
from marquee.db.models import Movie, PosterRun, RunCandidate

NOW = datetime(2026, 11, 24, tzinfo=UTC)

ROUTES = [
    ("GET", "/api/review"),
    ("POST", "/api/runs/1/feedback"),
    ("GET", "/api/runs/1/feedback"),
    ("POST", "/api/feedback/1/revoke"),
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
    }
    first = await client.post("/api/taste/rebuild", headers=headers, json={"force": True})
    again = await client.post("/api/taste/rebuild", headers=headers)
    assert first.status_code == 202 and again.status_code == 200
    assert first.json()["kind"] == "taste_rebuild" and again.json()["id"] == first.json()["id"]
    assert first.json()["payload"]["force"] is True
