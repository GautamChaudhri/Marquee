"""Poster pipeline jobs: confined, non-deploying analysis submission and execution.

A pipeline run analyses and ranks; it never writes artwork. Covers the path-free request
contract, the read-only result schema, the single canonical executor for single-subject
and batch runs, and the confined workspace those runs are allowed to touch."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import Queries
from pydantic import ValidationError
from sqlalchemy import select

from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.documents import PosterPipelineRequestV1, PosterPipelineResultV1
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.database import _get_engine
from marquee.main import app
from marquee.models import Job, JobArtifact, JobAttempt, JobBatch, Movie, PipelineRun


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


@pytest_asyncio.fixture(autouse=True)
async def installed_pgqueuer(db) -> Queries:
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queries = Queries.from_asyncpg_connection(raw.driver_connection)
        await queries.install()
        try:
            yield queries
        finally:
            await db.rollback()
            await queries.uninstall()


@pytest.mark.asyncio
async def test_movie_route_returns_canonical_nonmutating_submission(client, db) -> None:
    movie = Movie(
        title="Alien",
        year=1979,
        folder_path="/library/Alien",
        movie_file_path="/library/Alien/Alien.mkv",
        tmdb_id=348,
    )
    db.add(movie)
    await db.commit()
    await db.refresh(movie)

    response = await client.post(f"/api/pipeline/movie/{movie.id}/run")
    assert response.status_code == 202
    body = response.json()
    assert body == {
        "job_id": body["job_id"],
        "disposition": "created",
        "idempotent": False,
        "phase": "queued",
        "snapshot_url": f"/api/jobs/{body['job_id']}/snapshot",
        "detail_url": f"/projection-room/jobs/{body['job_id']}",
        "activity_url": f"/projection-room?view=queue&job={body['job_id']}",
        "active_conflict": None,
    }
    job = await db.get(Job, body["job_id"])
    assert job is not None
    assert job.type == "poster_pipeline"
    assert job.plan["entrypoint"] == "gpu"
    assert job.plan["effect_safety"] == "read_only"
    assert job.subject_kind == "movie"
    assert job.request == {
        "movie_id": movie.id,
        "tmdb_id": 348,
        "title": "Alien",
        "source_descriptors": [{"provider": "tmdb", "reference": "movie:348"}],
    }
    assert not any(key in body for key in ("run_id", "results_url", "pgq_job_id"))


def test_poster_result_schema_cannot_claim_artwork_mutation() -> None:
    fields = set(PosterPipelineResultV1.model_fields)
    assert (
        not {
            "deployed",
            "active_poster",
            "deployment_path",
            "reset",
            "restore",
            "heal",
        }
        & fields
    )


def test_single_pipeline_call_graph_has_one_canonical_executor() -> None:
    route_path = Path("marquee/api/routes/pipeline.py")
    tree = ast.parse(route_path.read_text())
    route = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_pipeline"
    )
    calls = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(route)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert "job_manager.create" not in calls
    assert any(
        isinstance(node, ast.Call) and getattr(node.func, "id", None) == "submit_job"
        for node in ast.walk(route)
    )

    assert not Path("marquee/core/jobs/builtin_handlers.py").exists()
    registrations = Path("marquee/core/jobs/handlers_posters.py").read_text()
    assert registrations.count('register_execution_handler("poster_pipeline"') == 1


@pytest.mark.asyncio
async def test_movie_batch_is_ticketless_parent_with_frozen_children(client, db) -> None:
    movies = [
        Movie(
            title=title,
            year=year,
            folder_path=f"/library/{title}",
            movie_file_path=f"/library/{title}/{title}.mkv",
            tmdb_id=tmdb_id,
        )
        for title, year, tmdb_id in (("Arrival", 2016, 329865), ("Heat", 1995, 949))
    ]
    db.add_all(movies)
    await db.commit()
    for movie in movies:
        await db.refresh(movie)

    response = await client.post(
        "/api/pipeline/batch",
        json={"scope": "selected", "movie_ids": [movies[1].id, movies[0].id]},
    )
    assert response.status_code == 202, response.text
    parent = await db.get(Job, response.json()["job_id"])
    assert parent is not None
    assert parent.type == "poster_pipeline_batch"
    assert parent.plan["parent_only"] is True
    assert parent.plan["effect_safety"] == "read_only"
    assert parent.request == {"scope": "selected", "selection_count": 2}
    projection = await db.get(JobBatch, parent.id)
    assert projection is not None
    assert projection.sealed_child_total == 2

    children = list(
        (
            await db.execute(
                select(Job).where(Job.parent_id == parent.id).order_by(Job.subject_reference)
            )
        )
        .scalars()
        .all()
    )
    assert [child.type for child in children] == ["poster_pipeline", "poster_pipeline"]
    assert {child.subject_kind for child in children} == {"movie"}
    assert {child.request["title"] for child in children} == {"Arrival", "Heat"}
    assert all(child.trigger_kind == "batch" for child in children)


@pytest.mark.asyncio
async def test_movie_batch_request_can_select_all_at_once(client, db) -> None:
    movies = [
        Movie(
            title=f"Movie {index}",
            folder_path=f"/library/Movie {index}",
            movie_file_path=f"/library/Movie {index}/Movie {index}.mkv",
            tmdb_id=20_000 + index,
        )
        for index in range(1, 4)
    ]
    db.add_all(movies)
    await db.commit()
    for movie in movies:
        await db.refresh(movie)

    response = await client.post(
        "/api/pipeline/batch",
        json={
            "scope": "selected",
            "movie_ids": [movie.id for movie in movies],
            "batch_mode": "all_at_once",
        },
    )

    assert response.status_code == 202, response.text
    parent = await db.get(Job, response.json()["job_id"])
    assert parent.request == {
        "scope": "selected",
        "selection_count": 3,
        "grouping_mode": "all_at_once",
        "configured_chunk_size": 8,
    }
    child = await db.scalar(select(Job).where(Job.parent_id == parent.id))
    assert child is not None
    assert child.type == "poster_pipeline_group"
    assert child.request["batch_mode"] == "all_at_once"
    assert [member["movie_id"] for member in child.request["members"]] == [
        movie.id for movie in movies
    ]


async def _movie(db, title: str, tmdb_id: int) -> Movie:
    movie = Movie(
        title=title,
        year=2000,
        folder_path=f"/library/{title}",
        movie_file_path=f"/library/{title}/{title}.mkv",
        tmdb_id=tmdb_id,
    )
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    return movie


async def _put_in_review(db, movie: Movie) -> None:
    """Give a movie an undecided completed run — the Review tab's own condition."""
    job_id = uuid4().hex
    job = Job(
        id=job_id,
        type="poster_pipeline",
        payload_version=1,
        request={},
        phase="terminal",
        outcome="succeeded",
        desired_state="run",
        fence_token=1,
        root_id=job_id,
        trigger_kind="manual",
        feature_area="posters",
        presentation_family="posters",
        subject_kind="movie",
        subject_reference=str(movie.id),
        subject_snapshot={"version": 1, "kind": "movie"},
        terminal_at=datetime.now(UTC),
    )
    db.add(job)
    await db.flush()
    attempt = JobAttempt(
        job_id=job_id,
        number=1,
        fence_token=1,
        phase="finished",
        outcome="succeeded",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    db.add(attempt)
    await db.flush()
    payload = b"{}"
    artifact = JobArtifact(
        job_id=job_id,
        attempt_id=attempt.id,
        kind="command_report",
        name="pipeline-run.json",
        status="available",
        storage_key=f"test-artifacts/{job_id}/pipeline-run.json",
        content_type="application/json",
        size_bytes=len(payload),
        checksum=sha256(payload).hexdigest(),
        artifact_metadata={"family": "poster_pipeline"},
    )
    db.add(artifact)
    await db.flush()
    db.add(
        PipelineRun(
            run_id=uuid4().hex,
            movie_id=movie.id,
            media_type="movie",
            status="completed",
            job_id=job_id,
            attempt_id=attempt.id,
            fence_token=1,
            archive_artifact_id=artifact.id,
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_missing_scope_skips_the_review_queue(client, db) -> None:
    awaiting_run = await _movie(db, "Arrival", 329865)
    await _put_in_review(db, await _movie(db, "Heat", 949))

    response = await client.post("/api/pipeline/batch", json={"scope": "missing"})
    assert response.status_code == 202, response.text
    parent = await db.get(Job, response.json()["job_id"])
    assert parent.request == {"scope": "missing", "selection_count": 1}
    children = list(
        (await db.execute(select(Job).where(Job.parent_id == parent.id))).scalars().all()
    )
    assert [child.request["movie_id"] for child in children] == [awaiting_run.id]


@pytest.mark.asyncio
async def test_all_scope_still_covers_the_review_queue(client, db) -> None:
    """ "Re-run whole library" is an explicit re-run, so review is in scope."""
    await _movie(db, "Arrival", 329865)
    await _put_in_review(db, await _movie(db, "Heat", 949))

    response = await client.post("/api/pipeline/batch", json={"scope": "all"})
    assert response.status_code == 202, response.text
    parent = await db.get(Job, response.json()["job_id"])
    assert parent.request == {"scope": "all", "selection_count": 2}


@pytest.mark.asyncio
async def test_batch_refuses_to_queue_movies_that_are_already_active(client, db) -> None:
    movie = await _movie(db, "Arrival", 329865)

    first = await client.post("/api/pipeline/batch", json={"scope": "missing"})
    assert first.status_code == 202, first.text

    second = await client.post("/api/pipeline/batch", json={"scope": "missing"})
    assert second.status_code == 409, second.text
    assert "already active" in second.json()["detail"]

    selected = await client.post(
        "/api/pipeline/batch", json={"scope": "selected", "movie_ids": [movie.id]}
    )
    assert selected.status_code == 409, selected.text


def test_batch_parents_have_no_executor_or_legacy_lifecycle_calls() -> None:
    assert not Path("marquee/core/jobs/builtin_handlers.py").exists()
    for route_path, names in (
        ("marquee/api/routes/pipeline.py", {"run_pipeline_batch"}),
        (
            "marquee/api/routes/pipeline_tv.py",
            {"run_tv_pipeline_batch", "run_series_pipeline"},
        ),
    ):
        tree = ast.parse(Path(route_path).read_text())
        routes = [
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name in names
        ]
        assert {route.name for route in routes} == names
        rendered = "\n".join(ast.unparse(route) for route in routes)
        assert "job_manager" not in rendered
        assert "create_fixed_batch" in rendered


def test_poster_request_is_path_free_and_server_policy_only() -> None:
    valid = {
        "movie_id": 1,
        "title": "Example",
        "source_descriptors": [{"provider": "tmdb", "reference": "movie:1"}],
    }
    request = PosterPipelineRequestV1.model_validate(valid)
    assert request.source_descriptors[0].reference == "movie:1"

    for invalid in (
        {**valid, "source_path": "/library/poster.jpg"},
        {**valid, "gpu": 1},
        {**valid, "artifact_key": "caller-selected"},
        {**valid, "source_descriptors": [{"provider": "tmdb", "reference": "/tmp/poster"}]},
    ):
        with pytest.raises(ValidationError):
            PosterPipelineRequestV1.model_validate(invalid)


def test_pipeline_runs_only_through_the_read_only_executor() -> None:
    definition = JOB_DEFINITION_REGISTRY.get("poster_pipeline")
    assert definition.enabled is True
    assert definition.effect_safety.value == "read_only"
    assert definition.execution_class.value == "gpu"
    assert definition.request.models[1] is PosterPipelineRequestV1
    assert "poster_pipeline" in EXECUTION_HANDLERS


def test_pipeline_handler_has_no_legacy_or_artwork_writer_bridge() -> None:
    source = Path("marquee/core/jobs/poster_pipeline.py").read_text()
    forbidden = (
        "run_manager",
        "progress_bridge",
        "job_manager",
        "media_job_manager",
        "cancel_registry",
    )
    assert all(token not in source for token in forbidden)
    # The real handler runs the pipeline inside the contained internal runner, so it
    # imports no pipeline/ML stack directly and owns no legacy run/artwork bridge.
    assert "from marquee.pipeline" not in source
    assert "artifact_registry" not in source
    assert "register_physical_artifact" in source
    assert "run_internal_operation" in source
