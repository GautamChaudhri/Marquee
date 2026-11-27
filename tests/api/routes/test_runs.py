"""The run and deployment routes against the test database."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.db.models import Deployment, Job, Movie, PosterRun, RunCandidate

NOW = datetime(2026, 11, 24, tzinfo=UTC)
RADARR_ROOT = "/radarr/movies"

ROUTES = [
    ("POST", "/api/movies/1/run"),
    ("POST", "/api/runs/batch"),
    ("GET", "/api/runs"),
    ("GET", "/api/runs/1"),
    ("GET", "/api/runs/1/candidates/1/image"),
    ("GET", "/api/runs/1/candidates/1/image/large"),
    ("POST", "/api/runs/1/deploy"),
    ("GET", "/api/deployments"),
    ("POST", "/api/deployments/1/restore"),
]


@pytest.fixture
def headers(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key}


@pytest.fixture
def runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    settings.runs_dir.mkdir(parents=True)
    return settings.runs_dir


def _movie(radarr_id: int, title: str, **values: object) -> Movie:
    defaults: dict[str, object] = {
        "radarr_id": radarr_id,
        "title": title,
        "sort_title": title.lower(),
        "radarr_path": f"{RADARR_ROOT}/{title}",
        "has_file": True,
        "is_present": True,
        "tmdb_id": 100 + radarr_id,
        "tmdb_enriched_at": NOW,
        "last_seen_at": NOW,
    }
    return Movie(**{**defaults, **values})


async def _run(session: AsyncSession, runs_dir: Path) -> dict[str, int]:
    """A movie and a completed run: the pick (downloaded), another, and one never downloaded."""
    movie = _movie(1, "Alpha")
    session.add(movie)
    await session.flush()
    run = PosterRun(
        movie_id=movie.id,
        state="completed",
        run_folder="00000001-alpha",
        started_at=NOW,
        counts={"ranked": 2},
        engine_status="completed",
    )
    session.add(run)
    await session.flush()
    folder = runs_dir / run.run_folder
    folder.mkdir()
    (folder / "pick.jpg").write_bytes(b"\xff\xd8pick")
    (folder / "other.png").write_bytes(b"other")
    pick = RunCandidate(
        run_id=run.id,
        source_file="pick.jpg",
        sha256=hashlib.sha256(b"\xff\xd8pick").hexdigest(),
        rank=1,
        final_score=0.9,
        stack_rank=1,
        stack_label="A",
        is_pick=True,
    )
    other = RunCandidate(
        run_id=run.id,
        source_file="other.png",
        sha256=hashlib.sha256(b"other").hexdigest(),
        rank=2,
        final_score=0.5,
        stack_rank=2,
        stack_label="A",
    )
    never = RunCandidate(run_id=run.id, source_file="never.jpg", rejection_reason="resolution")
    session.add_all([pick, other, never])
    await session.commit()
    return {"movie": movie.id, "run": run.id, "pick": pick.id, "other": other.id, "never": never.id}


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_every_run_route_needs_the_key(
    offline_client: AsyncClient, method: str, path: str
) -> None:
    assert (await offline_client.request(method, path)).status_code == 401
    wrong = await offline_client.request(method, path, headers={"X-Api-Key": "wrong"})
    assert wrong.status_code == 401


async def test_a_run_is_queued_once_per_movie(
    client: AsyncClient, db_session: AsyncSession, headers: dict
) -> None:
    movie = _movie(1, "Alpha")
    wanted = _movie(2, "Bravo", has_file=False)
    db_session.add_all([movie, wanted])
    await db_session.commit()

    first = await client.post(f"/api/movies/{movie.id}/run", headers=headers)
    again = await client.post(f"/api/movies/{movie.id}/run", headers=headers)

    assert first.status_code == 202 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert first.json()["kind"] == "poster_run"
    assert first.json()["payload"] == {"movie_id": movie.id}
    refused = await client.post(f"/api/movies/{wanted.id}/run", headers=headers)
    assert refused.status_code == 409 and "has no file" in refused.json()["detail"]
    assert (await client.post("/api/movies/999/run", headers=headers)).status_code == 404


async def test_batches_by_scope_or_by_movie(
    client: AsyncClient, db_session: AsyncSession, headers: dict
) -> None:
    alpha, bravo = _movie(1, "Alpha"), _movie(2, "Bravo")
    unready = _movie(3, "Charlie", tmdb_enriched_at=None)
    db_session.add_all([alpha, bravo, unready])
    await db_session.commit()

    by_scope = await client.post(
        "/api/runs/batch", headers=headers, json={"scope": "all", "limit": 1}
    )
    assert by_scope.status_code == 202
    body = by_scope.json()
    assert (body["movies"], body["queued"], body["batch"]["kind"]) == (1, 1, "batch")

    by_ids = await client.post(
        "/api/runs/batch", headers=headers, json={"movie_ids": [alpha.id, bravo.id]}
    )
    # Alpha's run from the scope batch is still queued, so it is left out.
    assert (by_ids.json()["movies"], by_ids.json()["queued"]) == (2, 1)

    refused = await client.post(
        "/api/runs/batch", headers=headers, json={"movie_ids": [bravo.id, unready.id, 999]}
    )
    assert refused.status_code == 422 and str([unready.id, 999]) in refused.json()["detail"]
    for bad in (
        {},
        {"scope": "all", "movie_ids": [1]},
        {"movie_ids": [1], "limit": 2},
        {"scope": "x"},
    ):
        assert (await client.post("/api/runs/batch", headers=headers, json=bad)).status_code == 422


async def test_runs_list_and_detail(
    client: AsyncClient, db_session: AsyncSession, headers: dict, runs_dir: Path
) -> None:
    ids = await _run(db_session, runs_dir)

    listing = (
        await client.get("/api/runs", headers=headers, params={"movie_id": ids["movie"]})
    ).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == ids["run"]
    assert (await client.get("/api/runs", headers=headers, params={"state": "failed"})).json()[
        "total"
    ] == 0
    assert (
        await client.get("/api/runs", headers=headers, params={"state": "lost"})
    ).status_code == 422

    detail = (await client.get(f"/api/runs/{ids['run']}", headers=headers)).json()
    assert [c["source_file"] for c in detail["candidates"]] == [
        "pick.jpg",
        "other.png",
        "never.jpg",
    ]
    pick, _, never = detail["candidates"]
    assert (pick["stack"], pick["is_pick"], pick["rank"]) == ("1A", True, 1)
    assert pick["image_url"] == f"/api/runs/{ids['run']}/candidates/{ids['pick']}/image"
    assert never["image_url"] is None and never["sha256"] is None
    # This run's folder holds no archive and no large copies.
    assert all(c["score_parts"] is None for c in detail["candidates"])
    assert all(c["large_image_url"] is None for c in detail["candidates"])
    assert detail["run_folder"] == "00000001-alpha"
    assert str(runs_dir) not in str(detail) and RADARR_ROOT not in str(detail)
    assert (await client.get("/api/runs/999", headers=headers)).status_code == 404


async def test_a_candidate_image_is_served_from_its_run_folder(
    client: AsyncClient, db_session: AsyncSession, headers: dict, runs_dir: Path
) -> None:
    ids = await _run(db_session, runs_dir)
    base = f"/api/runs/{ids['run']}/candidates"

    image = await client.get(f"{base}/{ids['pick']}/image", headers=headers)
    assert image.status_code == 200 and image.content == b"\xff\xd8pick"
    assert image.headers["content-type"] == "image/jpeg"
    etag = image.headers["etag"]
    assert etag == f'"{hashlib.sha256(b"\xff\xd8pick").hexdigest()}"'
    cached = await client.get(
        f"{base}/{ids['pick']}/image", headers={**headers, "If-None-Match": etag}
    )
    assert cached.status_code == 304
    assert (await client.get(f"{base}/{ids['other']}/image", headers=headers)).headers[
        "content-type"
    ] == "image/png"

    assert (await client.get(f"{base}/{ids['never']}/image", headers=headers)).status_code == 404
    assert (
        await client.get(f"/api/runs/999/candidates/{ids['pick']}/image", headers=headers)
    ).status_code == 404
    (runs_dir / "00000001-alpha" / "pick.jpg").write_bytes(b"changed")
    assert (await client.get(f"{base}/{ids['pick']}/image", headers=headers)).status_code == 404


async def test_a_run_says_what_each_ranked_score_is_made_of(
    client: AsyncClient, db_session: AsyncSession, headers: dict, runs_dir: Path
) -> None:
    ids = await _run(db_session, runs_dir)
    rows = [
        {
            "orig_filename": "pick.jpg",
            "rank": 1,
            "contributions": {"knn_sim": 0.6, "aesthetic": 0.3},
            "normalized_features": {"knn_sim": 1.0, "aesthetic": 0.75},
        },
        {"orig_filename": "other.png", "rank": 2, "contributions": {"knn_sim": 0.5}},
        # Not ranked in the record, so its parts are not given whatever the archive says.
        {"orig_filename": "never.jpg", "rank": 3, "contributions": {"knn_sim": 0.1}},
    ]
    (runs_dir / "00000001-alpha" / "pipeline_run.json").write_text(
        json.dumps({"diagnostic_ledger": {"candidates": rows}})
    )

    detail = (await client.get(f"/api/runs/{ids['run']}", headers=headers)).json()

    pick, other, never = detail["candidates"]
    assert pick["score_parts"] == [
        {"feature": "knn_sim", "contribution": 0.6, "value": 1.0},
        {"feature": "aesthetic", "contribution": 0.3, "value": 0.75},
    ]
    assert other["score_parts"] == [{"feature": "knn_sim", "contribution": 0.5, "value": None}]
    assert never["score_parts"] is None


async def test_a_large_copy_is_served_when_the_run_kept_one(
    client: AsyncClient, db_session: AsyncSession, headers: dict, runs_dir: Path, tmp_path: Path
) -> None:
    ids = await _run(db_session, runs_dir)
    base = f"/api/runs/{ids['run']}/candidates"
    folder = runs_dir / "00000001-alpha" / "large"
    folder.mkdir()
    (folder / "pick.jpg").write_bytes(b"\xff\xd8large pick")

    detail = (await client.get(f"/api/runs/{ids['run']}", headers=headers)).json()
    pick, other, never = detail["candidates"]
    assert pick["large_image_url"] == f"{base}/{ids['pick']}/image/large"
    assert other["large_image_url"] is None and never["large_image_url"] is None

    image = await client.get(pick["large_image_url"], headers=headers)
    assert image.status_code == 200 and image.content == b"\xff\xd8large pick"
    assert image.headers["content-type"] == "image/jpeg"
    cached = await client.get(
        pick["large_image_url"], headers={**headers, "If-None-Match": image.headers["etag"]}
    )
    assert cached.status_code == 304

    assert (
        await client.get(f"{base}/{ids['other']}/image/large", headers=headers)
    ).status_code == 404
    assert (await client.get(f"{base}/999/image/large", headers=headers)).status_code == 404
    assert (
        await client.get(f"/api/runs/999/candidates/{ids['pick']}/image/large", headers=headers)
    ).status_code == 404

    # A copy that is a link out of the runs directory is neither offered nor served.
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"\xff\xd8outside")
    (folder / "pick.jpg").unlink()
    (folder / "pick.jpg").symlink_to(outside)
    detail = (await client.get(f"/api/runs/{ids['run']}", headers=headers)).json()
    assert detail["candidates"][0]["large_image_url"] is None
    assert (
        await client.get(f"{base}/{ids['pick']}/image/large", headers=headers)
    ).status_code == 404


async def test_a_candidate_image_outside_the_runs_directory_is_refused(
    client: AsyncClient, db_session: AsyncSession, headers: dict, runs_dir: Path, tmp_path: Path
) -> None:
    ids = await _run(db_session, runs_dir)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"\xff\xd8pick")
    target = runs_dir / "00000001-alpha" / "pick.jpg"
    target.unlink()
    target.symlink_to(outside)
    response = await client.get(
        f"/api/runs/{ids['run']}/candidates/{ids['pick']}/image", headers=headers
    )
    assert response.status_code == 404


async def test_deploy_queues_the_owners_write(
    client: AsyncClient, db_session: AsyncSession, headers: dict, runs_dir: Path
) -> None:
    ids = await _run(db_session, runs_dir)

    pick = await client.post(f"/api/runs/{ids['run']}/deploy", headers=headers)
    assert pick.status_code == 202
    assert pick.json()["payload"] == {
        "candidate_id": ids["pick"],
        "initiated_by": "owner",
        "movie_id": ids["movie"],
    }
    # The same request again, while it is the movie's newest write and queued: that job.
    again = await client.post(f"/api/runs/{ids['run']}/deploy", headers=headers)
    assert again.status_code == 200 and again.json()["id"] == pick.json()["id"]
    # Another candidate is a new request: queued, and it wins over the first.
    other = await client.post(
        f"/api/runs/{ids['run']}/deploy", headers=headers, json={"candidate_id": ids["other"]}
    )
    assert other.status_code == 202 and other.json()["id"] > pick.json()["id"]

    # Let the queued deploy finish, so the next request is checked rather than deduplicated.
    await db_session.execute(
        Job.__table__.update().where(Job.kind == "poster_deploy").values(state="succeeded")
    )
    await db_session.commit()
    never = await client.post(
        f"/api/runs/{ids['run']}/deploy", headers=headers, json={"candidate_id": ids["never"]}
    )
    assert never.status_code == 409 and "never downloaded" in never.json()["detail"]
    stranger = await client.post(
        f"/api/runs/{ids['run']}/deploy", headers=headers, json={"candidate_id": 999}
    )
    assert stranger.status_code == 404
    assert (await client.post("/api/runs/999/deploy", headers=headers)).status_code == 404


async def test_deployments_list_and_restore(
    client: AsyncClient, db_session: AsyncSession, headers: dict, runs_dir: Path
) -> None:
    ids = await _run(db_session, runs_dir)
    sha = "c" * 64
    first = Deployment(
        movie_id=ids["movie"],
        artwork_kind="poster",
        action="deploy",
        initiated_by="owner",
        run_id=ids["run"],
        candidate_id=ids["pick"],
        source_file="pick.jpg",
        file_name="poster.jpg",
        new_sha256=sha,
        new_size_bytes=4,
    )
    db_session.add(first)
    await db_session.flush()
    second = Deployment(
        movie_id=ids["movie"],
        artwork_kind="poster",
        action="restore",
        initiated_by="owner",
        file_name="poster.jpg",
        previous_sha256=sha,
        previous_origin="marquee",
        backup_file=f"posters/{ids['movie']}/{sha}.jpg",
        restores_deployment_id=first.id,
    )
    db_session.add(second)
    await db_session.commit()

    listing = (await client.get("/api/deployments", headers=headers)).json()
    assert [d["id"] for d in listing["items"]] == [second.id, first.id]
    assert listing["items"][0]["backup_file"] == f"posters/{ids['movie']}/{sha}.jpg"
    assert (await client.get("/api/deployments", headers=headers, params={"movie_id": 999})).json()[
        "total"
    ] == 0

    # Any deployment can be restored, not only the newest.
    old = await client.post(f"/api/deployments/{first.id}/restore", headers=headers)
    assert old.status_code == 202
    assert old.json()["kind"] == "poster_restore"
    assert old.json()["payload"] == {"deployment_id": first.id, "movie_id": ids["movie"]}
    # The same restore again is that job; a restore of another deployment is queued too.
    same = await client.post(f"/api/deployments/{first.id}/restore", headers=headers)
    assert same.status_code == 200 and same.json()["id"] == old.json()["id"]
    newest = await client.post(f"/api/deployments/{second.id}/restore", headers=headers)
    assert newest.status_code == 202
    assert newest.json()["id"] > old.json()["id"]
    assert (await client.post("/api/deployments/999/restore", headers=headers)).status_code == 404


async def test_movie_items_carry_their_poster_origin_and_latest_run(
    client: AsyncClient, db_session: AsyncSession, headers: dict, runs_dir: Path
) -> None:
    ids = await _run(db_session, runs_dir)
    body = (await client.get(f"/api/movies/{ids['movie']}", headers=headers)).json()
    assert (body["poster_origin"], body["latest_run_id"]) == (None, ids["run"])
    listing = (await client.get("/api/movies", headers=headers)).json()
    assert listing["items"][0]["latest_run_id"] == ids["run"]
    assert (await db_session.scalar(select(PosterRun.id))) == ids["run"]


async def test_a_masked_run_is_listed_when_asked_read_only_and_never_deployed_from(
    client: AsyncClient, db_session: AsyncSession, headers: dict, runs_dir: Path
) -> None:
    ids = await _run(db_session, runs_dir)
    run = await db_session.get(PosterRun, ids["run"])
    assert run is not None
    run.masked = True
    await db_session.commit()

    def listed(params: dict) -> list[int]:
        return [item["id"] for item in params["items"]]

    default = (await client.get("/api/runs", headers=headers)).json()
    assert default["total"] == 0
    masked = (await client.get("/api/runs", headers=headers, params={"masked": "true"})).json()
    assert listed(masked) == [ids["run"]] and masked["items"][0]["masked"] is True
    anything = (await client.get("/api/runs", headers=headers, params={"masked": "any"})).json()
    assert listed(anything) == [ids["run"]]
    assert (
        await client.get("/api/runs", headers=headers, params={"masked": "maybe"})
    ).status_code == 422

    detail = (await client.get(f"/api/runs/{ids['run']}", headers=headers)).json()
    assert detail["masked"] is True and detail["blind"] is True
    assert detail["residual_id"] is None
    image = await client.get(
        f"/api/runs/{ids['run']}/candidates/{ids['pick']}/image", headers=headers
    )
    assert image.status_code == 200

    deploy = await client.post(f"/api/runs/{ids['run']}/deploy", headers=headers)
    assert deploy.status_code == 409 and "masked run" in deploy.json()["detail"]
    answer = await client.post(
        f"/api/runs/{ids['run']}/feedback",
        headers=headers,
        json={"action": "approval", "idempotency_key": "masked-1", "deploy": False},
    )
    assert answer.status_code == 409 and "masked run" in answer.json()["detail"]
