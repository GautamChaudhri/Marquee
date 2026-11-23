"""HTTP caching behavior of the poster-serving routes.

Library posters carry a version token derived from the deployed bytes, so a
URL presenting the current token is immutable while any other URL revalidates
via ETag. Run-candidate artifacts are immutable evidence and cache forever.
The thumbnail derivative (``?w=400``) is built once per poster version.
"""

from __future__ import annotations

import shutil
from hashlib import sha256
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.main import app
from marquee.models import Movie

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _clean_thumbs():
    shutil.rmtree(settings.poster_thumbs_path, ignore_errors=True)
    yield
    shutil.rmtree(settings.poster_thumbs_path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _media_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])


async def _seed_movie(
    db: AsyncSession,
    tmp_path: Path,
    *,
    title: str = "Cache Test Film",
    poster_sha256: str | None = None,
    width: int = 800,
) -> tuple[Movie, Path]:
    folder = tmp_path / title
    folder.mkdir(parents=True, exist_ok=True)
    poster_file = folder / "poster.jpg"
    Image.new("RGB", (width, width * 3 // 2), (20, 40, 60)).save(poster_file, format="JPEG")
    movie = Movie(
        title=title,
        year=2020,
        folder_path=str(folder),
        poster_path=str(poster_file),
        poster_user_approved=True,
        poster_sha256=poster_sha256,
    )
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    return movie, poster_file


async def test_versioned_url_is_immutable_and_carries_validators(db, client, tmp_path):
    digest = sha256(b"deployed-bytes-1").hexdigest()
    movie, poster_file = await _seed_movie(db, tmp_path, poster_sha256=digest)
    version = digest[:16]

    resp = await client.get(f"/api/library/movies/{movie.id}/poster?v={version}")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert resp.headers["etag"] == f'"{version}"'
    assert "last-modified" in resp.headers
    assert int(resp.headers["content-length"]) == poster_file.stat().st_size
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content.startswith(b"\xff\xd8\xff")


async def test_unversioned_and_stale_urls_revalidate(db, client, tmp_path):
    digest = sha256(b"deployed-bytes-2").hexdigest()
    movie, _ = await _seed_movie(db, tmp_path, poster_sha256=digest)

    bare = await client.get(f"/api/library/movies/{movie.id}/poster")
    stale = await client.get(f"/api/library/movies/{movie.id}/poster?v=oldtoken")
    assert bare.headers["cache-control"] == "public, no-cache"
    assert stale.headers["cache-control"] == "public, no-cache"
    # The validator names the current bytes either way, so revalidation 304s.
    assert bare.headers["etag"] == f'"{digest[:16]}"'


async def test_if_none_match_returns_304_with_headers(db, client, tmp_path):
    digest = sha256(b"deployed-bytes-3").hexdigest()
    movie, _ = await _seed_movie(db, tmp_path, poster_sha256=digest)
    version = digest[:16]

    resp = await client.get(
        f"/api/library/movies/{movie.id}/poster?v={version}",
        headers={"If-None-Match": f'"{version}"'},
    )
    assert resp.status_code == 304
    assert resp.content == b""
    assert resp.headers["etag"] == f'"{version}"'
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"

    mismatch = await client.get(
        f"/api/library/movies/{movie.id}/poster?v={version}",
        headers={"If-None-Match": '"different"'},
    )
    assert mismatch.status_code == 200


async def test_unhashed_poster_falls_back_to_weak_etag(db, client, tmp_path):
    """Sync-adopted posters (no sha, no deploy timestamp) still revalidate."""
    movie, _ = await _seed_movie(db, tmp_path, poster_sha256=None)

    first = await client.get(f"/api/library/movies/{movie.id}/poster")
    etag = first.headers["etag"]
    assert etag.startswith('W/"')

    again = await client.get(
        f"/api/library/movies/{movie.id}/poster", headers={"If-None-Match": etag}
    )
    assert again.status_code == 304


async def test_non_jpeg_poster_content_is_refused(db, client, tmp_path):
    movie, poster_file = await _seed_movie(db, tmp_path)
    poster_file.write_bytes(b"<html>not a poster</html>")

    resp = await client.get(f"/api/library/movies/{movie.id}/poster")
    assert resp.status_code == 404


async def test_thumbnail_is_downscaled_cached_and_versioned(db, client, tmp_path):
    digest = sha256(b"deployed-bytes-4").hexdigest()
    movie, poster_file = await _seed_movie(db, tmp_path, poster_sha256=digest, width=800)
    version = digest[:16]
    url = f"/api/library/movies/{movie.id}/poster?v={version}&w=400"

    resp = await client.get(url)
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert resp.headers["etag"] == f'"{version}-w400"'
    assert int(resp.headers["content-length"]) < poster_file.stat().st_size

    thumb_file = settings.poster_thumbs_path / "movie" / f"{movie.id}-{version}-w400.jpg"
    assert thumb_file.is_file()
    first_mtime = thumb_file.stat().st_mtime_ns

    again = await client.get(url)
    assert again.status_code == 200
    assert thumb_file.stat().st_mtime_ns == first_mtime, "second request must hit the cache"

    conditional = await client.get(url, headers={"If-None-Match": f'"{version}-w400"'})
    assert conditional.status_code == 304


async def test_thumbnail_regeneration_drops_stale_siblings(db, client, tmp_path):
    old_digest = sha256(b"deployed-bytes-old").hexdigest()
    movie, poster_file = await _seed_movie(db, tmp_path, poster_sha256=old_digest)
    old_version = old_digest[:16]
    await client.get(f"/api/library/movies/{movie.id}/poster?v={old_version}&w=400")
    old_thumb = settings.poster_thumbs_path / "movie" / f"{movie.id}-{old_version}-w400.jpg"
    assert old_thumb.is_file()

    # A redeploy: new bytes, new sha, same URL shape with a new token.
    Image.new("RGB", (800, 1200), (90, 20, 20)).save(poster_file, format="JPEG")
    new_digest = sha256(b"deployed-bytes-new").hexdigest()
    movie.poster_sha256 = new_digest
    db.add(movie)
    await db.commit()

    new_version = new_digest[:16]
    resp = await client.get(f"/api/library/movies/{movie.id}/poster?v={new_version}&w=400")
    assert resp.status_code == 200
    new_thumb = settings.poster_thumbs_path / "movie" / f"{movie.id}-{new_version}-w400.jpg"
    assert new_thumb.is_file()
    assert not old_thumb.exists(), "the previous version's derivative must be removed"


async def test_unsupported_thumbnail_width_is_rejected(db, client, tmp_path):
    movie, _ = await _seed_movie(db, tmp_path)
    resp = await client.get(f"/api/library/movies/{movie.id}/poster?w=999")
    assert resp.status_code == 422


async def test_unversioned_poster_skips_thumbnailing(db, client, tmp_path):
    """No version token → no derivative name to build; serve the original."""
    movie, poster_file = await _seed_movie(db, tmp_path, poster_sha256=None)
    resp = await client.get(f"/api/library/movies/{movie.id}/poster?w=400")
    assert resp.status_code == 200
    assert int(resp.headers["content-length"]) == poster_file.stat().st_size
    assert not (settings.poster_thumbs_path / "movie").exists()


async def _seed_run_with_candidate(
    db: AsyncSession, *, run_id: str, movie_id: int
) -> tuple[str, bytes, str, Path]:
    """Hand-build the run projection the poster route re-proves per request."""
    import json
    from datetime import UTC, datetime
    from uuid import uuid4

    from marquee.models import Job, JobArtifact, PipelineRun
    from marquee.models.job import JobAttempt

    job_id = uuid4().hex
    now = datetime.now(UTC)
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
        subject_reference=str(movie_id),
        subject_snapshot={"version": 1, "kind": "movie", "id": movie_id},
        terminal_at=now,
    )
    db.add(job)
    await db.flush()
    attempt = JobAttempt(
        job_id=job_id,
        number=1,
        fence_token=1,
        phase="finished",
        outcome="succeeded",
        started_at=now,
        finished_at=now,
    )
    db.add(attempt)
    await db.flush()
    job.current_attempt_id = attempt.id

    reference = "poster_a.jpg"
    content = b"\xff\xd8\xff" + b"candidate-bytes" * 32
    key = f"test-artifacts/{job_id}/candidate-000.jpg"
    path = Path(settings.DATA_DIR) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    checksum = sha256(content).hexdigest()
    artifact = JobArtifact(
        job_id=job_id,
        attempt_id=attempt.id,
        kind="evidence_image",
        name=reference,
        status="available",
        storage_key=key,
        content_type="image/jpeg",
        size_bytes=len(content),
        checksum=checksum,
        artifact_metadata={
            "family": "poster_pipeline",
            "role": "review_candidate",
            "candidate_reference": reference,
        },
    )
    db.add(artifact)
    await db.flush()

    archive = {
        "review": {
            "version": 1,
            "survivors": [
                {
                    "reference": reference,
                    "position": 0,
                    "objective_eligible": True,
                    "artifact_id": artifact.id,
                    "artifact_storage_key": key,
                    "artifact_checksum": checksum,
                }
            ],
        }
    }
    payload = json.dumps(archive).encode()
    archive_key = f"test-artifacts/{job_id}/pipeline-run.json"
    archive_path = Path(settings.DATA_DIR) / archive_key
    archive_path.write_bytes(payload)
    archive_artifact = JobArtifact(
        job_id=job_id,
        attempt_id=attempt.id,
        kind="command_report",
        name="pipeline-run.json",
        status="available",
        storage_key=archive_key,
        content_type="application/json",
        size_bytes=len(payload),
        checksum=sha256(payload).hexdigest(),
        artifact_metadata={"family": "poster_pipeline", "run_id": run_id},
    )
    db.add(archive_artifact)
    await db.flush()

    run = PipelineRun(
        run_id=run_id,
        movie_id=movie_id,
        media_type="movie",
        subject_snapshot={"version": 1, "kind": "movie", "id": movie_id},
        status="completed",
        scorer_name="weighted",
        job_id=job_id,
        attempt_id=attempt.id,
        fence_token=attempt.fence_token,
        selected_artifact_id=artifact.id,
        archive_artifact_id=archive_artifact.id,
        auto_pick_filename=reference,
    )
    db.add(run)
    await db.commit()
    return reference, content, checksum, path


async def test_run_candidate_is_immutable_with_checksum_etag(db, client, tmp_path):
    movie, _ = await _seed_movie(db, tmp_path)
    reference, content, checksum, _ = await _seed_run_with_candidate(
        db, run_id="cachingrun0001", movie_id=movie.id
    )

    resp = await client.get(f"/api/pipeline/runs/cachingrun0001/posters/{reference}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert resp.headers["etag"] == f'"{checksum}"'
    assert int(resp.headers["content-length"]) == len(content)
    assert resp.content == content

    conditional = await client.get(
        f"/api/pipeline/runs/cachingrun0001/posters/{reference}",
        headers={"If-None-Match": f'"{checksum}"'},
    )
    assert conditional.status_code == 304
    assert conditional.content == b""


async def test_run_candidate_size_mismatch_refuses_to_serve(db, client, tmp_path):
    movie, _ = await _seed_movie(db, tmp_path)
    reference, content, _, stored = await _seed_run_with_candidate(
        db, run_id="cachingrun0002", movie_id=movie.id
    )
    # Corrupt the stored file: identity checks pass, the size guard must not.
    stored.write_bytes(content + b"-tampered")

    resp = await client.get(f"/api/pipeline/runs/cachingrun0002/posters/{reference}")
    assert resp.status_code == 404


async def test_movie_list_poster_url_carries_version(db, client, tmp_path):
    digest = sha256(b"deployed-bytes-5").hexdigest()
    movie, _ = await _seed_movie(db, tmp_path, poster_sha256=digest)

    resp = await client.get("/api/library/movies?include_unavailable=true")
    assert resp.status_code == 200
    item = next(entry for entry in resp.json()["items"] if entry["id"] == movie.id)
    assert item["poster_url"] == f"/api/library/movies/{movie.id}/poster?v={digest[:16]}"
